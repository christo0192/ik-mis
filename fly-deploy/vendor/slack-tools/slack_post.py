"""
Slack Posting Helper — IK India Marketing Intelligence
========================================================
Posts messages to Slack via the Web API (chat.postMessage).
Supports thread replies and automatic message chunking.

Usage:
  python slack_post.py --message "Hello team"
  python slack_post.py --message "Main alert" --thread "Detailed breakdown"
  python slack_post.py --message "Thread reply" --thread-ts "1234567890.123456"

Returns the message timestamp (thread_ts) on stdout for chaining thread replies.

Auth: Reads SLACK_BOT_TOKEN and SLACK_CHANNEL_ID from .env

--------------------------------------------------------------------------
VENDORED COPY -- PLAN.md Phase 1 Step 14 (ik-ad-generator Fly deploy).
--------------------------------------------------------------------------
This is a verbatim copy of /mnt/c/mcp-servers/slack-tools/slack_post.py,
kept under fly-deploy/vendor/ solely so the ik-ad-generator Docker build
context can stay scoped to this repo (the MIS repo root) instead of a
shared C:\\mcp-servers context. The anomaly-engine's own fly-deploy/
Dockerfile avoids this by building from C:\\mcp-servers (its repo root),
which lets it COPY both anomaly-engine/ and slack-tools/ as build-context
siblings -- unavailable here because, per PLAN.md's "Naming caution", the
Generation department's code intentionally lives in a separate repo
(this one) from the Performance department's mcp-servers runtime.
generation/notify_slack.py shells out to whichever copy IK_MCP_DIR points
at (default /app/slack-tools/slack_post.py inside this image); the
credential-resolution and chat.postMessage mechanics below are untouched.
Keep this file in sync with the original by hand if it changes upstream.
"""

import argparse
import json
import ssl
import sys
import http.client
import urllib.request
import urllib.parse
from pathlib import Path

from dotenv import load_dotenv
import os

# Minimum required Slack bot scopes: chat:write (text), files:write (--image upload)
# Audit your bot at: https://api.slack.com/apps → OAuth & Permissions

# ---------------------------------------------------------------------------
# Load credentials
# ---------------------------------------------------------------------------

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID", "")

_ssl_ctx = ssl.create_default_context()

MAX_CHUNK_SIZE = 3900  # Slack limit is ~4000 chars per message text


# ---------------------------------------------------------------------------
# Slack API call
# ---------------------------------------------------------------------------

def slack_post(text: str, thread_ts: str | None = None) -> dict:
    """
    Post a message to Slack using chat.postMessage.
    Returns the API response dict (includes 'ts' for threading).
    """
    if not BOT_TOKEN or BOT_TOKEN.startswith("xoxb-placeholder"):
        print("ERROR: SLACK_BOT_TOKEN not configured. Update C:/mcp-servers/slack-tools/.env", file=sys.stderr)
        sys.exit(1)

    if not CHANNEL_ID or CHANNEL_ID == "C0000000000":
        print("ERROR: SLACK_CHANNEL_ID not configured. Update C:/mcp-servers/slack-tools/.env", file=sys.stderr)
        sys.exit(1)

    payload = {
        "channel": CHANNEL_ID,
        "text": text,
        "unfurl_links": False,
        "unfurl_media": False,
    }

    if thread_ts:
        payload["thread_ts"] = thread_ts

    body = json.dumps(payload).encode("utf-8")

    conn = http.client.HTTPSConnection("slack.com", context=_ssl_ctx, timeout=30)
    try:
        conn.request(
            "POST",
            "/api/chat.postMessage",
            body=body,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {BOT_TOKEN}",
            },
        )
        resp = conn.getresponse()
        resp_body = resp.read().decode("utf-8")

        # Handle rate limiting with retry
        if resp.status == 429:
            import time
            retry_after = int(resp.getheader("Retry-After", "5"))
            print(f"Rate limited. Retrying in {retry_after}s...", file=sys.stderr)
            time.sleep(retry_after)
            conn.close()
            conn = http.client.HTTPSConnection("slack.com", context=_ssl_ctx, timeout=30)
            conn.request("POST", "/api/chat.postMessage", body=body, headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {BOT_TOKEN}",
            })
            resp = conn.getresponse()
            resp_body = resp.read().decode("utf-8")

        if resp.status != 200:
            print(f"ERROR: Slack HTTP {resp.status}: {resp_body[:300]}", file=sys.stderr)
            sys.exit(1)

        data = json.loads(resp_body)

        if not data.get("ok"):
            error = data.get("error", "unknown")
            print(f"ERROR: Slack API error: {error}", file=sys.stderr)
            if error == "not_in_channel":
                print("FIX: Invite the bot to the channel with /invite @IK Ads Anomaly Bot", file=sys.stderr)
            elif error == "channel_not_found":
                print("FIX: Check SLACK_CHANNEL_ID in .env — use the channel ID (C...), not the name", file=sys.stderr)
            elif error == "invalid_auth":
                print("FIX: Check SLACK_BOT_TOKEN in .env — should start with xoxb-", file=sys.stderr)
            sys.exit(1)

        return data

    finally:
        conn.close()


def _slack_api(method: str, body: dict) -> dict:
    """POST a JSON request to a slack.com API method. Returns parsed response."""
    payload = json.dumps(body).encode("utf-8")
    conn = http.client.HTTPSConnection("slack.com", context=_ssl_ctx, timeout=60)
    try:
        conn.request("POST", f"/api/{method}", body=payload, headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {BOT_TOKEN}",
        })
        resp = conn.getresponse()
        data = json.loads(resp.read().decode("utf-8"))
        if not data.get("ok"):
            print(f"ERROR: Slack {method} failed: {data.get('error', 'unknown')}", file=sys.stderr)
            if data.get("error") == "missing_scope":
                print("FIX: add the 'files:write' scope to the bot and reinstall the app.", file=sys.stderr)
            sys.exit(1)
        return data
    finally:
        conn.close()


def _ts_from_shares(shares: dict) -> str:
    """Pull the first message ts out of a Slack file `shares` block."""
    for visibility in ("public", "private"):
        for entries in shares.get(visibility, {}).values():
            for entry in entries:
                if entry.get("ts"):
                    return entry["ts"]
    return ""


def _find_share_ts(complete_resp: dict) -> str:
    """Extract the channel message ts from a completeUploadExternal response."""
    for f in complete_resp.get("files", []):
        ts = _ts_from_shares(f.get("shares", {}))
        if ts:
            return ts
    return ""


def _file_info_ts(file_id: str) -> str:
    """Look up a file's shared-message ts via files.info (needs files:read scope).

    completeUploadExternal often returns an empty `shares` block, so we re-fetch.
    Returns "" if the scope is missing or the share is not yet visible.
    """
    conn = http.client.HTTPSConnection("slack.com", context=_ssl_ctx, timeout=30)
    try:
        conn.request("GET", f"/api/files.info?file={file_id}",
                     headers={"Authorization": f"Bearer {BOT_TOKEN}"})
        data = json.loads(conn.getresponse().read().decode("utf-8"))
    finally:
        conn.close()
    if not data.get("ok"):
        # missing_scope -> bot lacks files:read; caller falls back to standalone.
        return ""
    return _ts_from_shares(data.get("file", {}).get("shares", {}))


def upload_image(image_path: str, comment: str, thread_ts: str | None = None) -> str:
    """Upload a local image to the channel via Slack's external-upload flow.

    Three steps: getUploadURLExternal -> POST bytes -> completeUploadExternal.
    `comment` becomes the message text (initial_comment).

    Returns the posted message ts when Slack populates `shares`. NOTE: bots
    without the files:read scope often get an empty `shares` block, so this may
    return "" -- callers must treat the return as best-effort and fall back to
    a standalone follow-up message rather than relying on threading.
    """
    if not BOT_TOKEN or BOT_TOKEN.startswith("xoxb-placeholder"):
        print("ERROR: SLACK_BOT_TOKEN not configured.", file=sys.stderr)
        sys.exit(1)
    path = Path(image_path)
    if not path.exists():
        print(f"ERROR: image not found: {image_path}", file=sys.stderr)
        sys.exit(1)

    data = path.read_bytes()

    # Step 1: reserve an upload URL
    qs = urllib.parse.urlencode({"filename": path.name, "length": len(data)})
    conn = http.client.HTTPSConnection("slack.com", context=_ssl_ctx, timeout=60)
    try:
        conn.request("GET", f"/api/files.getUploadURLExternal?{qs}",
                     headers={"Authorization": f"Bearer {BOT_TOKEN}"})
        info = json.loads(conn.getresponse().read().decode("utf-8"))
    finally:
        conn.close()
    if not info.get("ok"):
        print(f"ERROR: getUploadURLExternal failed: {info.get('error', 'unknown')}", file=sys.stderr)
        if info.get("error") == "missing_scope":
            print("FIX: add the 'files:write' scope to the bot and reinstall the app.", file=sys.stderr)
        sys.exit(1)

    # Step 2: upload the raw bytes to the one-time URL
    req = urllib.request.Request(info["upload_url"], data=data, method="POST")
    with urllib.request.urlopen(req, context=_ssl_ctx, timeout=120) as up:
        if up.status not in (200, 201):
            print(f"ERROR: file byte upload HTTP {up.status}", file=sys.stderr)
            sys.exit(1)

    # Step 3: complete the upload and share it into the channel
    complete_body = {
        "files": [{"id": info["file_id"], "title": path.name}],
        "channel_id": CHANNEL_ID,
        "initial_comment": comment,
    }
    if thread_ts:
        complete_body["thread_ts"] = thread_ts
    result = _slack_api("files.completeUploadExternal", complete_body)

    # Prefer the ts from the response; fall back to files.info (needs files:read)
    # so callers can thread replies onto the image message.
    ts = _find_share_ts(result)
    if not ts:
        ts = _file_info_ts(info["file_id"])
    return ts or (thread_ts or "")


def post_chunked(text: str, thread_ts: str | None = None) -> str:
    """
    Post a message, chunking into multiple messages if it exceeds Slack's limit.
    Returns the thread_ts of the first message.
    """
    if len(text) <= MAX_CHUNK_SIZE:
        result = slack_post(text, thread_ts=thread_ts)
        return result.get("ts", "")

    # Split by lines, accumulate chunks
    lines = text.split("\n")
    chunks = []
    current_chunk = ""

    for line in lines:
        # Handle single lines that exceed the limit
        if len(line) > MAX_CHUNK_SIZE:
            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = ""
            for i in range(0, len(line), MAX_CHUNK_SIZE):
                chunks.append(line[i:i + MAX_CHUNK_SIZE])
            continue

        if len(current_chunk) + len(line) + 1 > MAX_CHUNK_SIZE:
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = line
        else:
            current_chunk = current_chunk + "\n" + line if current_chunk else line

    if current_chunk:
        chunks.append(current_chunk)

    # Post first chunk (may be main message or thread)
    first_result = slack_post(chunks[0], thread_ts=thread_ts)
    first_ts = first_result.get("ts", "")

    # Remaining chunks as thread replies
    reply_ts = thread_ts or first_ts
    for chunk in chunks[1:]:
        slack_post(chunk, thread_ts=reply_ts)

    return first_ts


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Post messages to Slack")
    parser.add_argument("--message", "-m", required=True, help="Message text to post")
    parser.add_argument("--thread", "-t", default=None, help="Detailed text to post as thread reply")
    parser.add_argument("--thread-ts", default=None, help="Thread timestamp to reply to (for chaining)")
    parser.add_argument("--image", default=None,
                        help="Path to an image to upload; --message becomes its caption")

    args = parser.parse_args()

    if not args.message.strip():
        print("ERROR: --message cannot be empty", file=sys.stderr)
        sys.exit(1)

    # Post main message -- as an image upload (caption = message) when --image is set,
    # otherwise as a normal text message.
    if args.image:
        ts = upload_image(args.image, comment=args.message, thread_ts=args.thread_ts)
    else:
        ts = post_chunked(args.message, thread_ts=args.thread_ts)

    # Print thread_ts to stdout for chaining
    print(ts)

    # Post thread reply if provided
    if args.thread:
        reply_ts = args.thread_ts or ts
        post_chunked(args.thread, thread_ts=reply_ts)


if __name__ == "__main__":
    main()
