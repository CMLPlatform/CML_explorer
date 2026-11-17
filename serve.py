"""Serve the dashboard with the research assistant on your own computer.

    python3 serve.py            then open http://localhost:8765

Serves dashboard.html and answers its "Ask" view through /api/ask (assistant.py). Standard library only, apart
from the anthropic package (only needed with LLM_PROVIDER=anthropic). Model settings: see assistant.py. Listens on this computer only (127.0.0.1) by default; to let
colleagues on the network use it, run with --host 0.0.0.0, but only after agreeing on hosting with IT.
"""
import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import assistant

HERE = Path(__file__).resolve().parent
PAGE = HERE / "dashboard.html"
_client = None
_lock = threading.Lock()


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
        self.end_headers()
        self.wfile.write(data)

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
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            msgs = [m for m in body.get("messages", []) if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)]
            if not msgs or msgs[-1]["role"] != "user":
                return self._send(400, {"error": "send a list of messages ending with the user's question"})
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
