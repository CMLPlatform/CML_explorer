"""Build data/knowledge.db: a searchable SQLite database of CML people and their papers, for the assistant.

Tables
  people        one row per research staff member (themes, department, keywords, bio) from data/people.json
  papers        papers that count for at least one CML person (same rules as build_network.py)
  authorship    paper <-> CML person, with author position and whether they were at Leiden
  partners      paper <-> external institution and country (joint papers written at Leiden)
  papers_fts    full-text index (BM25) over title, abstract, keywords and topics
  people_fts    full-text index over name, keywords, themes and bio

Usage:  python3 build_index.py      (after build_network.py and build_dashboard.py; standard library only)
"""
import json
import sqlite3
from pathlib import Path

import build_network as bn

HERE = Path(__file__).resolve().parent
DB = HERE / "data" / "knowledge.db"
PEOPLE = HERE / "data" / "people.json"


def short(x):
    return (x or "").rsplit("/", 1)[-1]


def main():
    people_csv, p_works, p_cml_works, works, _ = bn.counted_works()
    profiles = {p["profile_url"]: p for p in json.loads(PEOPLE.read_text(encoding="utf-8"))} if PEOPLE.exists() else {}
    url_to_id = {p["profile_url"]: p["profile_url"].rstrip("/").rsplit("/staffmembers/", 1)[-1] for p in people_csv}
    aid_to_id = {p["openalex_id"]: url_to_id[p["profile_url"]] for p in people_csv if p["openalex_id"]}

    if DB.exists():
        DB.unlink()
    db = sqlite3.connect(DB)
    db.executescript("""
    CREATE TABLE people (id TEXT PRIMARY KEY, name TEXT, rank TEXT, position TEXT, department TEXT,
        cml_themes TEXT, tags TEXT, keywords TEXT, summary TEXT, profile_url TEXT, orcid TEXT,
        openalex_id TEXT, n_papers INTEGER, n_cml_papers INTEGER);
    CREATE TABLE papers (id TEXT PRIMARY KEY, title TEXT, year INTEGER, date TEXT, doi TEXT, venue TEXT, type TEXT,
        cited_by INTEGER, abstract TEXT, keywords TEXT, topics TEXT, oa_url TEXT, n_authors INTEGER, authors TEXT);
    CREATE TABLE authorship (paper_id TEXT, person_id TEXT, position TEXT, at_leiden INTEGER, corresponding INTEGER);
    CREATE TABLE partners (paper_id TEXT, institution TEXT, country TEXT, type TEXT);
    CREATE INDEX a_paper ON authorship(paper_id); CREATE INDEX a_person ON authorship(person_id);
    CREATE INDEX p_paper ON partners(paper_id); CREATE INDEX p_country ON partners(country);
    CREATE VIRTUAL TABLE papers_fts USING fts5(id UNINDEXED, title, abstract, keywords, topics, tokenize='porter unicode61');
    CREATE VIRTUAL TABLE people_fts USING fts5(id UNINDEXED, name, keywords, themes, summary, tokenize='porter unicode61');
    """)

    # people
    for p in people_csv:
        pid, prof = url_to_id[p["profile_url"]], profiles.get(p["profile_url"], {})
        aid = p["openalex_id"]
        row = (pid, p["name"], prof.get("rank", p.get("title", "")), prof.get("position", ""), prof.get("department", ""),
               "; ".join(prof.get("cml_themes", [])), "; ".join(prof.get("tags", [])),
               "; ".join(prof.get("keywords", [])), prof.get("summary", ""), p["profile_url"], p.get("orcid", ""),
               aid, len(p_works.get(aid, ())), len(p_cml_works.get(aid, ())))
        db.execute("INSERT INTO people VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
        db.execute("INSERT INTO people_fts VALUES (?,?,?,?,?)", (pid, p["name"], row[7] + "; " + row[6], row[5], row[8]))

    # papers, authorship, partners
    counted = {(aid, wid) for aid, ws in p_works.items() for wid in ws}
    for wid, w in works.items():
        auths = w.get("authorships") or []
        loc = w.get("best_oa_location") or {}
        src = ((w.get("primary_location") or {}).get("source") or {}).get("display_name")
        kws = "; ".join(k.get("display_name", "") for k in (w.get("keywords") or []))
        topics = "; ".join(t.get("display_name", "") for t in (w.get("topics") or [])[:3])
        names = [(a.get("author") or {}).get("display_name") or "" for a in auths]
        authors = ", ".join(names[:12]) + (f" … (+{len(names) - 12})" if len(names) > 12 else "")
        db.execute("INSERT INTO papers VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (wid, w.get("title"), w.get("publication_year"), w.get("publication_date"), w.get("doi"), src,
                    w.get("type"), w.get("cited_by_count") or 0, w.get("abstract") or "", kws, topics,
                    loc.get("pdf_url") or loc.get("landing_page_url"), len(auths), authors))
        db.execute("INSERT INTO papers_fts VALUES (?,?,?,?,?)", (wid, w.get("title") or "", w.get("abstract") or "", kws, topics))
        cml_here = False
        for a in auths:
            aid = short((a.get("author") or {}).get("id"))
            if (aid, wid) in counted:
                at_leiden = any(short(i.get("id")) == bn.LEIDEN for i in a.get("institutions") or [])
                cml_here = cml_here or at_leiden
                db.execute("INSERT INTO authorship VALUES (?,?,?,?,?)",
                           (wid, aid_to_id[aid], a.get("author_position"), int(at_leiden), int(bool(a.get("is_corresponding")))))
        if cml_here and len(auths) <= bn.MAX_AUTHORS:
            seen = set()
            for a in auths:
                if short((a.get("author") or {}).get("id")) in aid_to_id:
                    continue
                for i in a.get("institutions") or []:
                    if i.get("id") and short(i["id"]) != bn.LEIDEN and i["id"] not in seen:
                        seen.add(i["id"])
                        db.execute("INSERT INTO partners VALUES (?,?,?,?)",
                                   (wid, i.get("display_name"), i.get("country_code"), i.get("type")))
    db.commit()
    n = {t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("people", "papers", "authorship", "partners")}
    n_abs = db.execute("SELECT count(*) FROM papers WHERE abstract != ''").fetchone()[0]
    db.close()
    print(f"{DB.name}: {n['people']} people, {n['papers']} papers ({n_abs} with abstract), "
          f"{n['authorship']} CML authorships, {n['partners']} partner links ({DB.stat().st_size // 1024 // 1024} MB)")


if __name__ == "__main__":
    main()
