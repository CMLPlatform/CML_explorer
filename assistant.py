"""CML research assistant: answers questions about CML research from data/knowledge.db, with citations.

Answers cite papers by their OpenAlex id, e.g. [W4404281134]; ask() returns the answer plus the cited papers so a UI
can number them. Citations that do not match a paper in the database are dropped.

Two ways to use a language model (settings in .env, next to this file or one folder up):

  Local open-weight model (default when no ANTHROPIC_API_KEY is set): any OpenAI-compatible server
      LLM_PROVIDER=local
      LLM_BASE_URL=http://localhost:1234/v1        LM Studio (default); Ollama: http://localhost:11434/v1
      LLM_MODEL=qwen3-14b                          name as the server lists it; empty = first loaded model
      LLM_API_KEY=...                              only for hosted OpenAI-compatible services
  Mistral (EU-based API, OpenAI-compatible):
      LLM_PROVIDER=mistral, MISTRAL_API_KEY=..., LLM_MODEL=mistral-medium-latest (default)
  Hugging Face Inference Providers (OpenAI-compatible router; paid from the account's monthly credit):
      LLM_PROVIDER=hf, HF_TOKEN=... (fine-grained token with "Make calls to Inference Providers"),
      LLM_MODEL=openai/gpt-oss-120b:nebius (default; the suffix chooses the provider: Nebius, EU company, fast)
      LLM_FALLBACK_MODEL=openai/gpt-oss-120b:ovhcloud (optional; used when LLM_MODEL fails: OVHcloud, France, slower)
  Claude:
      LLM_PROVIDER=anthropic, ANTHROPIC_API_KEY=..., LLM_MODEL=claude-sonnet-5-5 (default)

Two modes (LLM_MODE):
  retrieve  the code searches (experts, papers, people and partners mentioned in the question) and the model only
            writes the answer from those results. Reliable with small models. Default for local models.
  agent     the model decides itself what to look up, with five search tools, possibly several rounds.
            Better for complex questions; needs a model that is good at tool calling. Default for Claude and Mistral.

Usage:  python3 assistant.py "What is the state of the art on critical raw materials at CML?"
        python3 assistant.py --person rene-kleijn "What does he work on?"     (answers scoped to one person)

Only titles, abstracts and metadata of published papers (OpenAlex, public) are sent to the model; with a local
model nothing leaves the computer.
"""
import json
import os
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = HERE / "data" / "knowledge.db"


def load_env():
    for path in (HERE / ".env", HERE.parent / ".env"):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k = k.strip().removeprefix("export ").strip()
                v = v.strip().strip('"').strip("'")
                if k and v and k not in os.environ:
                    os.environ[k] = v


load_env()
PROVIDER = (os.environ.get("LLM_PROVIDER") or ("anthropic" if os.environ.get("ANTHROPIC_API_KEY") else
                                              "mistral" if os.environ.get("MISTRAL_API_KEY") else "local")).lower()
DEFAULT_MODEL = {"anthropic": "claude-sonnet-5-5", "mistral": "mistral-medium-latest", "hf": "openai/gpt-oss-120b:nebius"}
DEFAULT_URL = {"mistral": "https://api.mistral.ai/v1", "hf": "https://router.huggingface.co/v1"}
KEY_NAME = {"mistral": "MISTRAL_API_KEY", "hf": "HF_TOKEN"}   # where each hosted OpenAI-compatible service keeps its key
MODEL = os.environ.get("LLM_MODEL") or os.environ.get("CML_ASSISTANT_MODEL") or DEFAULT_MODEL.get(PROVIDER, "")
FALLBACK_MODEL = os.environ.get("LLM_FALLBACK_MODEL", "")   # same service, other model or provider
BASE_URL = (os.environ.get("LLM_BASE_URL") or DEFAULT_URL.get(PROVIDER, "http://localhost:1234/v1")).rstrip("/")
MODE = (os.environ.get("LLM_MODE") or ("retrieve" if PROVIDER == "local" else "agent")).lower()
LLM_API_KEY = os.environ.get("LLM_API_KEY") or os.environ.get(KEY_NAME.get(PROVIDER, ""), "")
MAX_STEPS = 8

STOP = set("""a an and are as at be by can do does for from has have how i in into is it its me of on or our over
than that the their them there these they this to was we what when where which who whom why will with within work
he she his her hers him you your tell give show list explain describe summarise summarize overview more else also
works working about any anyone latest recent research researcher researchers state art cml institute leiden paper papers
study studies""".split())


def connect():
    if not DB.exists():
        raise SystemExit(f"{DB} not found: run build_network.py, build_dashboard.py and build_index.py first")
    db = sqlite3.connect(DB, check_same_thread=False)
    db.row_factory = sqlite3.Row
    return db


def fts_query(text):
    """Free text -> FTS5 query: the exact phrase ranks first, then all words, then any word."""
    words = [w for w in re.findall(r"[a-z0-9][a-z0-9\-]*", text.lower()) if w not in STOP and len(w) > 1]
    if not words:
        return None
    quoted = [f'"{w}"' for w in words]
    parts = []
    if len(words) > 1:
        parts.append('"' + " ".join(words) + '"')
        parts.append("(" + " AND ".join(quoted) + ")")
    parts.append("(" + " OR ".join(quoted) + ")")
    return " OR ".join(parts)


# ---------------------------------------------------------------- tools

def _cml_authors(db, pid):
    rows = db.execute("""SELECT pe.name, a.position, a.at_leiden FROM authorship a JOIN people pe ON pe.id = a.person_id
                         WHERE a.paper_id = ?""", (pid,)).fetchall()
    return [f"{r['name']} ({r['position']} author)" for r in rows]


def _person_filter(args):
    clauses, params = [], []
    if args.get("person_id"):
        clauses.append("pe.id = ?"); params.append(args["person_id"])
    if args.get("department"):
        clauses.append("pe.department = ?"); params.append(args["department"])
    if args.get("theme"):
        clauses.append("pe.cml_themes LIKE ?"); params.append(f"%{args['theme']}%")
    if args.get("position"):
        clauses.append("pe.position = ?"); params.append(args["position"])
    return clauses, params


def search_papers(db, query, from_year=None, to_year=None, department=None, theme=None, person_id=None, limit=12):
    q = fts_query(query)
    if not q:
        return {"error": "query has no searchable words"}
    pc, pp = _person_filter({"department": department, "theme": theme, "person_id": person_id})
    where, params = ["papers_fts MATCH ?"], [q]
    if from_year:
        where.append("p.year >= ?"); params.append(int(from_year))
    if to_year:
        where.append("p.year <= ?"); params.append(int(to_year))
    if pc:
        where.append(f"p.id IN (SELECT a.paper_id FROM authorship a JOIN people pe ON pe.id = a.person_id WHERE {' AND '.join(pc)})")
        params += pp
    rows = db.execute(f"""SELECT p.*, bm25(papers_fts, 0, 10.0, 3.0, 5.0, 2.0) AS score FROM papers_fts f
                          JOIN papers p ON p.id = f.id WHERE {' AND '.join(where)} ORDER BY score LIMIT ?""",
                      params + [min(int(limit), 30)]).fetchall()
    return {"query": query, "results": [{
        "id": r["id"], "title": r["title"], "year": r["year"], "venue": r["venue"], "cited_by": r["cited_by"],
        "cml_authors": _cml_authors(db, r["id"]), "all_authors": r["authors"],
        "abstract": (r["abstract"][:700] + "…") if len(r["abstract"]) > 700 else (r["abstract"] or "(no abstract)"),
        "keywords": r["keywords"]} for r in rows]}


def find_experts(db, query, department=None, position=None, theme=None, limit=10):
    q = fts_query(query)
    if not q:
        return {"error": "query has no searchable words"}
    hits = db.execute("""SELECT p.id, p.title, p.year, -bm25(papers_fts, 0, 10.0, 3.0, 5.0, 2.0) AS s FROM papers_fts f
                         JOIN papers p ON p.id = f.id WHERE papers_fts MATCH ? ORDER BY bm25(papers_fts, 0, 10.0, 3.0, 5.0, 2.0)
                         LIMIT 300""", (q,)).fetchall()
    pc, pp = _person_filter({"department": department, "theme": theme, "position": position})
    people = {}
    for h in hits:
        recency = 1.0 + max(0, (h["year"] or 2010) - 2015) * 0.05
        for a in db.execute(f"""SELECT pe.*, a.position AS apos FROM authorship a JOIN people pe ON pe.id = a.person_id
                                WHERE a.paper_id = ? {('AND ' + ' AND '.join(pc)) if pc else ''}""", [h["id"]] + pp):
            role = 1.3 if a["apos"] in ("first", "last") else 1.0
            e = people.setdefault(a["id"], {"row": a, "score": 0.0, "papers": []})
            e["score"] += h["s"] * recency * role
            e["papers"].append((h["year"], h["title"], h["id"]))
    for r in db.execute(f"""SELECT pe.*, -bm25(people_fts, 0, 2.0, 5.0, 3.0, 1.0) AS s FROM people_fts f JOIN people pe ON pe.id = f.id
                            WHERE people_fts MATCH ? {('AND ' + ' AND '.join(pc)) if pc else ''} LIMIT 50""", [q] + pp):
        e = people.setdefault(r["id"], {"row": r, "score": 0.0, "papers": []})
        e["score"] += r["s"] * 3   # profile keywords / bio mention the topic
        e["profile_match"] = True
    ranked = sorted(people.values(), key=lambda e: -e["score"])[:min(int(limit), 25)]
    return {"query": query, "experts": [{
        "person_id": e["row"]["id"], "name": e["row"]["name"], "position": e["row"]["rank"],
        "department": e["row"]["department"], "cml_themes": e["row"]["cml_themes"],
        "matching_papers": len(e["papers"]), "profile_mentions_topic": bool(e.get("profile_match")),
        "latest_matching_year": max((y or 0 for y, _, _ in e["papers"]), default=None),
        "top_papers": [{"id": i, "year": y, "title": t} for y, t, i in e["papers"][:4]],   # most relevant first
    } for e in ranked]}


def find_person(db, name_or_id):
    """A person row, a list of candidates when ambiguous, or None. Matches whole name parts ("Li" is not "Caitlin")."""
    r = db.execute("SELECT * FROM people WHERE id = ?", (name_or_id,)).fetchone()
    if r:
        return r
    fold = lambda x: unicodedata.normalize("NFKD", x).encode("ascii", "ignore").decode().lower()
    q = [t for t in re.findall(r"\w+", fold(name_or_id)) if t]
    if not q:
        return None
    rows = db.execute("SELECT * FROM people").fetchall()
    parts = lambda r: re.findall(r"\w+", fold(r["name"]))
    exact = [r for r in rows if fold(r["name"]) == fold(name_or_id).strip()]
    full = [r for r in rows if all(t in parts(r) for t in q)]
    loose = [r for r in rows if all(any(p.startswith(t) for p in parts(r)) for t in q)]
    for cand in (exact, full, loose):
        if len(cand) == 1:
            return cand[0]
        if cand:
            return cand
    return None


def get_person(db, name):
    r = find_person(db, name)
    if r is None:
        return {"error": f"no CML researcher matches '{name}'"}
    if isinstance(r, list):
        return {"ambiguous": [{"person_id": x["id"], "name": x["name"], "position": x["rank"]} for x in r[:10]]}
    pid = r["id"]
    co = db.execute("""SELECT pe.name, count(*) n FROM authorship a JOIN authorship b ON a.paper_id = b.paper_id AND b.person_id != a.person_id
                       JOIN people pe ON pe.id = b.person_id WHERE a.person_id = ? AND a.at_leiden = 1 AND b.at_leiden = 1
                       GROUP BY pe.id ORDER BY n DESC LIMIT 8""", (pid,)).fetchall()
    partners = db.execute("""SELECT pa.institution, pa.country, count(DISTINCT pa.paper_id) n FROM partners pa JOIN authorship a ON a.paper_id = pa.paper_id
                             WHERE a.person_id = ? GROUP BY pa.institution ORDER BY n DESC LIMIT 8""", (pid,)).fetchall()
    papers = db.execute("""SELECT p.id, p.year, p.title, p.venue, p.cited_by, a.position FROM authorship a JOIN papers p ON p.id = a.paper_id
                           WHERE a.person_id = ? ORDER BY p.year DESC, p.cited_by DESC LIMIT 10""", (pid,)).fetchall()
    cited = db.execute("""SELECT p.id, p.year, p.title, p.cited_by FROM authorship a JOIN papers p ON p.id = a.paper_id
                          WHERE a.person_id = ? ORDER BY p.cited_by DESC LIMIT 5""", (pid,)).fetchall()
    return {"person_id": pid, "name": r["name"], "position": r["rank"], "department": r["department"],
            "cml_themes": r["cml_themes"], "topic_tags": r["tags"], "profile_keywords": r["keywords"],
            "bio": (r["summary"] or "")[:1500], "profile_url": r["profile_url"],
            "papers_counted": r["n_papers"], "papers_written_at_leiden": r["n_cml_papers"],
            "frequent_cml_coauthors": [f"{c['name']} ({c['n']})" for c in co],
            "frequent_external_partners": [f"{p['institution']}, {p['country']} ({p['n']})" for p in partners],
            "recent_papers": [dict(p) for p in papers], "most_cited_papers": [dict(p) for p in cited]}


def get_papers(db, ids):
    out = []
    for i in ids[:10]:
        r = db.execute("SELECT * FROM papers WHERE id = ?", (i,)).fetchone()
        if r:
            d = dict(r)
            d["cml_authors"] = _cml_authors(db, i)
            out.append(d)
    return {"papers": out}


def collaborations(db, country=None, institution=None, person_id=None, department=None, theme=None, limit=15):
    pc, pp = _person_filter({"person_id": person_id, "department": department, "theme": theme})
    where, params = [], []
    if country:
        where.append("pa.country = ?"); params.append(country.upper())
    if institution:
        where.append("lower(pa.institution) LIKE ?"); params.append(f"%{institution.lower()}%")
    if pc:
        where.append(f"pa.paper_id IN (SELECT a.paper_id FROM authorship a JOIN people pe ON pe.id = a.person_id WHERE {' AND '.join(pc)})")
        params += pp
    w = ("WHERE " + " AND ".join(where)) if where else ""
    inst = db.execute(f"""SELECT pa.institution, pa.country, count(DISTINCT pa.paper_id) n FROM partners pa {w}
                          GROUP BY pa.institution ORDER BY n DESC LIMIT ?""", params + [min(int(limit), 40)]).fetchall()
    ctry = db.execute(f"""SELECT pa.country, count(DISTINCT pa.paper_id) n FROM partners pa {w}
                          GROUP BY pa.country ORDER BY n DESC LIMIT 15""", params).fetchall()
    who = db.execute(f"""SELECT pe.name, count(DISTINCT pa.paper_id) n FROM partners pa JOIN authorship a ON a.paper_id = pa.paper_id
                         JOIN people pe ON pe.id = a.person_id {w} GROUP BY pe.id ORDER BY n DESC LIMIT 12""", params).fetchall()
    return {"institutions": [f"{r['institution']} ({r['country']}): {r['n']} joint papers" for r in inst],
            "countries": [f"{r['country']}: {r['n']}" for r in ctry],
            "cml_people": [f"{r['name']}: {r['n']} joint papers" for r in who],
            "note": "joint papers written at Leiden since 2010; papers with more than 50 authors excluded"}


TOOLS = [
    {"name": "search_papers",
     "description": "Full-text search (title, abstract, keywords) over papers by current CML researchers since 2010. "
                    "Returns papers with year, venue, CML authors and abstract. Search several times with different "
                    "wordings or narrower terms when the first results are thin.",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "topic words, e.g. 'critical raw materials recycling'"},
         "from_year": {"type": "integer"}, "to_year": {"type": "integer"},
         "department": {"type": "string", "enum": ["Industrial ecology", "Environmental biology"]},
         "theme": {"type": "string", "enum": ["Biodiversity", "Future food", "Resources", "Urban", "Water"]},
         "person_id": {"type": "string", "description": "limit to one person's papers"},
         "limit": {"type": "integer", "description": "max results, default 12, max 30"}},
         "required": ["query"]}},
    {"name": "find_experts",
     "description": "Rank CML researchers by how much and how recently they publish on a topic (and whether their "
                    "profile mentions it). Use for 'who works on / who leads / who should I talk to'.",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string"},
         "department": {"type": "string", "enum": ["Industrial ecology", "Environmental biology"]},
         "position": {"type": "string", "enum": ["Professors", "Associate & assistant professors", "Postdocs & researchers", "PhD candidates"]},
         "theme": {"type": "string", "enum": ["Biodiversity", "Future food", "Resources", "Urban", "Water"]},
         "limit": {"type": "integer"}}, "required": ["query"]}},
    {"name": "get_person",
     "description": "Profile of one CML researcher: position, department, themes, keywords, bio, CML co-authors, "
                    "external partners, recent and most-cited papers. Accepts a name or person_id.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "get_papers",
     "description": "Full records (complete abstract, all authors, DOI) for up to 10 paper ids.",
     "input_schema": {"type": "object", "properties": {"ids": {"type": "array", "items": {"type": "string"}}}, "required": ["ids"]}},
    {"name": "collaborations",
     "description": "External partner institutions and countries of CML, optionally for one country (ISO code, e.g. 'DE'), "
                    "an institution name, a person, a department or a theme. Also lists which CML people are involved.",
     "input_schema": {"type": "object", "properties": {
         "country": {"type": "string"}, "institution": {"type": "string"}, "person_id": {"type": "string"},
         "department": {"type": "string", "enum": ["Industrial ecology", "Environmental biology"]},
         "theme": {"type": "string", "enum": ["Biodiversity", "Future food", "Resources", "Urban", "Water"]},
         "limit": {"type": "integer"}}}},
]
TOOL_FUNCS = {"search_papers": search_papers, "find_experts": find_experts, "get_person": get_person,
              "get_papers": get_papers, "collaborations": collaborations}

SYSTEM = """You are the research assistant of the Institute of Environmental Sciences (CML), Leiden University. You help
colleagues find out what CML works on, who works on it, and with whom, using only the institute's publication database
(papers by current CML staff since 2010: titles, abstracts, keywords, authors; from OpenAlex) and staff profiles.

How to work:
- Always look things up with the tools before answering; search more than once when a question has several parts or
  the first results are thin. Do not answer from general knowledge as if it were CML's work.
- Cite every claim about a paper with its id in square brackets, e.g. [W4404281134]. Only cite ids returned by the tools.
- Name the CML researchers behind the work and their role (first/last author, position, department). Distinguish who
  leads a line of work (senior, last author, many papers) from who contributes.
- "State of the art" means the state of CML's own work unless the user asks about the wider field; if they do, say
  clearly which parts come from CML papers and which from your general knowledge.
- Be clear about limits: you see abstracts, not full texts; recent papers may be missing; people who joined recently or
  have no ORCID may be under-represented.
- Answer in the language of the user's last question (usually English). Be concise: a short overview, then the specifics. Use short paragraphs or a few bullets."""


def _person_note(db, person_id, agent):
    r = db.execute("SELECT name, rank, department FROM people WHERE id = ?", (person_id,)).fetchone() if person_id else None
    if not r:
        return ""
    how = (f"Their person_id is '{person_id}': start with get_person and restrict searches to their papers unless asked "
           "otherwise. ") if agent else ""
    return (f"\n\nThis conversation is about the work of {r['name']} ({r['rank']}, {r['department']}). {how}"
            "Describe their work in the third person; never speak as them or attribute opinions to them that are not "
            "in their papers.")


def _run_tool(db, name, args, person_id, steps, on_step):
    if person_id and name in ("search_papers", "collaborations") and "person_id" not in args:
        args["person_id"] = person_id
    try:
        out = TOOL_FUNCS[name](db, **args)
    except Exception as e:   # a bad argument should not end the conversation
        out = {"error": f"{type(e).__name__}: {e}"}
    steps.append({"tool": name, "input": args})
    if on_step:
        on_step(steps[-1])
    return json.dumps(out, ensure_ascii=False, default=str)


# ---------------------------------------------------------------- OpenAI-compatible servers (LM Studio, Ollama, vLLM, ...)

def _http(method, path, body=None, timeout=600):
    import urllib.error
    import urllib.request
    from urllib.parse import urlparse
    url = BASE_URL + path
    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=headers, method=method)
    host = urlparse(url).hostname or ""
    opener = (urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never send local traffic through a proxy
              if host in ("localhost", "127.0.0.1", "::1") else urllib.request.build_opener())
    try:
        with opener.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:   # keep the service's own explanation, e.g. a missing token permission
        detail = e.read().decode("utf-8", "replace")[:300].strip()
        raise urllib.error.HTTPError(e.url, e.code, f"{e.reason} ({detail})" if detail else e.reason, e.headers, None) from None


_local_model = None


def local_model():
    """LLM_MODEL, or the first model the local server has loaded."""
    global _local_model
    if MODEL:
        return MODEL
    if _local_model is None:
        models = [m["id"] for m in _http("GET", "/models", timeout=5).get("data", [])]
        if not models:
            raise RuntimeError(f"no model loaded on {BASE_URL}: load one in LM Studio / Ollama or set LLM_MODEL")
        _local_model = models[0]
    return _local_model


def _openai_chat(messages, tools=None, max_tokens=1800):
    body = {"model": local_model(), "messages": messages, "temperature": 0.2, "max_tokens": max_tokens}
    if tools:
        body["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                           "parameters": t["input_schema"]}} for t in tools]
    if not FALLBACK_MODEL:
        return _http("POST", "/chat/completions", body)["choices"][0]["message"]
    import urllib.error
    try:   # a shorter wait, so that the fallback still has time to answer
        return _http("POST", "/chat/completions", body, timeout=90)["choices"][0]["message"]
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, KeyError) as e:
        if isinstance(e, urllib.error.HTTPError) and e.code not in (429, 500, 502, 503, 504):
            raise   # a problem with the key or the request: the fallback does not help
        sys.stderr.write(f"{MODEL} failed ({type(e).__name__}: {e}); trying {FALLBACK_MODEL}\n")
        return _http("POST", "/chat/completions", {**body, "model": FALLBACK_MODEL})["choices"][0]["message"]


def _text(content):
    """Message content as plain text. Some APIs (Mistral, reasoning models) return a list of parts such as
    [{"type": "thinking", ...}, {"type": "text", "text": "..."}]; keep only the text parts."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, str):
                out.append(part)
            elif isinstance(part, dict) and part.get("type", "text") == "text":
                out.append(part.get("text") or "")
        return "".join(out)
    return str(content)


def _strip_thinking(text):
    """Reasoning models (Qwen3, DeepSeek-R1, ...) may put their reasoning in <think> tags; keep only the answer."""
    return re.sub(r"<think>.*?</think>", "", _text(text), flags=re.S).strip()


# ---------------------------------------------------------------- agent mode

def _agent_anthropic(db, messages, person_id, client, on_step):
    import anthropic
    client = client or anthropic.Anthropic()
    system = SYSTEM + _person_note(db, person_id, True)
    convo = [{"role": m["role"], "content": m["content"]} for m in messages]
    steps = []
    for _ in range(MAX_STEPS):
        resp = client.messages.create(model=MODEL, max_tokens=2500, system=system, tools=TOOLS, messages=convo)
        convo.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            break
        results = [{"type": "tool_result", "tool_use_id": b.id, "content": _run_tool(db, b.name, dict(b.input), person_id, steps, on_step)}
                   for b in resp.content if b.type == "tool_use"]
        convo.append({"role": "user", "content": results})
    last = convo[-1]
    answer = "".join(b.text for b in last["content"] if getattr(b, "type", "") == "text") if last["role"] == "assistant" else ""
    return answer, steps


def _tool_args(raw):
    """Tool-call arguments as a dict, or None if they cannot be read. Some providers (e.g. Nebius with gpt-oss)
    drop the closing brace, so try to repair a cut-off JSON object before giving up."""
    if isinstance(raw, dict):
        return raw
    raw = (raw or "").strip() or "{}"
    for tail in ("", "}", '"}', "]}", '"]}'):
        try:
            args = json.loads(raw.rstrip(",") + tail)
            return args if isinstance(args, dict) else None
        except json.JSONDecodeError:
            continue
    return None


def _agent_openai(db, messages, person_id, on_step):
    convo = [{"role": "system", "content": SYSTEM + _person_note(db, person_id, True)}]
    convo += [{"role": m["role"], "content": m["content"]} for m in messages]
    steps, answer = [], ""
    for _ in range(MAX_STEPS):
        msg = _openai_chat(convo, tools=TOOLS)
        calls = msg.get("tool_calls") or []
        convo.append({"role": "assistant", "content": _text(msg.get("content")), **({"tool_calls": calls} if calls else {})})
        if not calls:
            answer = msg.get("content") or ""
            break
        for c in calls:
            args = _tool_args(c["function"].get("arguments"))
            content = (json.dumps({"error": "the arguments are not valid JSON: send them again as one JSON object"}) if args is None
                       else _run_tool(db, c["function"]["name"], args, person_id, steps, on_step)
                       if c["function"]["name"] in TOOL_FUNCS else json.dumps({"error": "unknown tool"}))
            convo.append({"role": "tool", "tool_call_id": c.get("id", ""), "name": c["function"]["name"], "content": content})
    return _strip_thinking(answer), steps


# ---------------------------------------------------------------- retrieve mode

SYSTEM_RETRIEVE = """You are the research assistant of the Institute of Environmental Sciences (CML), Leiden University.
Answer the question using ONLY the search results given with it: papers by current CML staff since 2010 (titles,
abstracts, CML authors), ranked CML experts, people profiles and collaboration counts.

Rules:
- Cite every claim about a paper with its id in square brackets exactly as given, e.g. [W4404281134]. One id per bracket.
  Never invent ids, papers, people or numbers.
- Name the CML researchers behind the work. Say who leads a line of work (many papers, last or first author, senior
  position) and who contributes.
- If the results do not answer the question, say so plainly and suggest a narrower or different question.
- You only see abstracts. Do not present general knowledge as CML's work.
- Be concise: a two-sentence overview, then the specifics in a few short bullets. Answer in the language of the user's last question (usually English)."""

_COUNTRIES = None


def _countries():
    global _COUNTRIES
    if _COUNTRIES is None:
        path = HERE / "world_paths.json"
        names = json.loads(path.read_text(encoding="utf-8")).get("names", {}) if path.exists() else {}
        _COUNTRIES = {n.lower(): cc for cc, n in names.items() if len(n) > 3}
        _COUNTRIES.update({"usa": "US", "united states": "US", "uk": "GB", "united kingdom": "GB", "england": "GB",
                           "china": "CN", "the netherlands": "NL", "holland": "NL", "germany": "DE", "brazil": "BR"})
    return _COUNTRIES


def _mentioned_people(db, text):
    fold = lambda x: unicodedata.normalize("NFKD", x).encode("ascii", "ignore").decode().lower()
    t = " " + re.sub(r"[^a-z ]", " ", fold(text)) + " "
    rows = db.execute("SELECT id, name FROM people").fetchall()
    last = {}
    for r in rows:
        last.setdefault(fold(r["name"]).split()[-1], []).append(r["id"])
    found = [r["id"] for r in rows if f" {fold(r['name'])} " in t]
    for ln, ids in last.items():
        if len(ln) >= 4 and len(ids) == 1 and f" {ln} " in t and ids[0] not in found:
            found.append(ids[0])
    return found[:3]


def _brief(tool, out, abstract_chars=450):
    """Search results as compact text: small local models often run with a 4-8k token context."""
    if out.get("error"):
        return f"## {tool}: {out['error']}"
    if tool == "search_papers":
        lines = [f"## Papers matching \"{out['query']}\""]
        for r in out["results"]:
            ab = r["abstract"][:abstract_chars] + ("…" if len(r["abstract"]) > abstract_chars else "")
            lines.append(f"[{r['id']}] ({r['year']}) {r['title']}. {r['venue'] or ''}\n  CML authors: {'; '.join(r['cml_authors'])}\n  Abstract: {ab}")
        return "\n".join(lines if out["results"] else lines + ["(no matching papers)"])
    if tool == "find_experts":
        lines = [f"## CML researchers ranked for \"{out['query']}\""]
        for e in out["experts"]:
            ex = "; ".join(f"[{p['id']}] {p['title']} ({p['year']})" for p in e["top_papers"][:2])
            lines.append(f"- {e['name']} ({e['position']}, {e['department']}; themes: {e['cml_themes']}): {e['matching_papers']} matching papers, "
                         f"latest {e['latest_matching_year']}{'; profile mentions the topic' if e['profile_mentions_topic'] else ''}. e.g. {ex}")
        return "\n".join(lines)
    if tool == "get_person":
        if "ambiguous" in out:
            return "## Several people match: " + ", ".join(f"{a['name']} ({a['position']})" for a in out["ambiguous"])
        rec = "; ".join(f"[{p['id']}] {p['title']} ({p['year']})" for p in out["recent_papers"][:6])
        cit = "; ".join(f"[{p['id']}] {p['title']} ({p['year']}, {p['cited_by']} citations)" for p in out["most_cited_papers"][:3])
        return (f"## Profile: {out['name']}, {out['position']}, {out['department']}\n"
                f"Themes: {out['cml_themes']}. Topics: {out['topic_tags']}. Keywords: {out['profile_keywords']}\n"
                f"Bio: {out['bio'][:700]}\nPapers: {out['papers_counted']} ({out['papers_written_at_leiden']} at Leiden). "
                f"CML co-authors: {', '.join(out['frequent_cml_coauthors'][:6])}. External partners: {', '.join(out['frequent_external_partners'][:5])}\n"
                f"Recent papers: {rec}\nMost cited: {cit}")
    if tool == "collaborations":
        return ("## Collaborations (" + out["note"] + ")\nInstitutions: " + "; ".join(out["institutions"][:10])
                + "\nCountries: " + "; ".join(out["countries"][:10]) + "\nCML people involved: " + "; ".join(out["cml_people"][:8]))
    return f"## {tool}\n" + json.dumps(out, ensure_ascii=False, default=str)[:3000]


def gather(db, messages, person_id=None, on_step=None):
    """The fixed set of searches for retrieve mode. Returns (context text, steps)."""
    users = [m["content"] for m in messages if m["role"] == "user"]
    question = users[-1]
    # short follow-ups ("and in Germany?") are searched together with the previous question
    query = question if len(question.split()) > 5 or len(users) < 2 else users[-2] + " " + question
    steps, parts = [], []

    def run(tool, **args):
        out = json.loads(_run_tool(db, tool, args, person_id if tool != "get_person" else None, steps, on_step))
        parts.append(_brief(tool, out))

    for pid in ([person_id] if person_id else []) + _mentioned_people(db, question):
        run("get_person", name=pid)
    low = " " + question.lower() + " "
    ccs = [cc for name, cc in _countries().items() if f" {name} " in low or f" {name}?" in low or f" {name}," in low]
    for cc in list(dict.fromkeys(ccs))[:2]:
        run("collaborations", country=cc)
    if not ccs and re.search(r"partner|collaborat|work(s|ing)? with|institution|abroad|international", low):
        run("collaborations")
    topical = fts_query(query) is not None
    if topical and not person_id:
        run("find_experts", query=query, limit=8)
    if topical:
        run("search_papers", query=query, limit=10)
    return "\n\n".join(parts), steps


def _retrieve(db, messages, person_id, client, on_step):
    context, steps = gather(db, messages, person_id, on_step)
    system = SYSTEM_RETRIEVE + _person_note(db, person_id, False)
    history = [{"role": m["role"], "content": m["content"]} for m in messages[:-1]][-4:]
    final = {"role": "user", "content": f"Search results:\n\n{context}\n\nQuestion: {messages[-1]['content']}"}
    if PROVIDER == "anthropic":
        import anthropic
        client = client or anthropic.Anthropic()
        resp = client.messages.create(model=MODEL, max_tokens=1800, system=system, messages=history + [final])
        answer = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    else:
        answer = _strip_thinking(_openai_chat([{"role": "system", "content": system}] + history + [final]).get("content"))
    return answer, steps


# ---------------------------------------------------------------- entry points

def _clean_citations(answer):
    """Normalise the citation styles models produce: [W1, W2] / (W1) / [W1; W2] -> [W1][W2]."""
    answer = re.sub(r"\[((?:W\d+)(?:\s*[,;]\s*W\d+)+)\]", lambda m: "".join(f"[{w}]" for w in re.findall(r"W\d+", m.group(1))), answer)
    return re.sub(r"\((W\d+)\)", r"[\1]", answer)


def ask(messages, person_id=None, db=None, client=None, on_step=None):
    """Answer the last user message. messages: [{"role": "user"|"assistant", "content": str}, ...].
    Returns {"answer", "references": [paper records cited], "steps": [searches made], "model", "mode"}."""
    db = db or connect()
    if MODE == "retrieve":
        answer, steps = _retrieve(db, messages, person_id, client, on_step)
    elif PROVIDER == "anthropic":
        answer, steps = _agent_anthropic(db, messages, person_id, client, on_step)
    else:
        answer, steps = _agent_openai(db, messages, person_id, on_step)
    answer = _clean_citations(answer or "") or "I could not finish the search. Try a narrower question."
    refs = []
    for wid in dict.fromkeys(re.findall(r"\[(W\d+)\]", answer)):
        r = db.execute("SELECT id, title, year, venue, doi, authors FROM papers WHERE id = ?", (wid,)).fetchone()
        if r:
            refs.append(dict(r))
        else:   # an id the model made up: drop the citation rather than show a broken reference
            answer = answer.replace(f" [{wid}]", "").replace(f"[{wid}]", "")
    model = MODEL or (_local_model or "local model")
    return {"answer": answer, "references": refs, "steps": steps, "model": model, "mode": MODE, "provider": PROVIDER}


def status():
    """What the assistant will use, and whether it can run (for serve.py's health check)."""
    info = {"provider": PROVIDER, "mode": MODE, "model": MODEL, "db": DB.exists(), "ready": True, "problem": ""}
    if PROVIDER == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            info.update(ready=False, problem="no ANTHROPIC_API_KEY in .env")
    elif PROVIDER in KEY_NAME:
        info["base_url"] = BASE_URL
        if not LLM_API_KEY:
            info.update(ready=False, problem=f"no {KEY_NAME[PROVIDER]} in .env")
    else:
        info["base_url"] = BASE_URL
        try:
            info["model"] = local_model()
        except Exception as e:
            info.update(ready=False, problem=f"local model server at {BASE_URL} not reachable or no model loaded ({type(e).__name__})")
    if not info["db"]:
        info.update(ready=False, problem="data/knowledge.db missing: run build_index.py")
    return info


def main():
    args = sys.argv[1:]
    person = None
    if args[:1] == ["--person"]:
        person, args = args[1], args[2:]
    if not args:
        raise SystemExit(__doc__)
    st = status()
    print(f"[{st['provider']} · {st['model'] or '?'} · {st['mode']} mode]" + (f"  problem: {st['problem']}" if st["problem"] else ""), file=sys.stderr)
    out = ask([{"role": "user", "content": " ".join(args)}], person_id=person,
              on_step=lambda s: print(f"  … {s['tool']}({', '.join(f'{k}={v!r}' for k, v in s['input'].items())})", file=sys.stderr))
    text = out["answer"]
    for n, r in enumerate(out["references"], 1):
        text = text.replace(f"[{r['id']}]", f"[{n}]")
    print("\n" + text + "\n")
    for n, r in enumerate(out["references"], 1):
        print(f"[{n}] {r['authors'].split(',')[0]} et al. ({r['year']}). {r['title']}. {r['venue'] or ''} {r['doi'] or ''}")


if __name__ == "__main__":
    main()
