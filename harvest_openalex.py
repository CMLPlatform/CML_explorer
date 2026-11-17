"""
OpenAlex harvest for the CML network: publications and co-authors of every research staff member.

1. Takes the people from leiden_staff.json (scrape_staff.py); without that file it scrapes the
   staff page itself (fallback).
2. Matches every research staff member to an OpenAlex author
   (ORCID first, otherwise name search restricted to Leiden University).
3. Downloads their works and builds a co-authorship table with external
   institutions and countries.

Usage:
    pip install requests beautifulsoup4
    python3 harvest_openalex.py                    # everything (resumes if interrupted)
    python3 harvest_openalex.py --since 2015-01-01 # limit publication years
    python3 harvest_openalex.py --staff-only       # just the staff list
    python3 harvest_openalex.py --refresh-staff    # re-scrape the staff page and profiles
    python3 harvest_openalex.py --fresh            # ignore saved progress and start over

Put OPENALEX_API_KEY=<your free OpenAlex key> in a .env file next to this script
(or export it in the shell). Without a key, OpenAlex only allows a small shared
daily budget, which runs out after ~150 lookups.

The run saves progress after every person, so if it stops (network drop, laptop
sleep, rate limit) just run the same command again and it continues where it was.
Everything that happens is also written to data/harvest.log.

Outputs (in ./data, next to this script):
    staff.csv          everyone on the page, with role category
    people.csv         research staff + OpenAlex match and confidence
    match_review.csv   matches worth checking by hand
    works.jsonl        one OpenAlex work per line (deduplicated)
    authorships.csv    CML person -> work, with author position
    collaborations.csv every co-author on CML works, with institution and country
    harvest.log        progress and errors
    cache/             saved progress (safe to delete for a clean start)
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

HERE = Path(__file__).resolve().parent


def load_env(path=HERE / ".env"):
    """Read KEY=value lines from .env next to this script.
    Variables already set in the shell take priority."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


load_env()
load_env(HERE.parent / ".env")   # also accept a .env one folder up (outside the repo)

STAFF_URL = "https://www.universiteitleiden.nl/en/science/environmental-sciences/staff_alphabetically"
OA = "https://api.openalex.org"
OUT = HERE / "data"          # always next to the script, whatever folder you run it from
CACHE = OUT / "cache"
LOG = OUT / "harvest.log"
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "cml-knowledge-base/0.2"
MAILTO = os.environ.get("OPENALEX_MAILTO")
API_KEY = os.environ.get("OPENALEX_API_KEY")


class StopRun(Exception):
    """A problem that makes continuing pointless (bad key, budget used up)."""


def log(msg, err=False):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(msg, file=sys.stderr if err else sys.stdout, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- roles

SUPPORT_PATTERNS = [
    r"coordinator", r"co[oö]rdinator", r"study advis", r"office manager", r"technician",
    r"support officer", r"financial controller", r"board secretary", r"director of operations",
    r"internship labs", r"grant advisor", r"project manager", r"program manager",
    r"student affairs", r"team leader", r"visitor centre",
]
ROLE_RULES = [  # first match wins
    ("emeritus", r"emerit"),
    ("associate professor", r"associate prof"),
    ("assistant professor", r"assistant prof"),
    ("professor", r"\bprofessor\b|scientific director"),
    ("postdoc", r"post-?doc"),
    ("phd candidate", r"\bphd\b"),
    ("researcher", r"researcher|research assistant|education and research|education-/research|director of education"),
    ("lecturer", r"lecturer"),
]


def role_category(title: str) -> str:
    t = (title or "").lower()
    if not t:
        return "unknown"
    if any(re.search(p, t) for p in SUPPORT_PATTERNS):
        return "support"
    for cat, pat in ROLE_RULES:
        if re.search(pat, t):
            return cat
    return "other"


def is_external(title: str) -> bool:
    return bool(re.search(r"external|guest|self.?funded", (title or "").lower()))


# ---------------------------------------------------------------- http


def get(url, params=None, tries=6):
    params = dict(params or {})
    is_oa = url.startswith(OA)
    if is_oa:
        if MAILTO:
            params["mailto"] = MAILTO
        if API_KEY:
            params["api_key"] = API_KEY
    last = None
    i = 0
    while i < tries:
        i += 1
        try:
            r = SESSION.get(url, params=params, timeout=60)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last = f"{e.__class__.__name__}"
            wait = min(2 ** (i + 1), 60)
            log(f"    network problem ({last}), retry in {wait}s", err=True)
            time.sleep(wait)
            continue
        if r.status_code == 200:
            return r
        if r.status_code == 404:
            return None
        body = r.text[:300].lower()
        if is_oa and r.status_code in (401, 403):
            raise StopRun(
                f"OpenAlex refused the API key (HTTP {r.status_code}). "
                "Check OPENALEX_API_KEY in .env: copy it again from your openalex.org account.\n"
                f"OpenAlex said: {r.text[:200]}")
        if r.status_code == 429:
            # Short throttle (e.g. new keys are limited to $0.10 per 5 minutes): wait it out.
            wait = None
            try:
                wait = float(r.headers.get("retry-after", ""))
            except ValueError:
                m = re.search(r"retry in (\d+) second", body)
                wait = float(m.group(1)) if m else None
            if wait is not None and wait <= 15 * 60:
                log(f"    OpenAlex asks to slow down, waiting {int(wait) + 1}s", err=True)
                time.sleep(wait + 1)
                i -= 1  # a requested pause is not a failed attempt
                continue
        if r.status_code == 429 and ("budget" in body or "credit" in body):
            raise StopRun(
                "OpenAlex daily budget used up"
                + (" for this API key" if API_KEY else " (no API key was used)")
                + f". OpenAlex said: {r.text[:200]}\n"
                "Progress is saved: run the same command again after the reset (01:00 Amsterdam time).")
        if r.status_code in (429, 500, 502, 503, 504):
            last = f"HTTP {r.status_code}"
            wait = min(2 ** (i + 1), 60)
            log(f"    {last} from server, retry in {wait}s", err=True)
            time.sleep(wait)
            continue
        last = f"HTTP {r.status_code}: {r.text[:200]}"
        break
    raise requests.exceptions.RequestException(f"{url} failed: {last}")


def check_openalex():
    """One cheap request up front so a bad key or empty budget shows up immediately."""
    if API_KEY:
        log(f"OpenAlex API key loaded ({API_KEY[:3]}…, {len(API_KEY)} characters)")
    else:
        log("WARNING: no OPENALEX_API_KEY found (.env or shell). The run will likely stop early.", err=True)
    r = get(f"{OA}/institutions", {"search": "Leiden University", "filter": "country_code:NL"})
    left = r.headers.get("x-ratelimit-remaining")
    if left is not None:
        log(f"OpenAlex accepted the request; remaining budget reported: {left}")
    for inst in r.json()["results"]:
        if inst["display_name"].lower() == "leiden university":
            return inst["id"].rsplit("/", 1)[-1]
    raise StopRun("Could not find Leiden University in OpenAlex")


# ---------------------------------------------------------------- cache


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------- staff


def scrape_staff():
    html = get(STAFF_URL).text
    soup = BeautifulSoup(html, "html.parser")
    people, seen = [], set()
    for a in soup.select("a[href*='/staffmembers/']"):
        strong = a.find("strong")
        if not strong:
            continue
        url = requests.compat.urljoin(STAFF_URL, a["href"])
        if url in seen:
            continue
        seen.add(url)
        name = strong.get_text(" ", strip=True)
        title = a.get_text(" ", strip=True)[len(name):].strip()
        people.append({
            "name": name,
            "title": title,
            "role": role_category(title),
            "external_or_guest": is_external(title),
            "profile_url": url,
        })
    return people


def department_of(units):
    """Map the unit lines on a profile ('CML', 'Industriële Ecologie', ...) to a department."""
    text = " ".join(units).lower()
    if "industri" in text:
        return "Industrial Ecology"
    if "environmental biology" in text or "ecotox" in text:
        return "Environmental Biology"
    if "cml" in text or "environmental sciences" in text or "leiden-delft-erasmus" in text:
        return "Institute-wide"
    return ""


def profile_info(url):
    """Return (orcid, department) from a staff profile page."""
    try:
        r = get(url)
    except requests.exceptions.RequestException as e:
        log(f"    skipped profile {url}: {e}", err=True)
        return "", ""
    if not r:
        return "", ""
    m = re.search(r"orcid\.org/(\d{4}-\d{4}-\d{4}-\d{3}[\dX])", r.text)
    soup = BeautifulSoup(r.text, "html.parser")
    depts = []
    for role in soup.select(".role"):
        col = role.select_one(".col")
        units = [li.get_text(" ", strip=True) for li in (col.select("li") if col else [])]
        d = department_of([u for u in units if u])
        if d:
            depts.append(d)
    # a real department beats an institute-wide role (education, general)
    dept = next((d for d in depts if d != "Institute-wide"), depts[0] if depts else "")
    return (m.group(1) if m else ""), dept


ROLE_FROM_RANK = {"Support staff": "support", "PhD candidate": "phd candidate", "Postdoc": "postdoc",
                  "Assistant professor": "assistant professor", "Associate professor": "associate professor",
                  "Professor": "professor", "Professor emeritus": "emeritus",
                  "Professor by special appointment": "professor", "Lecturer": "lecturer", "Researcher": "researcher"}


def staff_from_scrape():
    """Use leiden_staff.json from scrape_staff.py when it exists (the normal case)."""
    path = HERE / "leiden_staff.json"
    if not path.exists():
        return None
    staff = []
    for s in json.loads(path.read_text(encoding="utf-8")):
        staff.append({"name": s["name"], "title": s.get("list_role") or s.get("title", ""),
                      "role": ROLE_FROM_RANK.get(s.get("rank"), "researcher"),
                      "external_or_guest": is_external(s.get("list_role") or ""),
                      "profile_url": s["profile_url"], "orcid": s.get("orcid", ""),
                      "department": s.get("field", "")})
    write_csv(OUT / "staff.csv", staff, ["name", "title", "role", "external_or_guest", "profile_url", "orcid", "department"])
    log(f"Using {path.name} from scrape_staff.py ({len(staff)} people)")
    return staff


def get_staff(refresh, skip_profiles):
    from_scrape = staff_from_scrape()
    if from_scrape is not None:
        return from_scrape
    path = OUT / "staff.csv"
    if path.exists() and not refresh:
        staff = list(csv.DictReader(open(path, encoding="utf-8")))
        log(f"Using saved staff list ({len(staff)} people). Use --refresh-staff to re-scrape.")
        return staff
    staff = scrape_staff()
    log(f"staff on page: {len(staff)}")
    if not skip_profiles:
        research = [p for p in staff if p["role"] != "support"]
        for i, p in enumerate(research, 1):
            p["orcid"], p["department"] = profile_info(p["profile_url"])
            print(f"  profile {i}/{len(research)} {p['name']}: {p['orcid'] or '-'} | {p['department'] or '-'}")
            time.sleep(1.0)  # be gentle: the university server resets fast repeated requests
    write_csv(path, staff, ["name", "title", "role", "external_or_guest", "profile_url", "orcid", "department"])
    return staff


# ---------------------------------------------------------------- openalex


def name_variants(name):
    yield name
    parts = name.split()
    core = [p for p in parts if p.lower() not in {"van", "der", "den", "de", "ter", "'t", "over"}]
    if len(core) != len(parts) and len(core) >= 2:
        yield " ".join(core)


def short(a_id):
    return (a_id or "").rsplit("/", 1)[-1]


def match_author(person, leiden_id):
    if person.get("orcid"):
        r = get(f"{OA}/authors/orcid:{person['orcid']}")
        if r:
            a = r.json()
            return dict(openalex_id=short(a["id"]), openalex_name=a["display_name"],
                        works_count=a["works_count"], match_confidence="orcid", n_candidates=1)

    for variant in name_variants(person["name"]):
        r = get(f"{OA}/authors", {
            "search": variant,
            "filter": f"affiliations.institution.id:{leiden_id}",
            "per-page": 10,
        })
        cands = r.json()["results"] if r else []
        if cands:
            cands.sort(key=lambda a: a["works_count"], reverse=True)
            best = cands[0]
            return dict(openalex_id=short(best["id"]), openalex_name=best["display_name"],
                        works_count=best["works_count"],
                        match_confidence="leiden-unique" if len(cands) == 1 else "leiden-multiple",
                        n_candidates=len(cands))

    r = get(f"{OA}/authors", {"search": person["name"], "per-page": 5})
    cands = r.json()["results"] if r else []
    if cands:
        best = cands[0]
        return dict(openalex_id=short(best["id"]), openalex_name=best["display_name"],
                    works_count=best["works_count"], match_confidence="name-only",
                    n_candidates=len(cands))
    return dict(openalex_id="", openalex_name="", works_count=0,
                match_confidence="no-match", n_candidates=0)


WORK_FIELDS = ",".join([
    "id", "doi", "title", "publication_year", "publication_date", "type",
    "primary_location", "open_access", "best_oa_location", "authorships",
    "topics", "keywords", "cited_by_count", "abstract_inverted_index",
])


def works_for(author_id, since):
    out, cursor = [], "*"
    while cursor:
        r = get(f"{OA}/works", {
            "filter": f"author.id:{author_id},from_publication_date:{since}",
            "select": WORK_FIELDS,
            "per-page": 200,
            "cursor": cursor,
        })
        if not r:
            break
        data = r.json()
        out.extend(data["results"])
        cursor = data["meta"].get("next_cursor")
    return out


def abstract_text(inv):
    if not inv:
        return ""
    pos = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos))


# ---------------------------------------------------------------- main


def write_csv(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def run(args):
    staff = get_staff(args.refresh_staff, args.skip_profiles)
    if args.staff_only:
        return
    research = [p for p in staff if p["role"] != "support"]
    log(f"research staff (incl. PhD/postdoc/unknown): {len(research)}")

    leiden = check_openalex()

    # ---- matching, saved after each person
    mpath = CACHE / "matches.json"
    matches = load_json(mpath, {})
    todo = [p for p in research if p["profile_url"] not in matches]
    if matches:
        log(f"Resuming matching: {len(matches)} done, {len(todo)} to go")
    failed = 0
    for i, p in enumerate(todo, 1):
        try:
            m = match_author(p, leiden)
        except requests.exceptions.RequestException as e:
            failed += 1
            log(f"  could not match {p['name']}: {e}", err=True)
            continue
        matches[p["profile_url"]] = m
        save_json(mpath, matches)
        print(f"  match {len(matches)}/{len(research)} {p['name']} -> "
              f"{m['openalex_name'] or '-'} ({m['match_confidence']}, {m['works_count']} works)", flush=True)
    log(f"Matching finished: {len(matches)}/{len(research)} people"
        + (f", {failed} failed (rerun to retry)" if failed else ""))

    for p in research:
        p.update(matches.get(p["profile_url"], dict(match_confidence="not-checked")))
    people_fields = ["name", "title", "role", "external_or_guest", "profile_url", "orcid",
                     "openalex_id", "openalex_name", "works_count", "match_confidence", "n_candidates"]
    write_csv(OUT / "people.csv", research, people_fields)
    write_csv(OUT / "match_review.csv",
              [p for p in research if p.get("match_confidence") not in ("orcid", "leiden-unique")],
              people_fields)
    log("Wrote people.csv and match_review.csv")

    # ---- works, one cache file per author
    wdir = CACHE / "works"
    wdir.mkdir(exist_ok=True)
    cml = {}
    for p in research:
        if p.get("openalex_id"):
            cml.setdefault(p["openalex_id"], p)
    todo = [a for a in cml if not (wdir / f"{a}.json").exists()]
    if len(todo) < len(cml):
        log(f"Resuming works download: {len(cml) - len(todo)} done, {len(todo)} to go")
    failed = 0
    for i, aid in enumerate(todo, 1):
        try:
            ws = works_for(aid, args.since)
        except requests.exceptions.RequestException as e:
            failed += 1
            log(f"  could not get works for {cml[aid]['name']}: {e}", err=True)
            continue
        save_json(wdir / f"{aid}.json", ws)
        print(f"  works {len(cml) - len(todo) + i}/{len(cml)} {cml[aid]['name']}: {len(ws)}", flush=True)
    if failed:
        log(f"{failed} people's works failed to download; rerun to retry", err=True)

    # ---- assemble outputs from the cache
    works, authorships, collabs = {}, [], []
    for aid, p in cml.items():
        for w in load_json(wdir / f"{aid}.json", []):
            wid = short(w["id"])
            auths = w.get("authorships") or []
            for a in auths:
                if short((a.get("author") or {}).get("id")) == aid:
                    authorships.append({
                        "person": p["name"], "openalex_id": aid, "work_id": wid,
                        "year": w.get("publication_year"),
                        "position": a.get("author_position"),
                        "is_corresponding": a.get("is_corresponding"),
                    })
            if wid in works:
                continue
            w = dict(w)
            w["abstract"] = abstract_text(w.pop("abstract_inverted_index", None))
            works[wid] = w
            for a in auths:
                author = a.get("author") or {}
                a_id = short(author.get("id"))
                for inst in (a.get("institutions") or [{}]):
                    collabs.append({
                        "work_id": wid,
                        "year": w.get("publication_year"),
                        "author_id": a_id,
                        "author_name": author.get("display_name"),
                        "is_cml_person": a_id in cml,
                        "institution_id": short(inst.get("id")),
                        "institution": inst.get("display_name"),
                        "institution_type": inst.get("type"),
                        "country": inst.get("country_code"),
                    })

    with open(OUT / "works.jsonl", "w", encoding="utf-8") as f:
        for w in works.values():
            f.write(json.dumps(w, ensure_ascii=False) + "\n")
    write_csv(OUT / "authorships.csv", authorships,
              ["person", "openalex_id", "work_id", "year", "position", "is_corresponding"])
    write_csv(OUT / "collaborations.csv", collabs,
              ["work_id", "year", "author_id", "author_name", "is_cml_person",
               "institution_id", "institution", "institution_type", "country"])

    log(f"Done: {len(research)} people, {len(cml)} matched, {len(works)} unique works, "
        f"{len(collabs)} author-institution rows -> {OUT}/")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2010-01-01")
    ap.add_argument("--staff-only", action="store_true")
    ap.add_argument("--skip-profiles", action="store_true", help="don't visit profile pages for ORCIDs")
    ap.add_argument("--refresh-staff", action="store_true", help="re-scrape the staff page and profiles")
    ap.add_argument("--fresh", action="store_true", help="ignore saved OpenAlex progress")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    CACHE.mkdir(exist_ok=True)
    if args.fresh:
        for f in [CACHE / "matches.json", *CACHE.glob("works/*.json")]:
            f.unlink(missing_ok=True)
    log(f"=== run started (python {sys.version.split()[0]}) ===")
    try:
        run(args)
    except StopRun as e:
        log(f"STOPPED: {e}", err=True)
        sys.exit(1)
    except KeyboardInterrupt:
        log("Interrupted. Progress is saved; run the same command to continue.", err=True)
        sys.exit(1)
    except Exception as e:
        log(f"CRASHED: {e.__class__.__name__}: {e}", err=True)
        raise


if __name__ == "__main__":
    main()
