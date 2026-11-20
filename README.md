# CML network dashboard

An interactive map of the research staff of the Institute of Environmental Sciences (CML), Leiden University: the Industrial Ecology and Environmental Biology departments, from professors to PhD candidates. It shows who works on what, with which methods, with whom inside CML, and with which institutions and countries outside it.

Open `dashboard.html` (or its copy `index.html`) in a browser. It is a single self-contained file (data embedded, no server, no external libraries).

**Online**: GitHub Pages serves `index.html` from this repository ([CMLPlatform/CML_explorer](https://github.com/CMLPlatform/CML_explorer)). GitHub Pages only serves files, so it cannot run the assistant. On the online version, **Ask** calls the assistant on a Hugging Face Space that runs `serve.py` (see `deploy_space.py`). GitHub secrets are not used: the API key is a secret of the Space.

## What the dashboard shows

Four views, chosen at the top:

| View | What it shows |
|---|---|
| **Themes map** | People as dots, grouped by **CML theme** (Biodiversity, Future food, Resources, Urban, Water), **field cluster** or **method cluster**. **Lines** show either shared topics or **co-authorship** (papers written together at Leiden). Click a person for their card: themes, keywords, bio, projects, and publications (paper counts, CML co-authors, external partners, recent papers). |
| **External partners** | A world map and tables of the institutions and countries CML publishes with, counted over joint papers written at Leiden. Click a country or institution to see which CML people work with it. |
| **Find expertise** | Type a topic (for example "critical raw materials") to rank people by how often it appears in their profile keywords, paper keywords, paper titles and bio. |
| **Ask** | Chat with a research assistant that searches CML's papers and answers with numbered citations, naming the people behind the work. From a person's card, **Ask about their work** limits the conversation to that person. Needs `serve.py` (below). |

The filters on the left (search, CML theme, department, position, harmonized tags, original keywords) apply to all views. The URL hash stores the current view, so **Copy link** shares it.

## How it is built

```
scrape_staff.py      -> leiden_staff.json          all staff profiles: keywords, bio, photo, department, ORCID, position
harvest_openalex.py  -> data/people.csv, works.jsonl, ...   OpenAlex publications per person (needs an API key)
build_network.py     -> data/network.json, pub_texts.json   co-authorship, partners, papers per person
harmonize.py                                        keyword canonicalisation + theme assignment (used by the build)
build_dashboard.py   -> dashboard.html, index.html, tag_report.md, data/people.json (+ cml-network-site/index.html if present)
build_index.py       -> data/knowledge.db           searchable paper + people database for the assistant
assistant.py, serve.py                              the assistant and a local server for dashboard + assistant
```

- **[scrape_staff.py](scrape_staff.py)**: scrapes every profile on the CML staff page (replaces the first two notebook cells, which only took professors). Support staff are kept in the file but left out of the dashboard. Departments come from the profile; people listed only under institute-wide units get the department of their CML co-authors, or else the one their keywords point to (listed in `tag_report.md`).
- **[harvest_openalex.py](harvest_openalex.py)**: matches each person to an OpenAlex author (ORCID first, otherwise name search among Leiden authors) and downloads their papers since 2010. Saves progress per person, resumes after interruptions, logs to `data/harvest.log`. Put `OPENALEX_API_KEY=...` in a `.env` file next to the script or one folder up (free key from openalex.org).
- **[build_network.py](build_network.py)**: decides which papers count for whom (all papers for ORCID matches; only Leiden-affiliated papers for name matches, to avoid namesakes), builds the CML co-author links and the partner institutions and countries. `data/match_review.csv` lists matches worth checking.
- **[harmonize.py](harmonize.py)**: three layers. Aliases merge keyword variants and drop generic ones; every canonical keyword maps to one or more fine-grained themes; bio text back-fills people with few keywords. Paper titles and keywords are a fourth, lower-weight layer that mostly helps PhD candidates with thin profiles. The fine themes are then scored into the five CML themes (`CML_WEIGHTS`, `CML_KEYWORDS`, `CML_TEXT`, `CML_PUB_WEIGHT`). Manual corrections live in `CML_OVERRIDES`. Keywords added for the full staff list are in a separate block for review.
- **[build_dashboard.py](build_dashboard.py)**: merges profiles and publications, runs the theme assignment, embeds everything in [dashboard_template.html](dashboard_template.html), writes `dashboard.html`, copies it to `index.html` (the GitHub Pages entry point) and, if the folder exists, to `cml-network-site/index.html`, and writes `tag_report.md` (tag quality checks, departments that were inferred, keywords without a theme mapping).
- **cml-network-site/**: an optional local clone of a separate repository (CMLPlatform/senior_staff_dashboard). This repository does not track it (see `.gitignore`). `build_dashboard.py` updates its `index.html` only when the folder exists.
- **[world_paths.json](world_paths.json)**: country outlines for the partner map, pre-projected (Natural Earth projection) from Natural Earth 1:110m data (public domain).

- **[build_index.py](build_index.py)**: SQLite database with people, papers (title, abstract, keywords, topics), authorships and partners, plus full-text (BM25) indexes.
- **[assistant.py](assistant.py)**: answers questions from that database with numbered citations, and drops citations it cannot match to a real paper. Two modes: **retrieve** (the code runs the searches: experts, papers, people and countries named in the question; the model only writes the answer; reliable with small models) and **agent** (the model chooses its own searches with five tools; better for complex questions, needs a model that is good at tool calling). Try it from the terminal: `python3 assistant.py "Who works on pollinators?"`.
- **[serve.py](serve.py)**: serves `dashboard.html` and the assistant at http://localhost:8765 (this computer only; `--host 0.0.0.0` shares it on the network, agree hosting with IT first). Online, it also accepts calls from the pages in `ALLOWED_ORIGINS` and limits questions per visitor per hour (`HOURLY_LIMIT`) and per day in total (`DAILY_LIMIT`).
- **[deploy_space.py](deploy_space.py)**: publishes the assistant as a Hugging Face Space (Docker, files in [space/](space/)). It uploads `serve.py`, `assistant.py`, `dashboard.html` and `data/knowledge.db`, and copies the model settings and API key from `.env` to the Space. `ASSISTANT_URL` in `build_dashboard.py` tells the online dashboard where the Space is. Run it again after `build_index.py` to update the Space.

### Choosing the language model

Settings go in `.env` (next to the scripts or one folder up).

| | Local open-weight model (default) | Claude |
|---|---|---|
| Settings | `LLM_PROVIDER=local`<br>`LLM_BASE_URL=http://localhost:1234/v1` (LM Studio; Ollama: `http://localhost:11434/v1`)<br>`LLM_MODEL=` name as listed by the server (empty = first loaded model) | `LLM_PROVIDER=anthropic`<br>`ANTHROPIC_API_KEY=...`<br>`LLM_MODEL=claude-sonnet-5-5` (default) |
| Default mode | retrieve | agent |
| Data | stays on the computer | paper abstracts and metadata (public) go to the API |
| Trade-off | free and private; weaker at long syntheses | stronger answers; API costs |

**Mistral API**: `LLM_PROVIDER=mistral`, `MISTRAL_API_KEY=...`, optionally `LLM_MODEL=mistral-medium-latest` (default; `mistral-small-latest` is cheaper, `mistral-large-latest` stronger). Default mode: agent.

**Hugging Face Inference Providers**: `LLM_PROVIDER=hf`, `HF_TOKEN=...` (a fine-grained token with the permission "Make calls to Inference Providers"), optionally `LLM_MODEL=openai/gpt-oss-120b:nebius` (default) and `LLM_FALLBACK_MODEL=openai/gpt-oss-120b:ovhcloud`. The suffix after `:` chooses the provider. Nebius (EU company) is fast. OVHcloud (servers in France) is slower; the assistant uses the fallback model only when the first model fails. Costs come from the Hugging Face account's monthly credit. Default mode: agent.

`LLM_MODE=retrieve` or `LLM_MODE=agent` overrides the default. For local models, set the context length to at least 8192 tokens in LM Studio or Ollama; one question sends about 2,000-3,000 tokens of search results. Any OpenAI-compatible service works with `LLM_BASE_URL` and `LLM_API_KEY`, for example a model hosted by the university.

### Rebuild

```bash
pip install -r requirements.txt   # anthropic is only needed with LLM_PROVIDER=anthropic
python3 scrape_staff.py         # ~5 min; refresh profiles
python3 harvest_openalex.py     # ~20-30 min first time; resumes if interrupted
python3 build_network.py        # seconds
python3 build_dashboard.py      # seconds; add --strict to stop on unmapped keywords
python3 build_index.py          # seconds; the assistant's database
python3 serve.py                # dashboard + assistant at http://localhost:8765 (start LM Studio / Ollama first)
```

After you edit `harmonize.py` or the template, run `build_dashboard.py` again (and `build_index.py` and `serve.py` for the assistant). Commit and push `index.html` to update the online version. Without `data/network.json` the dashboard still builds, without publications.

## Adjusting the groupings

- **CML theme of a person**: add or edit an entry in `CML_OVERRIDES` in `harmonize.py`.
- **New profile keywords**: `tag_report.md` lists keywords with no theme mapping; add them to `KEYWORD_THEMES` (or `ALIASES` / `DROP`).
- **Methods of a person**: add or edit an entry in `METHOD_OVERRIDES` in the template (first = main method).
- **Field cluster names**: edit `FIELD_NAMES` in the template.
- **Wrong publication match**: add the person's ORCID to their university profile and rerun the harvest; `data/match_review.csv` explains each flagged match.

## Data and licence

The code in this repository is released under the [MIT License](LICENSE).

The staff profile data (`leiden_staff.json`, `leiden_professors*.json`, `run_results.json` and the data embedded in the dashboard) was collected from public Leiden University staff pages; publication data comes from OpenAlex (CC0). The profile data remains the property of its original authors and Leiden University and is **not** covered by the MIT licence. Theme, method and department assignments are automated and partly hand-corrected; they are interpretations, not official affiliations. Check with the people concerned and with Leiden University before publishing the data or dashboard outside the institute.
