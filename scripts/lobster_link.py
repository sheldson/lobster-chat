#!/usr/bin/env python3
import argparse
import base64
import datetime as dt
import hashlib
import hmac
import json
import sys
import uuid
from pathlib import Path
from urllib import request, parse

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
STATE = DATA / "state.json"
INBOX = DATA / "inbox.jsonl"
OUTBOX = DATA / "outbox.jsonl"
PENDING = DATA / "pending_shares.json"


def now_iso():
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def ensure_files():
    DATA.mkdir(parents=True, exist_ok=True)
    if not STATE.exists():
        STATE.write_text(json.dumps({"me": None, "peers": {}}, ensure_ascii=False, indent=2))
    if not INBOX.exists():
        INBOX.write_text("")
    if not OUTBOX.exists():
        OUTBOX.write_text("")
    if not PENDING.exists():
        PENDING.write_text(json.dumps({"requests": []}, ensure_ascii=False, indent=2))


def load_state():
    ensure_files()
    return json.loads(STATE.read_text())


def save_state(s):
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=2))


def append_jsonl(path, obj):
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def sign(me_secret, payload_obj):
    msg = json.dumps(payload_obj, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hmac.new(me_secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def post_json(url, payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=12) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw else {"ok": True}


def get_json(url):
    with request.urlopen(url, timeout=12) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def register_relay(me):
    if not me.get("relay_url"):
        return
    url = me["relay_url"].rstrip("/") + "/register"
    post_json(url, {"lobster_id": me["lobster_id"], "name": me["name"]})


def cmd_init(args):
    s = load_state()
    if s.get("me") and not args.force:
        print("Already initialized. Use --force to reset.")
        return 1
    secret = uuid.uuid4().hex + uuid.uuid4().hex
    me = {
        "lobster_id": str(uuid.uuid4()),
        "name": args.name,
        "endpoint": args.endpoint,
        "relay_url": args.relay_url,
        "secret": secret,
        "created_at": now_iso(),
    }
    s["me"] = me
    s["peers"] = {}
    save_state(s)
    try:
        register_relay(me)
    except Exception:
        pass
    print(json.dumps({"ok": True, "lobster_id": me["lobster_id"], "name": me["name"], "relay_url": me.get("relay_url")}, ensure_ascii=False))
    return 0


def public_qr_payload(s):
    me = s.get("me")
    if not me:
        raise SystemExit("Not initialized. Run init first.")
    return {
        "v": 1,
        "lobster_id": me["lobster_id"],
        "name": me["name"],
        "endpoint": me.get("endpoint"),
        "relay_url": me.get("relay_url"),
        "public_key": "mvp-no-ed25519",
    }


def encode_qr_token(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    token = base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")
    return "lobster://v1/" + token


def decode_qr_input(s: str) -> dict:
    s = s.strip()
    if s.startswith("lobster://v1/"):
        token = s.split("lobster://v1/", 1)[1]
        token += "=" * ((4 - len(token) % 4) % 4)
        raw = base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8")
        return json.loads(raw)
    return json.loads(s)


def cmd_qr(args):
    s = load_state()
    payload = public_qr_payload(s)
    text = json.dumps(payload, ensure_ascii=False)
    token = encode_qr_token(payload)

    if args.png_out:
        qr_url = "https://quickchart.io/qr?size=900&text=" + parse.quote(token)
        out_path = Path(args.png_out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        request.urlretrieve(qr_url, str(out_path))
        print(json.dumps({"ok": True, "png": str(out_path), "qr_text": token, "payload": payload}, ensure_ascii=False))
        return 0

    if args.format == "text":
        print(token)
    else:
        print(json.dumps({"qr_text": token, "payload": payload}, ensure_ascii=False, indent=2))
    return 0


def cmd_add_peer(args):
    s = load_state()
    me = s.get("me")
    if not me:
        raise SystemExit("Not initialized.")
    p = decode_qr_input(args.qr)
    pid = p["lobster_id"]
    if pid == me["lobster_id"]:
        raise SystemExit("Cannot add self.")
    s["peers"][pid] = {
        "lobster_id": pid,
        "name": p.get("name", args.label or "peer"),
        "endpoint": p.get("endpoint"),
        "relay_url": p.get("relay_url"),
        "status": "active",
        "created_at": now_iso(),
    }
    save_state(s)
    print(json.dumps({"ok": True, "peer": s["peers"][pid]}, ensure_ascii=False))
    return 0


def build_envelope(s, to, intent, body):
    me = s["me"]
    payload = {
        "id": str(uuid.uuid4()),
        "ts": now_iso(),
        "from": me["lobster_id"],
        "to": to,
        "intent": intent,
        "body": body,
    }
    payload["sig"] = sign(me["secret"], payload)
    return payload


def deliver_to_peer(peer, envelope):
    if peer.get("relay_url"):
        url = peer["relay_url"].rstrip("/") + "/send"
        return post_json(url, {"envelope": envelope})
    if peer.get("endpoint"):
        return post_json(peer["endpoint"], envelope)
    return {"ok": True, "delivery": "queued_only"}


def cmd_send(args):
    s = load_state()
    peer = s["peers"].get(args.to)
    if not peer or peer.get("status") != "active":
        raise SystemExit("Peer not active.")
    env = build_envelope(s, args.to, args.intent, {"text": args.text})
    append_jsonl(OUTBOX, env)
    try:
        r = deliver_to_peer(peer, env)
        print(json.dumps({"ok": True, "delivery": "sent", "resp": r}, ensure_ascii=False))
        return 0
    except Exception as e:
        print(json.dumps({"ok": False, "delivery": "failed", "error": str(e), "saved_outbox": True}, ensure_ascii=False))
        return 2


def cmd_pull(_args):
    s = load_state()
    me = s.get("me")
    if not me or not me.get("relay_url"):
        raise SystemExit("relay_url not configured")
    q = parse.urlencode({"lobster_id": me["lobster_id"]})
    url = me["relay_url"].rstrip("/") + f"/pull?{q}"
    resp = get_json(url)
    msgs = resp.get("messages", [])
    for m in msgs:
        append_jsonl(INBOX, m)
    print(json.dumps({"ok": True, "pulled": len(msgs)}, ensure_ascii=False))
    return 0


def cmd_history(_args):
    ensure_files()
    print("== INBOX ==")
    print(INBOX.read_text()[-6000:])
    print("== OUTBOX ==")
    print(OUTBOX.read_text()[-6000:])
    return 0


def cmd_disconnect(args):
    s = load_state()
    p = s["peers"].get(args.peer)
    if not p:
        raise SystemExit("Peer not found")
    p["status"] = "blocked"
    p["blocked_at"] = now_iso()
    save_state(s)
    print(json.dumps({"ok": True, "peer": args.peer, "status": "blocked"}, ensure_ascii=False))
    return 0


def cmd_pending(_args):
    ensure_files()
    print(PENDING.read_text())
    return 0


def cmd_share_request(args):
    s = load_state()
    peer = s["peers"].get(args.to)
    if not peer or peer.get("status") != "active":
        raise SystemExit("Peer not active")
    req_id = str(uuid.uuid4())
    pending = json.loads(PENDING.read_text())
    item = {
        "request_id": req_id,
        "to": args.to,
        "kind": args.kind,
        "title": args.title,
        "content": args.content,
        "status": "awaiting_owner_approval",
        "created_at": now_iso(),
    }
    pending["requests"].append(item)
    PENDING.write_text(json.dumps(pending, ensure_ascii=False, indent=2))
    print(json.dumps({"ok": True, "request_id": req_id, "status": item["status"]}, ensure_ascii=False))
    return 0


def cmd_share_approve(args):
    s = load_state()
    pending = json.loads(PENDING.read_text())
    target = None
    for r in pending["requests"]:
        if r["request_id"] == args.request:
            target = r
            break
    if not target:
        raise SystemExit("Request not found")
    if target["status"] != "awaiting_owner_approval":
        raise SystemExit("Request not approvable")
    target["status"] = "approved"
    target["approved_at"] = now_iso()
    PENDING.write_text(json.dumps(pending, ensure_ascii=False, indent=2))
    env = build_envelope(s, target["to"], "share_approved", {
        "kind": target["kind"],
        "title": target["title"],
        "content": target["content"],
    })
    append_jsonl(OUTBOX, env)
    peer = s["peers"].get(target["to"])
    if peer:
        try:
            deliver_to_peer(peer, env)
        except Exception:
            pass
    print(json.dumps({"ok": True, "sent": True, "request_id": args.request}, ensure_ascii=False))
    return 0


def cmd_list_peers(_args):
    s = load_state()
    print(json.dumps(s.get("peers", {}), ensure_ascii=False, indent=2))
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init")
    p.add_argument("--name", required=True)
    p.add_argument("--endpoint", required=False, default="")
    p.add_argument("--relay-url", required=False, default="")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("qr")
    p.add_argument("--format", choices=["text", "pretty"], default="pretty")
    p.add_argument("--png-out", help="Write QR image PNG to this path (one-step shareable output)")
    p.set_defaults(fn=cmd_qr)

    p = sub.add_parser("add-peer")
    p.add_argument("--qr", required=True, help="QR payload JSON text")
    p.add_argument("--label")
    p.set_defaults(fn=cmd_add_peer)

    p = sub.add_parser("send")
    p.add_argument("--to", required=True)
    p.add_argument("--intent", default="ask")
    p.add_argument("--text", required=True)
    p.set_defaults(fn=cmd_send)

    p = sub.add_parser("pull")
    p.set_defaults(fn=cmd_pull)

    p = sub.add_parser("history")
    p.set_defaults(fn=cmd_history)

    p = sub.add_parser("disconnect")
    p.add_argument("--peer", required=True)
    p.set_defaults(fn=cmd_disconnect)

    p = sub.add_parser("pending")
    p.set_defaults(fn=cmd_pending)

    p = sub.add_parser("share-request")
    p.add_argument("--to", required=True)
    p.add_argument("--kind", choices=["skill", "code"], required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--content", default="")
    p.set_defaults(fn=cmd_share_request)

    p = sub.add_parser("share-approve")
    p.add_argument("--request", required=True)
    p.set_defaults(fn=cmd_share_approve)

    p = sub.add_parser("list-peers")
    p.set_defaults(fn=cmd_list_peers)

    args = ap.parse_args()
    rc = args.fn(args)
    sys.exit(rc if isinstance(rc, int) else 0)


if __name__ == "__main__":
    main()
