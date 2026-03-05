#!/usr/bin/env python3
import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RELAY = DATA / "relay.json"


def load():
    DATA.mkdir(parents=True, exist_ok=True)
    if not RELAY.exists():
        RELAY.write_text(json.dumps({"clients": {}, "queues": {}}, ensure_ascii=False, indent=2))
    return json.loads(RELAY.read_text())


def save(db):
    RELAY.write_text(json.dumps(db, ensure_ascii=False, indent=2))


class H(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(n)
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            self._json(400, {"ok": False, "error": "bad_json"})
            return

        db = load()
        if self.path == "/register":
            lid = body.get("lobster_id")
            if not lid:
                return self._json(400, {"ok": False, "error": "missing_lobster_id"})
            db["clients"][lid] = {"name": body.get("name", "")}
            db["queues"].setdefault(lid, [])
            save(db)
            return self._json(200, {"ok": True})

        if self.path == "/send":
            env = body.get("envelope", body)
            to = env.get("to")
            if not to:
                return self._json(400, {"ok": False, "error": "missing_to"})
            db["queues"].setdefault(to, []).append(env)
            save(db)
            return self._json(200, {"ok": True, "queued_for": to})

        self._json(404, {"ok": False, "error": "not_found"})

    def do_GET(self):
        u = urlparse(self.path)
        if u.path != "/pull":
            return self._json(404, {"ok": False, "error": "not_found"})
        qs = parse_qs(u.query)
        lid = (qs.get("lobster_id") or [None])[0]
        if not lid:
            return self._json(400, {"ok": False, "error": "missing_lobster_id"})
        db = load()
        msgs = db["queues"].get(lid, [])
        db["queues"][lid] = []
        save(db)
        return self._json(200, {"ok": True, "messages": msgs})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8788)
    args = ap.parse_args()
    srv = HTTPServer((args.host, args.port), H)
    print(f"Relay listening at http://{args.host}:{args.port}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
