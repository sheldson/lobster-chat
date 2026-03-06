#!/usr/bin/env python3
"""
Tunnel helper — detect and launch a public tunnel for the lobster's inbox server.

Supports:
    1. ngrok (ngrok http 8787)
    2. Cloudflare Tunnel (cloudflared tunnel --url http://localhost:8787)

The lobster runs this at init to get a stable public URL.
If no tunnel tool is available, prints instructions for the owner.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from urllib import request as urllib_request


def detect_tunnel_tool() -> dict:
    """Detect which tunnel tool is available on this machine."""
    tools = []
    if shutil.which("ngrok"):
        tools.append("ngrok")
    if shutil.which("cloudflared"):
        tools.append("cloudflared")
    return {"available": tools}


def _wait_for_ngrok_url(timeout: int = 15) -> str:
    """Poll ngrok's local API for the public URL."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib_request.Request("http://127.0.0.1:4040/api/tunnels")
            with urllib_request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                for t in data.get("tunnels", []):
                    url = t.get("public_url", "")
                    if url.startswith("https://"):
                        return url
        except Exception:
            pass
        time.sleep(1)
    return ""


def start_ngrok(port: int = 8787) -> dict:
    """Start ngrok in the background and return the public URL."""
    try:
        proc = subprocess.Popen(
            ["ngrok", "http", str(port), "--log=stdout"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        public_url = _wait_for_ngrok_url()
        if not public_url:
            proc.terminate()
            return {"ok": False, "error": "ngrok started but no public URL found (check ngrok auth)"}
        return {"ok": True, "public_url": public_url, "pid": proc.pid, "tool": "ngrok"}
    except FileNotFoundError:
        return {"ok": False, "error": "ngrok not found"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def start_cloudflared(port: int = 8787) -> dict:
    """Start cloudflared quick tunnel and return the public URL."""
    try:
        proc = subprocess.Popen(
            ["cloudflared", "tunnel", "--url", f"http://localhost:{port}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        # cloudflared prints the URL to stderr/stdout
        deadline = time.time() + 20
        url = ""
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            # Look for the trycloudflare.com URL
            if ".trycloudflare.com" in line or "https://" in line:
                for word in line.split():
                    if word.startswith("https://") and ("trycloudflare.com" in word or "cloudflare" in word.lower()):
                        url = word.rstrip("/.,;")
                        break
                if url:
                    break
        if not url:
            proc.terminate()
            return {"ok": False, "error": "cloudflared started but no public URL found"}
        return {"ok": True, "public_url": url, "pid": proc.pid, "tool": "cloudflared"}
    except FileNotFoundError:
        return {"ok": False, "error": "cloudflared not found"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def start_tunnel(port: int = 8787, prefer: str = "") -> dict:
    """Auto-detect and start a tunnel. Returns {"ok": True, "public_url": "..."} or error.

    Args:
        port: Local port the inbox server listens on.
        prefer: Preferred tool ("ngrok" or "cloudflared"). Auto-detects if empty.
    """
    available = detect_tunnel_tool()["available"]
    if not available:
        return {
            "ok": False,
            "error": "no_tunnel_tool",
            "message": "No tunnel tool found. Install one of: ngrok (https://ngrok.com), cloudflared (https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)",
            "manual_option": "If you already have a public URL (VPS, port forwarding, etc.), pass it directly: --endpoint https://your-url/lobster/inbox",
        }

    tool = prefer if prefer in available else available[0]
    if tool == "ngrok":
        return start_ngrok(port)
    elif tool == "cloudflared":
        return start_cloudflared(port)
    return {"ok": False, "error": f"unknown tool: {tool}"}


def get_install_instructions() -> str:
    """Return human-readable install instructions for tunnel tools."""
    return """To make your lobster reachable from the internet, install ONE of these:

Option 1: ngrok (recommended, easiest)
    brew install ngrok    # macOS
    # or download from https://ngrok.com/download
    ngrok authtoken YOUR_TOKEN  # one-time setup, free account

Option 2: Cloudflare Tunnel (no account needed for quick tunnels)
    brew install cloudflared    # macOS
    # or download from https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/

Option 3: Manual
    If you have a VPS or can port-forward, just pass your public URL:
    python3 scripts/lobster_link.py init --name "my-lobster" --endpoint "https://your-server.com/lobster/inbox"
"""


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Tunnel helper for lobster inbox")
    ap.add_argument("command", choices=["detect", "start", "instructions"])
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--prefer", default="", help="Preferred tunnel tool")
    args = ap.parse_args()

    if args.command == "detect":
        print(json.dumps(detect_tunnel_tool(), indent=2))
    elif args.command == "start":
        result = start_tunnel(port=args.port, prefer=args.prefer)
        print(json.dumps(result, indent=2))
    elif args.command == "instructions":
        print(get_install_instructions())
