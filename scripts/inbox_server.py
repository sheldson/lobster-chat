#!/usr/bin/env python3
import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
INBOX = DATA / "inbox.jsonl"
STATE = DATA / "state.json"

# Intents allowed from non-active peers (protocol handshake messages)
HANDSHAKE_INTENTS = {"friend_request", "friend_accepted", "friend_rejected"}

MAX_BODY_BYTES = 64 * 1024


def load_state():
    if not STATE.exists():
        return {"me": None, "peers": {}}
    return json.loads(STATE.read_text())


class H(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_POST(self):
        if self.path != "/lobster/inbox":
            self.send_response(404)
            self.end_headers()
            return
        n = int(self.headers.get("Content-Length", "0"))
        if n > MAX_BODY_BYTES:
            return self._json(413, {"ok": False, "error": "payload_too_large"})
        raw = self.rfile.read(n)
        try:
            msg = json.loads(raw.decode("utf-8"))
        except Exception:
            return self._json(400, {"ok": False, "error": "bad_json"})

        st = load_state()
        me = st.get("me")
        if not me:
            return self._json(503, {"ok": False, "error": "not_initialized"})

        frm = msg.get("from")
        intent = msg.get("intent", "")
        peers = st.get("peers", {})

        # Allow handshake intents from unknown peers; require active status for all else
        if intent not in HANDSHAKE_INTENTS:
            if frm not in peers or peers[frm].get("status") != "active":
                return self._json(403, {"ok": False, "error": "peer_not_active"})

        DATA.mkdir(parents=True, exist_ok=True)
        with INBOX.open("a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self._json(200, {"ok": True, "stored": True})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args()
    srv = HTTPServer((args.host, args.port), H)
    print(f"Lobster inbox server listening on http://{args.host}:{args.port}/lobster/inbox")
    srv.serve_forever()


if __name__ == "__main__":
    main()
