#!/usr/bin/env python3
"""claude -p on Fly.io -- de-risk spike (PLAN.md Phase 0 / Step 4).

Two-path smoke test, run unattended inside the Fly machine (and locally in
WSL to validate the script logic itself):

  1. PRIMARY: `claude -p` headless call (OAuth token via CLAUDE_CODE_OAUTH_TOKEN,
     or an already-authenticated local session). Mirrors the subprocess pattern
     in market_research/shared/llm.py (this project's proven claude() wrapper).
  2. FALLBACK: if (and only if) the primary path fails AND ANTHROPIC_API_KEY is
     set, call the Anthropic Messages API directly via stdlib urllib (no SDK
     dependency, keeps the image minimal).

Exit code 0 iff either path produced a pass (PASS or FALLBACK-PASS).
Exit code 1 otherwise.

This script intentionally has no third-party dependencies (stdlib only) so it
runs the same way locally and inside the minimal Fly image.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

PROMPT = 'Return exactly the JSON {"ok": true}'
CLAUDE_TIMEOUT_S = 120
FALLBACK_MODEL = "claude-haiku-4-5-20251001"
FALLBACK_MAX_TOKENS = 50

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_JSON_OBJ = re.compile(r"\{.*\}", re.S)


def _extract_json(text: str):
    """Best-effort JSON extraction: strip ```fences```, else grab the first
    {...} blob. Mirrors the _strip_fences() tolerance in shared/llm.py, since
    `claude -p` output is not guaranteed to be bare JSON."""
    t = (text or "").strip()
    m = _FENCE.search(t)
    if m:
        t = m.group(1).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    m = _JSON_OBJ.search(t)
    if m:
        return json.loads(m.group(0))
    raise ValueError(f"no parseable JSON in output: {t[:200]!r}")


def _is_ok(parsed) -> bool:
    return isinstance(parsed, dict) and parsed.get("ok") is True


def try_claude_p() -> tuple[bool, str]:
    """PRIMARY path: `claude -p` headless. Returns (ok, detail)."""
    claude_bin = os.environ.get("CLAUDE_BIN", "claude")
    try:
        proc = subprocess.run(
            [claude_bin, "-p", PROMPT, "--output-format", "text"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=CLAUDE_TIMEOUT_S,
        )
    except FileNotFoundError:
        return False, f"'{claude_bin}' binary not found on PATH"
    except subprocess.TimeoutExpired:
        return False, f"claude -p timed out after {CLAUDE_TIMEOUT_S}s"

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()[:500]
        return False, f"claude -p exit {proc.returncode}: {stderr}"

    stdout = (proc.stdout or "").strip()
    try:
        parsed = _extract_json(stdout)
    except Exception as e:
        return False, f"could not parse JSON from claude -p output: {e} (raw: {stdout[:200]!r})"

    if not _is_ok(parsed):
        return False, f"claude -p returned JSON but not {{'ok': true}}: {parsed!r}"

    return True, f"claude -p OK, raw output: {stdout[:200]!r}"


def try_api_fallback() -> tuple[bool, str]:
    """FALLBACK path: direct Anthropic Messages API call via stdlib urllib.
    Only invoked when the primary path fails AND ANTHROPIC_API_KEY is set."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return False, "ANTHROPIC_API_KEY not set; fallback not attempted"

    body = json.dumps(
        {
            "model": FALLBACK_MODEL,
            "max_tokens": FALLBACK_MAX_TOKENS,
            "messages": [{"role": "user", "content": PROMPT}],
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        method="POST",
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=CLAUDE_TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        return False, f"Anthropic API HTTP {e.code}: {detail}"
    except urllib.error.URLError as e:
        return False, f"Anthropic API request failed: {e}"

    try:
        envelope = json.loads(raw)
        text = "".join(
            block.get("text", "")
            for block in envelope.get("content", [])
            if block.get("type") == "text"
        )
        parsed = _extract_json(text)
    except Exception as e:
        return False, f"could not parse Anthropic API response: {e} (raw: {raw[:300]!r})"

    if not _is_ok(parsed):
        return False, f"Anthropic API returned JSON but not {{'ok': true}}: {parsed!r}"

    return True, f"Anthropic API fallback OK, model={FALLBACK_MODEL}"


def main() -> int:
    print(f"[spike_test] prompt: {PROMPT!r}")
    print("[spike_test] --- PRIMARY: claude -p ---")
    ok, detail = try_claude_p()
    if ok:
        print(f"PASS: {detail}")
        return 0
    print(f"FAIL: {detail}")

    print("[spike_test] --- FALLBACK: ANTHROPIC_API_KEY direct call ---")
    fb_ok, fb_detail = try_api_fallback()
    if fb_ok:
        print(f"FALLBACK-PASS: {fb_detail}")
        return 0
    print(f"FALLBACK-FAIL: {fb_detail}")

    print("RESULT: both paths failed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
