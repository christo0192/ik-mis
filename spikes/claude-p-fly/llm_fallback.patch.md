# `ANTHROPIC_API_KEY` fallback for the `llm.py` wrapper pattern (NOT APPLIED)

Context: PLAN.md Phase 0 / Step 4 says to "add an `ANTHROPIC_API_KEY` fallback
path to the shared LLM wrapper (`market_research/shared/llm.py` pattern) so
either auth mode works." This doc is that diff, written against the actual
file at:

```
/mnt/c/Users/Admin/Claude projects/Ad content generator/market_research/shared/llm.py
```

**This patch is intentionally NOT applied** — per the task instructions, the
spike only needs to show the exact diff so it can be applied to both:
1. the Intelligence repo's `market_research/shared/llm.py` (shown below), and
2. the future Generation repo's `generation/llm.py` (same shape, once that
   file exists — do not create it now; another agent owns `generation/`).

Apply this only after the Fly spike (`spike_test.py` on `ik-claude-spike`)
determines the fallback path is actually needed (see this directory's
`README.md` → "Decision rule").

## Why a fallback is needed

`llm.py`'s `claude()` function shells out to `claude -p` unconditionally. If
the Fly-hosted `claude` CLI can't authenticate (OAuth token expired/invalid,
CLI not installed correctly, network egress blocked to the CLI's auth
endpoint), every LLM call in the pipeline hard-fails with no recourse other
than a person fixing the container. A direct Anthropic Messages API call
(stdlib `urllib`, no new dependency) as a fallback means a bad `claude -p`
day degrades to "spend a few cents on direct API calls" instead of "the
whole scheduled run fails."

## Model ID mapping (verified against the current model catalog)

`config.py` uses short CLI aliases (`"haiku"`, `"sonnet"`) for
`C.EXTRACT_MODEL` / `C.SYNTH_MODEL`, which the `claude -p --model <alias>`
CLI resolves internally. The Messages API does not accept those bare
aliases — it needs the canonical model ID string. Current mapping:

| CLI alias (`config.py`) | Messages API model ID |
|---|---|
| `"haiku"` | `claude-haiku-4-5` |
| `"sonnet"` | `claude-sonnet-5` |

(`claude-haiku-4-5` and `claude-sonnet-5` are the current aliases per the
Claude model catalog as of 2026-07; re-check `shared/models.md` / the
Models API before applying this patch if significant time has passed.)

## The diff

```diff
--- a/market_research/shared/llm.py
+++ b/market_research/shared/llm.py
@@ -1,15 +1,32 @@
-"""LLM access via `claude -p` (Claude Code headless, Max plan — no API key).
+"""LLM access via `claude -p` (Claude Code headless, Max plan — no API key),
+with an ANTHROPIC_API_KEY fallback for when the CLI path fails (expired/
+invalid OAuth token, CLI not installed, network egress blocked to the CLI's
+auth endpoint, etc.) -- see spikes/claude-p-fly/README.md for the spike that
+validated this pattern on Fly.io.

 extract_json(): page/article -> validated dict (Haiku). The tool-call gate is
 applied by the caller via the Pydantic model. synthesize(): free-text (Sonnet).

 Long prompts (full article text) are piped via STDIN to avoid OS command-line
 length limits. Output often comes back wrapped in a ```json fence -> stripped.
 """
 from __future__ import annotations
-import subprocess, json, re
+import json, os, re, subprocess, urllib.error, urllib.request
 from concurrent.futures import ThreadPoolExecutor, as_completed
 import config as C

 _FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
+
+# CLI short alias -> Messages API model ID. The `claude -p --model` flag
+# accepts short aliases ("haiku", "sonnet"); the Messages API needs the
+# canonical ID. Extend this map if config.py ever introduces a new alias.
+_API_MODEL_MAP = {
+    "haiku": "claude-haiku-4-5",
+    "sonnet": "claude-sonnet-5",
+    "opus": "claude-opus-4-8",
+}
+_FALLBACK_MAX_TOKENS = int(os.getenv("MR_LLM_FALLBACK_MAX_TOKENS", "4096"))


 def _strip_fences(text: str) -> str:
     t = (text or "").strip()
     m = _FENCE.search(t)
     return m.group(1).strip() if m else t


-def claude(prompt: str, model: str | None = None, timeout: int | None = None) -> str:
-    """One headless call. Prompt is sent on STDIN (handles large content)."""
+def _api_fallback(prompt: str, model_alias: str, timeout: int) -> str:
+    """Direct Anthropic Messages API call via stdlib urllib. Used only when
+    `claude -p` fails AND ANTHROPIC_API_KEY is set. No SDK dependency -- keeps
+    this fallback usable even in a minimal container that only has the CLI
+    installed, not the `anthropic` Python package."""
+    api_key = os.environ.get("ANTHROPIC_API_KEY")
+    if not api_key:
+        raise RuntimeError("ANTHROPIC_API_KEY not set; cannot fall back")
+
+    api_model = _API_MODEL_MAP.get(model_alias, model_alias)
+    body = json.dumps({
+        "model": api_model,
+        "max_tokens": _FALLBACK_MAX_TOKENS,
+        "messages": [{"role": "user", "content": prompt}],
+    }).encode("utf-8")
+
+    req = urllib.request.Request(
+        "https://api.anthropic.com/v1/messages",
+        data=body, method="POST",
+        headers={
+            "content-type": "application/json",
+            "x-api-key": api_key,
+            "anthropic-version": "2023-06-01",
+        },
+    )
+    try:
+        with urllib.request.urlopen(req, timeout=timeout) as resp:
+            raw = resp.read().decode("utf-8")
+    except urllib.error.HTTPError as e:
+        detail = e.read().decode("utf-8", errors="replace")[:300]
+        raise RuntimeError(f"Anthropic API HTTP {e.code}: {detail}") from e
+    except urllib.error.URLError as e:
+        raise RuntimeError(f"Anthropic API request failed: {e}") from e
+
+    envelope = json.loads(raw)
+    if envelope.get("stop_reason") == "refusal":
+        raise RuntimeError("Anthropic API refused the request (stop_reason=refusal)")
+    text = "".join(
+        block.get("text", "")
+        for block in envelope.get("content", [])
+        if block.get("type") == "text"
+    )
+    return text.strip()
+
+
+def claude(prompt: str, model: str | None = None, timeout: int | None = None) -> str:
+    """One headless call. Prompt is sent on STDIN (handles large content).
+    Falls back to a direct Anthropic Messages API call if `claude -p` fails
+    and ANTHROPIC_API_KEY is set (fail-soft auth: CLI first, API key second)."""
     model = model or C.EXTRACT_MODEL
     timeout = timeout or C.LLM_TIMEOUT_S
-    proc = subprocess.run(
-        [C.CLAUDE_BIN, "-p", "--model", model, "--output-format", "text"],
-        input=prompt, capture_output=True, text=True, encoding="utf-8",
-        timeout=timeout,
-    )
-    if proc.returncode != 0:
-        raise RuntimeError(f"claude -p ({model}) exit {proc.returncode}: "
-                           f"{(proc.stderr or '')[:300]}")
-    return (proc.stdout or "").strip()
+    try:
+        proc = subprocess.run(
+            [C.CLAUDE_BIN, "-p", "--model", model, "--output-format", "text"],
+            input=prompt, capture_output=True, text=True, encoding="utf-8",
+            timeout=timeout,
+        )
+        if proc.returncode != 0:
+            raise RuntimeError(f"claude -p ({model}) exit {proc.returncode}: "
+                               f"{(proc.stderr or '')[:300]}")
+        return (proc.stdout or "").strip()
+    except (RuntimeError, subprocess.TimeoutExpired, FileNotFoundError) as e:
+        if not os.environ.get("ANTHROPIC_API_KEY"):
+            raise
+        # CLI path failed and a fallback key is available -- degrade instead
+        # of hard-failing the whole pipeline run.
+        return _api_fallback(prompt, model, timeout)
```

## Notes for whoever applies this

- **Preserves the existing signature and callers.** `extract_json()`,
  `synthesize()`, and `map_extract()` all call `claude()` and need no changes
  — the fallback is internal to `claude()`.
- **Fail-soft, not silent.** If `claude -p` fails and no
  `ANTHROPIC_API_KEY` is set, the original exception still propagates
  unchanged (no behavior change for deployments that haven't opted into the
  fallback).
- **`_API_MODEL_MAP` must be kept in sync** with whatever aliases
  `config.py`'s `EXTRACT_MODEL` / `SYNTH_MODEL` env vars can hold. If a
  future config value is already a full model ID (not a short alias), the
  `.get(model_alias, model_alias)` fallback passes it through unchanged.
- **No new dependency.** Uses stdlib `urllib` only, matching the pattern in
  this spike's own `spike_test.py` — deliberately not pulling in the
  `anthropic` Python SDK just for one fallback call path.
- **Cost:** Haiku fallback calls are ~$1/$5 per MTok in/out (cheap); Sonnet
  fallback calls are ~$3/$15 per MTok — matches the "~$2-9/mo" estimate in
  `Ad content generator/PROJECT_TODO.md`'s blocker note for the API-key path.
- **Apply the same diff shape to `generation/llm.py`** once that file exists
  (owned by another agent per this task's constraints) — same `_api_fallback`
  helper, same `_API_MODEL_MAP`, adjusted for whatever config module that
  repo uses in place of `config.py`.
