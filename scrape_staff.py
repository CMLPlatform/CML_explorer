"""Scrape every CML staff profile (all roles, incl. PhD candidates and postdocs).

Replaces cells 1-2 of cml_network.ipynb, which only took professors.
Writes leiden_staff.json: one record per person with name, title, keywords, bio (summary),
projects, organisation, photo, ORCID, department and a harmonized position.

Usage:  python3 scrape_staff.py           (about 5 minutes; 1 s pause between profiles)
Needs:  requests, beautifulsoup4
"""
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

HERE = Path(__file__).resolve().parent
BASE_URL = "https://www.universiteitleiden.nl"
STAFF_URL = f"{BASE_URL}/en/science/environmental-sciences/staff_alphabetically"
OUTPUT_FILE = HERE / "leiden_staff.json"
DELAY_SECONDS = 1.0  # be polite and avoid triggering the site's bot protection

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en",
})
NOISE = re.compile(r"^(more information about|more research|publications|activities)\b", re.I)

# Support roles are scraped too (they are on the page) but marked, so the dashboard can leave them out.
SUPPORT = re.compile(r"co[oö]rdinator|study advis|office manager|technician|support officer|financial controller|"
                     r"board secretary|director of operations|internship labs|grant advisor|project manager|"
                     r"program(me)? manager|student affairs|team leader|visitor centre", re.I)

# Manual corrections for cases the scraped data cannot resolve (carried over from the notebook)
RANK_OVERRIDES = {
    "Glenn Aguilar Hernandez": "Assistant professor",
    "Stefano Cucurachi": "Associate professor",
}
FIELD_OVERRIDES = {"Jan Willem Erisman": "environmental_biology"}


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def fetch_page(url):
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    if "Access blocked" in r.text[:3000]:
        raise RuntimeError(f"Request blocked by the site's protection system: {url}")
    return r.text


def staff_links(html):
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for item in soup.select("ul.table-list li"):
        a = item.find("a", href=True)
        if not a:
            continue
        url = urljoin(BASE_URL, a["href"])
        if url in seen:
            continue
        seen.add(url)
        meta = item.find("span", class_="meta")
        out.append((url, clean(meta.get_text()) if meta else ""))
    return out


def position(role, name):
    """Small, consistent set of positions for filtering."""
    if name in RANK_OVERRIDES:
        return RANK_OVERRIDES[name]
    t = role.lower()
    if SUPPORT.search(t):
        return "Support staff"
    if "emerit" in t:
        return "Professor emeritus"
    if "special appointment" in t:
        return "Professor by special appointment"
    if "assistant prof" in t:
        return "Assistant professor"
    if "associate prof" in t:
        return "Associate professor"
    if "professor" in t or "scientific director" in t:
        return "Professor"
    if re.search(r"post-?doc", t):
        return "Postdoc"
    if re.search(r"\bphd\b", t):
        return "PhD candidate"
    if "lecturer" in t:
        return "Lecturer"
    return "Researcher"


def department(units):
    """Department from the unit lines on a profile ('CML', 'Industriële Ecologie', ...)."""
    text = " ".join(u for role in units for u in role).lower()
    if "industri" in text:
        return "industrial_ecology"
    if "environmental biology" in text or "ecotox" in text:
        return "environmental_biology"
    return ""   # institute-wide role or no unit: filled in later from co-authorship (build step)


def extract_profile(url, list_role):
    html = fetch_page(url)
    soup = BeautifulSoup(html, "html.parser")
    name_tag = soup.select_one("article.profile h1") or soup.select_one("section.basics h1")
    name = clean(name_tag.get_text()) if name_tag else ""
    context = soup.select_one("article.profile p.context")
    title = clean(context.get_text()) if context else ""

    keywords = [clean(li.get_text()) for li in soup.select("article.profile ul.tags li") if clean(li.get_text())]
    if not keywords:
        meta_kw = soup.find("meta", attrs={"name": "keywords"})
        if meta_kw and meta_kw.get("content"):
            keywords = [clean(k) for k in meta_kw["content"].split(",") if clean(k)]

    summary_parts = []
    for section in soup.select("article.profile section.tab"):
        if section.get("data-tab-label") not in ("Overview", "Profile"):
            continue
        for p in section.find_all("p"):
            if p.find_parent(class_="box"):
                continue
            text = clean(p.get_text())
            if text and text != title and not NOISE.match(text) and text not in summary_parts:
                summary_parts.append(text)

    projects, organisation = [], []
    for box in soup.select("article.profile div.box"):
        heading = box.find("h2")
        box_title = heading.get_text().strip().lower() if heading else ""
        if "research" in box_title:
            for li in box.select("li"):
                a = li.find("a", href=True)
                if not a:
                    continue
                text = clean((li.find("strong") or li.find("p") or li).get_text())
                purl = urljoin(BASE_URL, a["href"])
                if text and all(p["url"] != purl for p in projects):
                    projects.append({"title": text, "url": purl})
        elif box_title == "organisation":
            organisation = [clean(li.get_text()) for li in box.select("li") if clean(li.get_text())]

    photo = soup.select_one("figure.photo img")
    units = [[clean(li.get_text()) for li in (r.select_one(".col").select("li") if r.select_one(".col") else [])
              if clean(li.get_text())] for r in soup.select(".role")]
    orcid = re.search(r"orcid\.org/(\d{4}-\d{4}-\d{4}-\d{3}[\dX])", html)
    name = name or url.rsplit("/", 1)[-1].replace("-", " ").title()
    return {
        "name": name,
        "title": title or list_role,
        "list_role": list_role,
        "rank": position(list_role or title, name),
        "field": FIELD_OVERRIDES.get(name) or department(units),
        "keywords": keywords,
        "organisation": organisation,
        "summary": " ".join(summary_parts),
        "projects": projects,
        "photo_url": urljoin(BASE_URL, photo["src"]) if photo and photo.get("src") else "",
        "profile_url": url,
        "orcid": orcid.group(1) if orcid else "",
        "units": units,
    }


def main():
    links = staff_links(fetch_page(STAFF_URL))
    if not links:
        raise RuntimeError("No staff links found - the page structure may have changed.")
    print(f"Found {len(links)} profiles")
    profiles = []
    for i, (url, role) in enumerate(links, 1):
        try:
            p = extract_profile(url, role)
            profiles.append(p)
            print(f"  {i}/{len(links)} {p['name']}: {p['rank']}, {p['field'] or '-'}, {len(p['keywords'])} keywords")
        except Exception as exc:
            print(f"  {i}/{len(links)} skipped {url} ({exc})")
        time.sleep(DELAY_SECONDS)
    profiles.sort(key=lambda p: p["name"].split()[-1].lower())
    OUTPUT_FILE.write_text(json.dumps(profiles, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(profiles)} profiles -> {OUTPUT_FILE.name}")


if __name__ == "__main__":
    main()
