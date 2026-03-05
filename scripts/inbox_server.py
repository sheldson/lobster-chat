#!/usr/bin/env python3
import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
INBOX = DATA / "inbox.jsonl"
STATE = DATA / "state.json"


def load_state():
    if not STATE.exists():
        return {"me": None, "peers": {}}
    return json.loads(STATE.read_text())


class H(BaseHTTPRequestHandler):
    def _ok(self, obj):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_POST(self):
        if self.path != "/lobster/inbox":
            self.send_response(404)
            self.end_headers()
            return
        n = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(n)
        try:
            msg = json.loads(raw.decode("utf-8"))
        except Exception:
            self.send_response(400)
            self.end_headers()
            return

        st = load_state()
        me = st.get("me")
        peers = st.get("peers", {})
        if not me:
            self._ok({"ok": False, "error": "not_initialized"})
            return

        frm = msg.get("from")
        if frm not in peers or peers[frm].get("status") != "active":
            self._ok({"ok": False, "error": "peer_not_active"})
            return

        DATA.mkdir(parents=True, exist_ok=True)
        with INBOX.open("a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self._ok({"ok": True, "stored": True})


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
