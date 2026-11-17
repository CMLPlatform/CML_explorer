"""Publications, co-authorship and external partners for the dashboard.

Reads the OpenAlex harvest (data/people.csv, data/works.jsonl from harvest_openalex.py) and writes:
  data/network.json            per person (keyed by profile URL): paper counts, keywords, topics, recent papers,
                               partners; plus CML co-author links, partner institutions and one row per joint paper
  data/pub_texts.json          per person: title + keywords of each counted paper (used by harmonize.py)
  data/match_review.csv        OpenAlex matches worth checking by hand

Which papers count for a person:
  * matched by ORCID       -> all papers since the harvest's start year (expertise), and the
                              Leiden-affiliated ones for collaborations
  * matched by name only   -> only papers where they are listed at Leiden (protects against namesakes)
Datasets, abstracts, errata etc. are left out. Papers with more than MAX_AUTHORS authors count for
topics but not as collaborations.

Usage:  python3 build_network.py
"""
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
LEIDEN = "I121797337"   # Leiden University in OpenAlex
KEEP_TYPES = {"article", "review", "book-chapter", "book", "conference-paper",
              "preprint", "report", "dissertation", "letter", "editorial"}
MAX_AUTHORS = 50


def short(x):
    return (x or "").rsplit("/", 1)[-1]


def norm(s):
    s = (s or "").lower().translate(str.maketrans("áàäâãéèëêíìïîóòöôõúùüûçñ", "aaaaaeeeeiiiiooooouuuucn"))
    return re.sub(r"[^a-z]", "", s)


def same_name(a, b):
    """Rough check that two name strings could be the same person (last name appears in the other)."""
    ta = [norm(t) for t in re.split(r"[\s\-‐.]+", a) if len(norm(t)) > 1]
    tb = [norm(t) for t in re.split(r"[\s\-‐.]+", b) if len(norm(t)) > 1]
    return bool(ta and tb) and (ta[-1] in tb or tb[-1] in ta)


def counted_works():
    """Apply the counting rules. Returns (people, p_works, p_cml_works, works, review), where p_works maps an
    OpenAlex author id to the papers that count for that person and p_cml_works to those written at Leiden."""
    people = list(csv.DictReader(open(DATA / "people.csv", encoding="utf-8")))
    by_aid = {p["openalex_id"]: p for p in people if p["openalex_id"]}

    # ---- which works count for whom
    p_works, p_cml_works, works = defaultdict(set), defaultdict(set), {}
    for line in open(DATA / "works.jsonl", encoding="utf-8"):
        w = json.loads(line)
        if w.get("type") not in KEEP_TYPES:
            continue
        wid, hit = short(w["id"]), False
        for a in w.get("authorships") or []:
            aid = short((a.get("author") or {}).get("id"))
            p = by_aid.get(aid)
            if not p:
                continue
            at_leiden = any(short(i.get("id")) == LEIDEN for i in a.get("institutions") or [])
            if p["match_confidence"] == "orcid" or at_leiden:
                p_works[aid].add(wid)
                hit = True
            if at_leiden:
                p_cml_works[aid].add(wid)
        if hit:
            works[wid] = w

    # ---- review notes on the matches; drop clear namesakes
    review = []
    for p in people:
        aid, conf = p["openalex_id"], p["match_confidence"]
        n_all, n_ok = int(p["works_count"] or 0), len(p_works.get(aid, ()))
        if conf == "orcid":
            note = "" if same_name(p["name"], p["openalex_name"]) else "ORCID profile has a different name: check"
        elif conf == "no-match":
            note = "no OpenAlex profile found (often: no publications yet)"
        elif not same_name(p["name"], p["openalex_name"]) and conf != "leiden-unique":
            note = "WRONG PERSON likely (different name): ignored"
        elif not same_name(p["name"], p["openalex_name"]):
            note = "only Leiden profile with this name, but spelled differently: check"
        elif n_ok == 0:
            note = "no Leiden-affiliated works: ignored until an ORCID is added"
        elif n_all > 15 and n_ok < 0.1 * n_all:
            note = f"profile may mix several people ({n_all} works, {n_ok} at Leiden): only Leiden works used"
        elif n_all > 2 * n_ok:
            note = f"no ORCID: only the {n_ok} Leiden works are used (add ORCID for full record)"
        else:
            note = ""
        p["works_used"], p["note"] = n_ok, note
        if note.startswith("WRONG"):
            p_works.pop(aid, None)
            p_cml_works.pop(aid, None)
        if note or conf not in ("orcid", "leiden-unique"):
            review.append(p)
    return people, p_works, p_cml_works, works, review


def main():
    people, p_works, p_cml_works, works, review = counted_works()
    with open(DATA / "match_review.csv", "w", newline="", encoding="utf-8") as f:
        fields = ["name", "role", "match_confidence", "openalex_name", "openalex_id",
                  "works_count", "works_used", "note", "profile_url"]
        wr = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(sorted(review, key=lambda r: (not r["note"].startswith("WRONG"), r["note"], r["name"])))

    # ---- aggregate
    active = {aid for aid, ws in p_works.items() if ws}
    inst_info, inst_works = {}, defaultdict(set)
    edges = Counter()
    p_partners, p_countries, p_topics, p_subfields, p_keywords, p_ext = (defaultdict(Counter) for _ in range(6))
    ext_people, collab_rows = {}, []
    for wid, w in works.items():
        auths = w.get("authorships") or []
        here = [short(a["author"]["id"]) for a in auths if a.get("author")
                and short(a["author"]["id"]) in active and wid in p_works[short(a["author"]["id"])]]
        for aid in here:
            for t in (w.get("topics") or [])[:2]:
                p_topics[aid][t["display_name"]] += 1
                p_subfields[aid][t["subfield"]["display_name"]] += 1
            for k in (w.get("keywords") or [])[:5]:
                p_keywords[aid][k.get("display_name", "").lower()] += 1
        cml = sorted({aid for aid in here if wid in p_cml_works.get(aid, ())})
        if not cml or len(auths) > MAX_AUTHORS:
            continue
        for i, a in enumerate(cml):
            for b in cml[i + 1:]:
                edges[(a, b)] += 1
        row_insts, row_cc = set(), set()
        for a in auths:
            aid = short((a.get("author") or {}).get("id"))
            if aid in active:
                continue
            insts = [i for i in (a.get("institutions") or []) if i.get("id") and short(i["id"]) != LEIDEN]
            for inst in insts:
                iid = short(inst["id"])
                inst_info[iid] = (inst.get("display_name"), inst.get("country_code") or "", inst.get("type") or "")
                inst_works[iid].add(wid)
                row_insts.add(iid)
                if inst.get("country_code"):
                    row_cc.add(inst["country_code"])
                for c in cml:
                    p_partners[c][iid] += 1
            for cc in {i.get("country_code") for i in insts if i.get("country_code")}:
                for c in cml:
                    p_countries[c][cc] += 1
            name = (a.get("author") or {}).get("display_name")
            if name and aid:
                ext_people[aid] = (name, short(insts[0]["id"]) if insts else "")
                for c in cml:
                    p_ext[c][aid] += 1
        collab_rows.append((cml, row_insts, row_cc))

    def paper(wid):
        w = works[wid]
        src = ((w.get("primary_location") or {}).get("source") or {}).get("display_name")
        return {"t": w.get("title"), "y": w.get("publication_year"), "doi": w.get("doi"),
                "v": src, "c": w.get("cited_by_count", 0)}

    url_of = {p["openalex_id"]: p["profile_url"] for p in people if p["openalex_id"]}
    out_people, pub_texts = {}, {}
    for p in people:
        aid = p["openalex_id"]
        ws = p_works.get(aid, set())
        recent = sorted(ws, key=lambda x: (works[x].get("publication_year") or 0,
                                            works[x].get("cited_by_count") or 0), reverse=True)
        out_people[p["profile_url"]] = {
            "oa": aid, "match": p["match_confidence"], "note": p["note"],
            "n": len(ws), "n_cml": len(p_cml_works.get(aid, ())),
            "cites": sum(works[x].get("cited_by_count") or 0 for x in ws),
            "pkw": [k for k, _ in p_keywords[aid].most_common(15) if k],
            "topics": [k for k, _ in p_topics[aid].most_common(6)],
            "sub": [k for k, _ in p_subfields[aid].most_common(4)],
            "partners": [[iid, n] for iid, n in p_partners[aid].most_common(8)],
            "countries": [[cc, n] for cc, n in p_countries[aid].most_common(8)],
            "ext_coauthors": [[ext_people[x][0], ext_people[x][1], n] for x, n in p_ext[aid].most_common(6)],
            "papers": [paper(x) for x in recent[:6]],
        }
        if ws:
            pub_texts[p["profile_url"]] = [
                " ".join([works[x].get("title") or ""] + [k.get("display_name", "") for k in (works[x].get("keywords") or [])])
                for x in recent]

    insts = sorted(inst_works, key=lambda i: len(inst_works[i]), reverse=True)
    iidx = {i: k for k, i in enumerate(insts)}
    out = {
        "meta": {"works": len(works), "collab_works": len(collab_rows), "max_authors": MAX_AUTHORS},
        "people": out_people,
        "edges": [[url_of[a], url_of[b], n] for (a, b), n in edges.most_common()],
        "institutions": [{"id": i, "name": inst_info[i][0], "cc": inst_info[i][1], "type": inst_info[i][2],
                          "n": len(inst_works[i])} for i in insts],
        # one row per joint paper written at Leiden: [CML profile URLs], [institution indexes], [country codes]
        "cw": [[[url_of[a] for a in ppl], sorted(iidx[i] for i in ins), sorted(ccs)] for ppl, ins, ccs in collab_rows],
    }
    (DATA / "network.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (DATA / "pub_texts.json").write_text(json.dumps(pub_texts, ensure_ascii=False), encoding="utf-8")
    print(f"{len(active)} people with papers, {len(works)} papers ({len(collab_rows)} joint papers written at Leiden), "
          f"{len(edges)} CML co-author links, {len(insts)} partner institutions")
    print(f"{sum(1 for r in review if r['note'].startswith('WRONG'))} wrong matches ignored; see data/match_review.csv")


if __name__ == "__main__":
    main()
