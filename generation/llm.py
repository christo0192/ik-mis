#!/usr/bin/env python3
"""
LLM access via `claude -p` (Claude Code headless) for the Generation
department's conductor (PLAN.md Phase 1 Step 9).

Adapted from `Ad content generator/market_research/shared/llm.py`'s
subprocess / fence-stripping / retry pattern (prompt piped via STDIN so long
digests never hit an OS command-line length limit; output often comes back
wrapped in a ```json fence -> stripped), with two additions PLAN.md's Step 4
spike calls for:

1. An `ANTHROPIC_API_KEY` fallback path -- applies the diff in
   `spikes/claude-p-fly/llm_fallback.patch.md` to this module. That patch
   was written against the *other* repo's `market_research/shared/llm.py`;
   its own notes say to "apply the same diff shape to `generation/llm.py`
   once that file exists" -- this is that file. Same `_api_fallback` helper,
   same `_API_MODEL_MAP`, same fail-soft behavior (CLI first, API key
   second; if `claude -p` fails and no `ANTHROPIC_API_KEY` is set, the
   original exception still propagates unchanged).
2. `LLM_MAX_CONCURRENCY` semantics: defaults to **1** (PLAN.md Step 4:
   "Concurrency stays 1 -- fine for batch cadence on a dedicated machine"),
   so `map_extract()` runs its calls sequentially unless an operator
   explicitly raises the env var. The conductor itself (`generate_assets.py`)
   never calls `map_extract()` in parallel form anyway -- one `claude -p`
   call per path -- but this module keeps the same knob/behavior the
   Intelligence dept's wrapper offers, for parity and any future caller.

This module intentionally has no dependency on `generation/config.py`
(that file is BQ-writer/schema configuration -- dataset, DRY_RUN, service
account -- not LLM configuration). Every knob here is its own env var with
a sane default, matching config.py's own "env-driven, no machine-specific
paths" style.

Testability: `tests_conductor.py` monkeypatches `call()` (not `extract_json`
or `synthesize`) to return canned JSON text -- every other function in this
module is a thin wrapper around `call()`, so patching that one function is
sufficient to drive the whole conductor without the `claude` CLI installed.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

CLAUDE_BIN = os.environ.get("GENERATION_CLAUDE_BIN", "claude")
EXTRACT_MODEL = os.environ.get("GENERATION_EXTRACT_MODEL", "haiku")
SYNTH_MODEL = os.environ.get("GENERATION_SYNTH_MODEL", "sonnet")
LLM_TIMEOUT_S = int(os.environ.get("GENERATION_LLM_TIMEOUT_S", "600"))
# PLAN.md Step 4: concurrency stays 1 -- batch cadence on a dedicated machine.
LLM_MAX_CONCURRENCY = int(os.environ.get("LLM_MAX_CONCURRENCY", "1"))

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)

# CLI short alias -> Messages API model ID (spikes/claude-p-fly/llm_fallback.patch.md,
# verified against the model catalog as of 2026-07). Extend if a new alias
# is ever passed as `model=`.
_API_MODEL_MAP = {
    "haiku": "claude-haiku-4-5",
    "sonnet": "claude-sonnet-5",
    "opus": "claude-opus-4-8",
}
_FALLBACK_MAX_TOKENS = int(os.environ.get("GENERATION_LLM_FALLBACK_MAX_TOKENS", "8192"))


def _strip_fences(text: str) -> str:
    t = (text or "").strip()
    m = _FENCE.search(t)
    return m.group(1).strip() if m else t


def _api_fallback(prompt: str, model_alias: str, timeout: int) -> str:
    """Direct Anthropic Messages API call via stdlib `urllib` -- no `anthropic`
    SDK dependency, matching the patch's own rationale (keeps the fallback
    usable in a minimal container that only has the CLI installed). Used
    only when `claude -p` fails AND `ANTHROPIC_API_KEY` is set."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set; cannot fall back")

    api_model = _API_MODEL_MAP.get(model_alias, model_alias)
    body = json.dumps({
        "model": api_model,
        "max_tokens": _FALLBACK_MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body, method="POST",
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"Anthropic API HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Anthropic API request failed: {e}") from e

    envelope = json.loads(raw)
    if envelope.get("stop_reason") == "refusal":
        raise RuntimeError("Anthropic API refused the request (stop_reason=refusal)")
    text = "".join(
        block.get("text", "")
        for block in envelope.get("content", [])
        if block.get("type") == "text"
    )
    return text.strip()


def call(prompt: str, model: str | None = None, timeout: int | None = None) -> str:
    """One headless `claude -p` call. Prompt is sent on STDIN (handles large
    digests without hitting OS command-line length limits). Falls back to a
    direct Anthropic Messages API call if the CLI invocation fails and
    `ANTHROPIC_API_KEY` is set (fail-soft auth: CLI first, API key second --
    see spikes/claude-p-fly/README.md's decision rule). If no fallback key is
    set, the original exception propagates unchanged -- no behavior change
    for a deployment that hasn't opted into the fallback.

    This is the single point every other function in this module calls
    through -- `tests_conductor.py` monkeypatches this one function to run
    the whole conductor without the `claude` CLI."""
    model = model or EXTRACT_MODEL
    timeout = timeout or LLM_TIMEOUT_S
    try:
        proc = subprocess.run(
            [CLAUDE_BIN, "-p", "--model", model, "--output-format", "text"],
            input=prompt, capture_output=True, text=True, encoding="utf-8",
            timeout=timeout,
        )
        if proc.returncode != 0:
            # Some claude CLI failure modes (auth/config errors in particular)
            # write to stdout instead of stderr -- capture both so a future
            # failure is actually diagnosable from logs (2026-07-07 scheduled
            # run failed with an empty stderr-only message, hiding the cause).
            detail = "; ".join(
                s for s in (
                    f"stderr={proc.stderr.strip()[:300]}" if proc.stderr and proc.stderr.strip() else "",
                    f"stdout={proc.stdout.strip()[:300]}" if proc.stdout and proc.stdout.strip() else "",
                ) if s
            ) or "(no output on stdout or stderr)"
            raise RuntimeError(f"claude -p ({model}) exit {proc.returncode}: {detail}")
        return (proc.stdout or "").strip()
    except (RuntimeError, subprocess.TimeoutExpired, FileNotFoundError):
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise
        return _api_fallback(prompt, model, timeout)


def extract_json(prompt: str, model: str | None = None, retries: int = 2,
                 timeout: int | None = None):
    """Call the LLM and parse a JSON object/array from the reply. Retries on
    unparseable output (appending a stricter nudge to the prompt)."""
    last = None
    p = prompt
    for _attempt in range(retries + 1):
        out = call(p, model=model, timeout=timeout)
        try:
            return json.loads(_strip_fences(out))
        except Exception as e:
            last = e
            p = prompt + "\n\nReturn ONLY valid JSON. No prose, no markdown fences."
    raise ValueError(f"unparseable JSON after {retries + 1} tries: {last}")


def synthesize(prompt: str, model: str | None = None, timeout: int | None = None) -> str:
    return call(prompt, model=model or SYNTH_MODEL, timeout=timeout)


def map_extract(prompts: list[str], model: str | None = None,
                max_workers: int | None = None, timeout: int | None = None) -> list:
    """Run extract_json over many prompts. Bounded by LLM_MAX_CONCURRENCY
    (default 1 -- PLAN.md Step 4: sequential calls; the conductor itself
    never needs this for more than one prompt at a time today, but this
    keeps parity with the Intelligence dept's wrapper for any future
    caller). Failures -> None in the corresponding slot (caller filters)."""
    workers = max_workers or LLM_MAX_CONCURRENCY
    results: list = [None] * len(prompts)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(extract_json, p, model, 2, timeout): i for i, p in enumerate(prompts)}
        for f in as_completed(futs):
            i = futs[f]
            try:
                results[i] = f.result()
            except Exception:
                results[i] = None
    return results
