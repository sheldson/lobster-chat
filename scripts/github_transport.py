#!/usr/bin/env python3
"""
GitHub Gist Transport — zero-server message delivery via GitHub Gists.

Each lobster has a Gist as their "inbox". Sending a message = creating a
comment on the recipient's Gist. Pulling = reading own Gist comments.

Requirements:
    - A GitHub token with 'gist' scope (set as GITHUB_TOKEN env var,
      or stored in data/github_token)
    - No server, no relay, no infrastructure to maintain

How it works:
    1. init_inbox()  → creates a Gist, returns gist_id
    2. send(gist_id, envelope) → posts a comment on recipient's Gist
    3. pull(gist_id) → reads comments from own Gist, deletes after read
"""
import json
import os
import subprocess
from pathlib import Path
from urllib import request as urllib_request

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
TOKEN_FILE = DATA / "github_token"

GITHUB_API = "https://api.github.com"


def _get_token() -> str:
    """Get GitHub token from env or file."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token and TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text().strip()
    if not token:
        # Try gh CLI as last resort
        try:
            result = subprocess.run(
                ["gh", "auth", "token"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                token = result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    return token


def _api(method: str, path: str, body: dict | None = None, token: str = "") -> dict:
    """Make a GitHub API call. Returns parsed JSON response."""
    if not token:
        token = _get_token()
    if not token:
        return {"ok": False, "error": "no_github_token"}

    url = GITHUB_API + path if path.startswith("/") else path
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body else None
    req = urllib_request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        with urllib_request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except Exception as e:
        error_msg = str(e)
        # Try to read error body
        if hasattr(e, 'read'):
            try:
                error_msg = e.read().decode("utf-8")
            except Exception:
                pass
        return {"ok": False, "error": error_msg}


def create_inbox(lobster_name: str = "lobster") -> dict:
    """Create a new Gist to serve as this lobster's inbox.
    Returns {"ok": True, "gist_id": "...", "gist_url": "..."} or error."""
    token = _get_token()
    if not token:
        return {"ok": False, "error": "no_github_token. Set GITHUB_TOKEN env var or save token to data/github_token"}

    resp = _api("POST", "/gists", {
        "description": f"lobster-link inbox for {lobster_name} (do not delete)",
        "public": True,
        "files": {
            "LOBSTER_INBOX.md": {
                "content": f"# Lobster Link Inbox\n\nThis gist is the message inbox for **{lobster_name}**.\n\nMessages are delivered as comments. Do not delete this gist.\n"
            }
        }
    }, token=token)

    if "id" not in resp:
        return {"ok": False, "error": resp.get("message", resp.get("error", "unknown"))}

    return {
        "ok": True,
        "gist_id": resp["id"],
        "gist_url": resp.get("html_url", ""),
    }


def send_message(gist_id: str, envelope: dict) -> dict:
    """Send a message by creating a comment on the recipient's Gist.
    The comment body is the JSON-encoded envelope."""
    token = _get_token()
    if not token:
        return {"ok": False, "error": "no_github_token"}

    # Encode envelope as JSON in a comment
    comment_body = json.dumps(envelope, ensure_ascii=False)
    resp = _api("POST", f"/gists/{gist_id}/comments", {
        "body": comment_body,
    }, token=token)

    if "id" not in resp:
        return {"ok": False, "error": resp.get("message", resp.get("error", "unknown"))}

    return {"ok": True, "comment_id": resp["id"]}


def pull_messages(gist_id: str) -> dict:
    """Pull all messages (comments) from own Gist inbox.
    Returns messages and deletes the comments after reading."""
    token = _get_token()
    if not token:
        return {"ok": False, "error": "no_github_token"}

    # List all comments on the gist
    resp = _api("GET", f"/gists/{gist_id}/comments", token=token)
    if isinstance(resp, dict) and "error" in resp:
        return resp

    # resp should be a list of comments
    if not isinstance(resp, list):
        return {"ok": False, "error": "unexpected_response"}

    messages = []
    comment_ids = []
    for comment in resp:
        body = comment.get("body", "")
        try:
            msg = json.loads(body)
            messages.append(msg)
            comment_ids.append(comment["id"])
        except (json.JSONDecodeError, KeyError):
            # Not a valid lobster message, skip
            comment_ids.append(comment["id"])
            continue

    # Delete read comments (cleanup inbox)
    for cid in comment_ids:
        _api("DELETE", f"/gists/{gist_id}/comments/{cid}", token=token)

    return {"ok": True, "messages": messages, "count": len(messages)}


def check_token() -> dict:
    """Check if GitHub token is available and valid."""
    token = _get_token()
    if not token:
        return {"ok": False, "error": "no_github_token"}
    resp = _api("GET", "/user", token=token)
    if "login" in resp:
        return {"ok": True, "github_user": resp["login"]}
    return {"ok": False, "error": resp.get("message", "invalid_token")}
