"""Serve the dashboard with the research assistant on your own computer.

    python3 serve.py            then open http://localhost:8765

Serves dashboard.html and answers its "Ask" view through /api/ask (assistant.py). Standard library only, apart
from the anthropic package (only needed with LLM_PROVIDER=anthropic). Model settings: see assistant.py. Listens on this computer only (127.0.0.1) by default; to let
colleagues on the network use it, run with --host 0.0.0.0, but only after agreeing on hosting with IT.

Online use (Hugging Face Space, see deploy_space.py and space/Dockerfile) takes these settings from the environment:
    ALLOWED_ORIGINS   web pages on other sites that may call the API, comma-separated (e.g. https://cmlplatform.github.io)
    HOURLY_LIMIT      questions per visitor (IP address) per hour; 0 = no limit (default)
    DAILY_LIMIT       questions per day for all visitors together; 0 = no limit (default)
"""
import argparse
import json
import sys
import os
import threading
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import assistant

HERE = Path(__file__).resolve().parent
PAGE = HERE / "dashboard.html"
_client = None
_lock = threading.Lock()
ALLOWED_ORIGINS = {o.strip().rstrip("/") for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()}
HOURLY_LIMIT = int(os.environ.get("HOURLY_LIMIT") or 0)
DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT") or 0)
MAX_BODY = 200_000        # bytes per request
MAX_QUESTION = 2_000      # characters per question
_recent = defaultdict(deque)   # IP address -> times of its questions in the last hour
_day = {"date": "", "n": 0}


def over_limit(ip):
    """A message for the visitor when a limit is reached, otherwise "" (and the question is counted)."""
    now = time.time()
    with _lock:
        today = time.strftime("%Y-%m-%d", time.gmtime(now))
        if _day["date"] != today:
            _day.update(date=today, n=0)
        if DAILY_LIMIT and _day["n"] >= DAILY_LIMIT:
            return "The assistant has answered its maximum number of questions for today. Try again tomorrow."
        q = _recent[ip]
        while q and now - q[0] > 3600:
            q.popleft()
        if HOURLY_LIMIT and len(q) >= HOURLY_LIMIT:
            return f"You can ask {HOURLY_LIMIT} questions per hour. Try again later."
        q.append(now)
        _day["n"] += 1
        if len(_recent) > 10_000:   # forget visitors with no questions in the last hour
            for k in [k for k, v in _recent.items() if not v or now - v[-1] > 3600]:
                del _recent[k]
        return ""


def client():
    """The Anthropic client (only used with LLM_PROVIDER=anthropic; local models are called over HTTP)."""
    global _client
    if assistant.PROVIDER != "anthropic":
        return None
    with _lock:
        if _client is None:
            import anthropic
            _client = anthropic.Anthropic()
        return _client


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if "json" in ctype or "html" in ctype else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _cors(self):
        """Let the pages in ALLOWED_ORIGINS (e.g. the dashboard on GitHub Pages) call the API from the browser."""
        origin = (self.headers.get("Origin") or "").rstrip("/")
        if origin and origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _ip(self):
        """The visitor's IP address. Behind a proxy (Hugging Face) it is the first one in X-Forwarded-For."""
        fwd = self.headers.get("X-Forwarded-For", "")
        return fwd.split(",")[0].strip() if fwd else self.client_address[0]

    def do_OPTIONS(self):
        """The browser asks this before a cross-site POST with a JSON body."""
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0].split("#")[0]
        if path in ("/", "/index.html", "/dashboard.html"):
            if not PAGE.exists():
                return self._send(404, b"dashboard.html not found: run build_dashboard.py", "text/plain")
            return self._send(200, PAGE.read_bytes(), "text/html")
        if path == "/api/health":
            return self._send(200, {"ok": True, **assistant.status()})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/api/ask":
            return self._send(404, {"error": "not found"})
        try:
            size = int(self.headers.get("Content-Length", 0))
            if size > MAX_BODY:
                return self._send(413, {"error": "The conversation is too long. Reload the page to start a new one."})
            body = json.loads(self.rfile.read(size) or b"{}")
            msgs = [m for m in body.get("messages", []) if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)]
            if not msgs or msgs[-1]["role"] != "user":
                return self._send(400, {"error": "send a list of messages ending with the user's question"})
            if len(msgs[-1]["content"]) > MAX_QUESTION:
                return self._send(400, {"error": f"Ask a shorter question (at most {MAX_QUESTION} characters)."})
            limit = over_limit(self._ip())
            if limit:
                return self._send(429, {"error": limit})
            out = assistant.ask(msgs[-12:], person_id=body.get("person_id"), db=assistant.connect(), client=client())
            self._send(200, out)
        except Exception as e:   # report the problem to the page instead of dropping the connection
            name = type(e).__name__
            hint = (" Check ANTHROPIC_API_KEY in .env." if "Authentication" in name or "api_key" in str(e)
                    else f" Is the local model server running at {assistant.BASE_URL} with a model loaded?" if name in ("URLError", "ConnectionRefusedError", "RuntimeError")
                    else " The local model may be too slow or its context too short: in LM Studio set the context length to 8192 or more." if name in ("TimeoutError", "HTTPError")
                    else "")
            self._send(500, {"error": f"{name}: {e}.{hint}"})

    def log_message(self, fmt, *args):
        if "/api/ask" in (args[0] if args else ""):
            sys.stderr.write("%s  %s\n" % (self.log_date_time_string(), fmt % args))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    assistant.connect().close()   # fail early if the database is missing
    st = assistant.status()
    print(f"Assistant: {st['provider']} · {st['model'] or '?'} · {st['mode']} mode" + (f"   (problem: {st['problem']})" if st["problem"] else ""))
    print(f"CML network dashboard with assistant: http://localhost:{a.port}   (Ctrl+C to stop)")
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
