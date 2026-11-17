"""Publish the research assistant as a Hugging Face Space, so that the Ask view works on GitHub Pages.

GitHub Pages only serves files: it cannot run serve.py, and GitHub secrets never reach the page. The Space runs
serve.py; the dashboard on GitHub Pages calls it (build_dashboard.py writes the Space address into the page).

Usage
    pip install huggingface_hub
    hf auth login                       (a token with "write" access, from huggingface.co/settings/tokens)
    python3 deploy_space.py [--space OWNER/NAME] [--no-secrets]

What it does
    1. Makes the Space (Docker, public) if it does not exist.
    2. Uploads space/README.md, space/Dockerfile, serve.py, assistant.py, dashboard.html, data/knowledge.db.
    3. Copies the model settings from .env to the Space: LLM_PROVIDER, LLM_MODEL, LLM_MODE, LLM_BASE_URL as
       variables, and the API keys (MISTRAL_API_KEY, ANTHROPIC_API_KEY, LLM_API_KEY) as secrets.
       --no-secrets skips this step (then set them by hand in the Space settings).

The Space must be public, because visitors' browsers call it. Everyone can then download the uploaded files,
including data/knowledge.db (public OpenAlex metadata and public staff profiles). The API keys stay secret.
"""
import argparse
import os
import re
import sys
from pathlib import Path

import assistant   # reads .env into os.environ

HERE = Path(__file__).resolve().parent
DEFAULT_SPACE = "CMLPlatform/cml-assistant"
FILES = {   # path in the Space -> local file
    "README.md": HERE / "space" / "README.md",
    "Dockerfile": HERE / "space" / "Dockerfile",
    "serve.py": HERE / "serve.py",
    "assistant.py": HERE / "assistant.py",
    "dashboard.html": HERE / "dashboard.html",
    "data/knowledge.db": HERE / "data" / "knowledge.db",
}
VARIABLES = ("LLM_PROVIDER", "LLM_MODEL", "LLM_MODE", "LLM_BASE_URL")
SECRETS = ("MISTRAL_API_KEY", "ANTHROPIC_API_KEY", "LLM_API_KEY")


def space_url(repo_id):
    """The address where the Space answers, e.g. CMLPlatform/cml-assistant -> https://cmlplatform-cml-assistant.hf.space"""
    return "https://" + re.sub(r"[^a-z0-9]+", "-", repo_id.lower()).strip("-") + ".hf.space"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--space", default=DEFAULT_SPACE, help=f"OWNER/NAME of the Space (default {DEFAULT_SPACE})")
    ap.add_argument("--no-secrets", action="store_true", help="do not copy model settings and API keys from .env")
    a = ap.parse_args()

    try:
        from huggingface_hub import CommitOperationAdd, HfApi
    except ImportError:
        sys.exit("Install huggingface_hub first:  pip install huggingface_hub")

    missing = [str(p.relative_to(HERE)) for p in FILES.values() if not p.exists()]
    if missing:
        sys.exit(f"Missing: {', '.join(missing)}. Run build_dashboard.py and build_index.py first.")
    st = assistant.status()
    if not a.no_secrets and st["provider"] == "local":
        sys.exit("LLM_PROVIDER is 'local' in .env. The Space cannot reach a model on this computer: "
                 "set LLM_PROVIDER=mistral (or anthropic) and its API key in .env.")

    api = HfApi()
    try:
        user = api.whoami()["name"]
    except Exception:
        sys.exit("Not logged in to Hugging Face. Run:  hf auth login   (token with write access)")
    print(f"Hugging Face user: {user}. Space: {a.space}")

    api.create_repo(a.space, repo_type="space", space_sdk="docker", private=False, exist_ok=True)
    ops = [CommitOperationAdd(path_in_repo=dest, path_or_fileobj=str(src)) for dest, src in FILES.items()]
    api.create_commit(a.space, ops, repo_type="space", commit_message="Update the CML research assistant")
    print(f"Uploaded {len(ops)} files.")

    if not a.no_secrets:
        env = os.environ
        for key in VARIABLES:
            value = env.get(key, "")
            if value and not (key == "LLM_BASE_URL" and "localhost" in value):
                api.add_space_variable(a.space, key, value)
                print(f"  variable {key} = {value}")
        for key in SECRETS:
            if env.get(key):
                api.add_space_secret(a.space, key, env[key])
                print(f"  secret   {key} (value hidden)")

    url = space_url(a.space)
    print(f"\nSpace page: https://huggingface.co/spaces/{a.space}   (the first build takes a few minutes)")
    print(f"Check:      {url}/api/health")
    build = (HERE / "build_dashboard.py").read_text(encoding="utf-8")
    if url not in build:
        print(f"\nThe dashboard does not use this Space yet. Set ASSISTANT_URL in build_dashboard.py to\n    {url}\n"
              "then run build_dashboard.py and commit and push index.html.")


if __name__ == "__main__":
    main()
