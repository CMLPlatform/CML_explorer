"""Build dashboard.html: people, themes, publications, co-authorship and external partners in one page.

Inputs
  leiden_staff.json          all staff profiles (scrape_staff.py); falls back to leiden_professors_classified.json
  data/network.json          publications, co-authors and partners (harvest_openalex.py + build_network.py), optional
  data/pub_texts.json        paper titles + keywords per person, used by harmonize.py as extra theme evidence, optional
  world_paths.json           pre-projected country shapes for the partner map
Outputs
  dashboard.html, tag_report.md, cml-network-site/index.html (published copy)

Usage: python3 build_dashboard.py [--strict]    (--strict: stop when a profile keyword has no theme mapping)
"""
import difflib
import json
import os
import re
import sys
from collections import Counter
from itertools import combinations
from pathlib import Path

import harmonize

HERE = Path(__file__).parent
STAFF = HERE / "leiden_staff.json"
LEGACY = HERE / "leiden_professors_classified.json"
NETWORK = HERE / "data" / "network.json"
PUB_TEXTS = HERE / "data" / "pub_texts.json"
WORLD = HERE / "world_paths.json"
TEMPLATE = HERE / "dashboard_template.html"
OUT = HERE / "dashboard.html"
REPORT = HERE / "tag_report.md"
SITE_COPY = HERE / "cml-network-site" / "index.html"  # published copy, kept in sync here
PAGES_COPY = HERE / "index.html"  # GitHub Pages entry point of this repo
# Online research assistant (Hugging Face Space, see deploy_space.py). The Ask view on GitHub Pages calls it.
# Empty = no online assistant. The environment variable ASSISTANT_URL overrides it.
ASSISTANT_URL = os.environ.get("ASSISTANT_URL", "https://fdonati-cml-assistant.hf.space")

MAX_TAGS = harmonize.MAX_THEMES  # people at the cap may have lost tags
FIELDS = ("industrial_ecology", "environmental_biology")
# Positions shown as one filter group each
POSITION_GROUP = {
    "Professor": "Professors", "Professor by special appointment": "Professors", "Professor emeritus": "Professors",
    "Associate professor": "Associate & assistant professors", "Assistant professor": "Associate & assistant professors",
    "Postdoc": "Postdocs & researchers", "Researcher": "Postdocs & researchers", "Lecturer": "Postdocs & researchers",
    "PhD candidate": "PhD candidates",
}
# Areas of the fine themes that point to each department, for people whose profile lists no department
FIELD_AREAS = {
    "industrial_ecology": {"Assessment methods", "Resources, energy & built environment", "Society, food & policy"},
    "environmental_biology": {"Biodiversity & ecosystems", "Pollution & chemicals", "Water, land & earth system"},
}


def dedupe_sentences(text):
    """Drop repeated sentences (scraped profiles often repeat their first sentence)."""
    parts = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text or "").strip())
    seen, out = set(), []
    for s in parts:
        key = s.lower().strip(" .")
        if key and key not in seen:
            seen.add(key)
            out.append(s)
    return " ".join(out)


def slug(url):
    return url.rstrip("/").rsplit("/staffmembers/", 1)[-1]


def norm(tag):
    t = re.sub(r"[^a-z0-9 ]", " ", tag.lower().replace("&", " and "))
    return " ".join(w[:-1] if w.endswith("s") and len(w) > 3 else w for w in t.split())


# ---------------------------------------------------------------- inputs

def load_people():
    if STAFF.exists():
        people = [p for p in json.loads(STAFF.read_text(encoding="utf-8")) if p.get("rank") != "Support staff"]
        for p in people:
            p["original_keywords"] = list(p.get("keywords") or [])
        source = STAFF.name
    else:   # the old professors-only file
        people = [p for v in json.loads(LEGACY.read_text(encoding="utf-8")).values() for p in v]
        source = LEGACY.name
    for p in people:
        p["summary"] = dedupe_sentences(p.get("summary", ""))
        p["id"] = slug(p["profile_url"])
        p["position"] = POSITION_GROUP.get(p.get("rank"), "Postdocs & researchers")
        p.setdefault("field", "")
        p["field_basis"] = "profile" if p["field"] in FIELDS else ""
    return people, source


def fill_departments(people, edges):
    """Profiles that list only institute-wide units get the department of their CML co-authors,
    otherwise the one their keywords and bio point to."""
    field = {p["id"]: p["field"] for p in people if p["field"] in FIELDS}
    for p in people:
        if p["field"] in FIELDS:
            continue
        votes = Counter()
        for a, b, n in edges:
            other = b if a == p["id"] else a if b == p["id"] else None
            if other in field:
                votes[field[other]] += n
        if votes and votes.most_common(1)[0][1] >= 2:
            p["field"], p["field_basis"] = votes.most_common(1)[0][0], "co-authors"
            continue
        _, tags = harmonize.harmonize_person(p)
        score = Counter({f: sum(1 for t in tags if harmonize.THEME_AREA.get(t) in areas) for f, areas in FIELD_AREAS.items()})
        p["field"] = score.most_common(1)[0][0] if score and score.most_common(1)[0][1] else "industrial_ecology"
        p["field_basis"] = "keywords and bio" if score.most_common(1)[0][1] else "unknown (default)"


def load_network(people):
    if not NETWORK.exists():
        return None, {}
    net = json.loads(NETWORK.read_text(encoding="utf-8"))
    pub_texts = json.loads(PUB_TEXTS.read_text(encoding="utf-8")) if PUB_TEXTS.exists() else {}
    ids = {p["id"] for p in people}
    inst_idx = {i["id"]: k for k, i in enumerate(net["institutions"])}
    for p in people:
        rec = net["people"].get(p["profile_url"])
        if rec:
            rec = dict(rec)
            rec["partners"] = [[inst_idx[i], n] for i, n in rec["partners"] if i in inst_idx]
            rec["ext_coauthors"] = [[name, inst_idx.get(i, -1), n] for name, i, n in rec["ext_coauthors"]]
            p["pub"] = rec
        p["pub_texts"] = pub_texts.get(p["profile_url"], [])
    edges = [[slug(a), slug(b), n] for a, b, n in net["edges"] if slug(a) in ids and slug(b) in ids]
    compact = {
        "meta": net["meta"],
        "edges": edges,
        "inst": [[i["name"], i["cc"]] for i in net["institutions"]],
        "cw": [[[slug(u) for u in ppl if slug(u) in ids], ins, ccs] for ppl, ins, ccs in net["cw"]],
    }
    compact["cw"] = [r for r in compact["cw"] if r[0]]
    return compact, pub_texts


# ---------------------------------------------------------------- report

def report(people, taxonomy, source):
    tags = Counter(t for p in people for t in p["harmonized_tags"])
    lines = ["# Tag quality report", "", f"{len(people)} people (from {source}), {len(tags)} harmonized tags.", ""]
    lines += ["## People by position and department", "", "| Position | Industrial ecology | Environmental biology |", "|---|---|---|"]
    for pos in dict.fromkeys(POSITION_GROUP.values()):
        c = Counter(p["field"] for p in people if p["position"] == pos)
        lines.append(f"| {pos} | {c['industrial_ecology']} | {c['environmental_biology']} |")
    guessed = [p for p in people if p["field_basis"] not in ("profile", "")]
    lines += ["", f"## Department not on the profile ({len(guessed)})", ""]
    lines += [f"- {p['name']}: {LABEL[p['field']]} (from {p['field_basis']})" for p in guessed] or ["- none"]

    lines += ["", "## Tag usage", "", "| Tag | People |", "|---|---|"]
    lines += [f"| {t} | {n} |" for t, n in tags.most_common()]
    singles = [t for t, n in tags.items() if n == 1]
    lines += ["", "## Tags used by one person (consider merging or dropping)", ""]
    lines += [f"- {t} ({next(p['name'] for p in people if t in p['harmonized_tags'])})" for t in singles] or ["- none"]
    lines += ["", "## Near-duplicate tag names", ""]
    near = [(a, b) for a, b in combinations(tags, 2) if difflib.SequenceMatcher(None, norm(a), norm(b)).ratio() > 0.8]
    lines += [f"- {a}  <->  {b}" for a, b in near] or ["- none"]
    lines += ["", "## Tags that almost always appear together (candidates to merge)", ""]
    co = Counter()
    for p in people:
        for a, b in combinations(sorted(set(p["harmonized_tags"])), 2):
            co[(a, b)] += 1
    rows = [f"- {a}  <->  {b}: {n} shared, Jaccard {n / (tags[a] + tags[b] - n):.2f}"
            for (a, b), n in co.items() if n >= 2 and n / (tags[a] + tags[b] - n) >= 0.6]
    lines += rows or ["- none"]
    zero = [p["name"] for p in people if not p["harmonized_tags"]]
    lines += ["", f"## People with no harmonized tag ({len(zero)})", ""]
    lines += [f"- {n}" for n in zero] or ["- none"]
    capped = [p["name"] for p in people if len(p["harmonized_tags"]) >= MAX_TAGS]
    lines += ["", f"## People at the {MAX_TAGS}-tag cap ({len(capped)}): tags may have been truncated", ""]
    lines += [f"- {n}" for n in capped] or ["- none"]

    cml = Counter(t for p in people for t in p["cml_themes"])
    lines += ["", "## CML theme assignment (people can have up to %d)" % harmonize.CML_MAX, "", "| Theme | People |", "|---|---|"]
    lines += [f"| {t} | {n} |" for t, n in cml.most_common()]
    lines += ["", "Manually assigned (see harmonize.CML_OVERRIDES): " + ", ".join(sorted(harmonize.CML_OVERRIDES))]
    lines += ["", "## Keyword harmonization", "",
              f"{len({k for p in people for k in p['raw_keywords']})} raw keywords -> "
              f"{len({k for p in people for k in p['original_keywords']})} canonical keywords."]
    unm = taxonomy["unmapped"]
    lines += ["", f"## Keywords without a theme mapping ({len(unm)}): add them to harmonize.KEYWORD_THEMES", ""]
    lines += [f"- {k} ({n})" for k, n in sorted(unm.items(), key=lambda kv: -kv[1])] or ["- none"]
    lines += ["", "## Spot-check sample (name, tags, original keywords)", ""]
    for p in people[:: max(1, len(people) // 10)]:
        lines.append(f"- **{p['name']}** ({p['rank']}): {', '.join(p['harmonized_tags']) or '-'}  |  {', '.join(p['raw_keywords'][:8])}")
    return "\n".join(lines) + "\n"


LABEL = {"industrial_ecology": "Industrial ecology", "environmental_biology": "Environmental biology"}


def main():
    strict = "--strict" in sys.argv
    people, source = load_people()
    net, _ = load_network(people)
    fill_departments(people, net["edges"] if net else [])

    data = {f: [p for p in people if p["field"] == f] for f in FIELDS}
    taxonomy = harmonize.apply(data)
    if taxonomy["unmapped"]:
        msg = f"{len(taxonomy['unmapped'])} keywords have no theme mapping (listed in {REPORT.name})"
        if strict:
            raise SystemExit(msg + ": " + ", ".join(taxonomy["unmapped"]))
        print("note:", msg)
    REPORT.write_text(report(people, taxonomy, source), encoding="utf-8")
    # the merged person records, for the paper index and the assistant (build_index.py)
    (HERE / "data").mkdir(exist_ok=True)
    (HERE / "data" / "people.json").write_text(json.dumps([{
        "id": p["id"], "name": p["name"], "rank": p.get("rank", ""), "position": p["position"],
        "department": LABEL[p["field"]], "department_basis": p["field_basis"], "cml_themes": p["cml_themes"],
        "tags": p["harmonized_tags"], "keywords": p["original_keywords"], "summary": p.get("summary", ""),
        "profile_url": p["profile_url"], "photo_url": p.get("photo_url", ""), "orcid": p.get("orcid", ""),
        "projects": p.get("projects", []),
    } for p in people], ensure_ascii=False, indent=1), encoding="utf-8")

    keep = ("id", "name", "title", "rank", "position", "field", "field_basis", "profile_url", "photo_url", "summary",
            "projects", "harmonized_tags", "original_keywords", "cml_themes", "pub", "list_role")
    slim = {f: [{k: p[k] for k in keep if k in p} for p in plist] for f, plist in data.items()}
    payload = json.dumps(slim, ensure_ascii=False).replace("</", "<\\/")
    tax = json.dumps({k: taxonomy[k] for k in ("areas", "themeArea", "keywordThemes", "cml", "cmlWeights")},
                     ensure_ascii=False).replace("</", "<\\/")
    world = WORLD.read_text(encoding="utf-8") if WORLD.exists() else "null"
    html = (TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", payload).replace("__TAXONOMY__", tax)
            .replace("__NETWORK__", json.dumps(net, ensure_ascii=False).replace("</", "<\\/") if net else "null")
            .replace("__WORLD__", world).replace("__ASSISTANT_URL__", ASSISTANT_URL))
    OUT.write_text(html, encoding="utf-8")
    PAGES_COPY.write_text(html, encoding="utf-8")
    if SITE_COPY.parent.exists():
        SITE_COPY.write_text(html, encoding="utf-8")
    with_pubs = sum(1 for p in people if (p.get("pub") or {}).get("n"))
    print(f"{len(people)} people ({with_pubs} with publications) from {source} -> {OUT.name} "
          f"({OUT.stat().st_size // 1024} KB), {REPORT.name}")


if __name__ == "__main__":
    main()
