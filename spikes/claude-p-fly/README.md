# `claude -p` on Fly.io — deployment de-risk spike

PLAN.md Phase 0 / Step 4. This is a throwaway Fly app whose only job is to
answer one question: **can `claude -p` complete a headless call unattended on
a Fly machine, using a long-lived OAuth token?** — and if not, does the
`ANTHROPIC_API_KEY` fallback work instead. The answer settles the auth
pattern for both the Intelligence dept's pending Fly deploys (flagged
"riskiest" in `Ad content generator/PROJECT_TODO.md`) and the future
`ik-ad-generator` app (PLAN.md Step 14).

Everything in this directory is scaffolded and locally verified. The steps
below are the **user-owned actions remaining** — they require an
interactive browser login and/or a Fly account, which the agent cannot do.

---

## 1. Generate the Claude Code OAuth token

Run this in a normal interactive terminal (NOT via a Claude Code `!` bash
call — that would echo the token into the transcript):

```powershell
claude setup-token
```

This opens a browser, you approve, and it prints a long-lived OAuth token to
the terminal. Copy it immediately (it is not shown again).

Save it locally for reference (optional, but matches the pattern already
used for the Intelligence deploys):

```powershell
Set-Content -Path "C:\Users\Admin\.secrets\claude_oauth_spike.token" -Value "<paste-token>" -NoNewline
```

## 2. Set it as a Fly secret

The exact env var the Claude Code CLI reads for a long-lived token is
**`CLAUDE_CODE_OAUTH_TOKEN`** (confirmed against the anomaly-engine's
`fly-deploy/entrypoint.sh`, which already requires this exact var name for
its own `claude -p` step, and against `Ad content generator/PROJECT_TODO.md`'s
blocker note).

```powershell
fly secrets set CLAUDE_CODE_OAUTH_TOKEN="<paste-token>" -a ik-claude-spike
```

**Fallback secret** (only needed if the OAuth path fails, or if you want to
test both paths in the same run): a standard Anthropic API key.

```powershell
fly secrets set ANTHROPIC_API_KEY="sk-ant-..." -a ik-claude-spike
```

`spike_test.py` only attempts the API-key fallback if the primary `claude -p`
call fails AND this var is set — so setting both secrets is safe and lets you
observe which path actually wins without a second deploy.

## 3. Launch / deploy the app

From this directory (`spikes/claude-p-fly/`):

```powershell
cd "C:\Users\Admin\Claude projects\MIS\spikes\claude-p-fly"

# First time only -- creates the app shell without deploying yet (name is
# already pinned in fly.toml as ik-claude-spike):
fly launch --no-deploy --copy-config --name ik-claude-spike

# Build + deploy the image:
fly deploy .
```

If `fly launch` complains the app name is taken or asks unrelated questions,
just create the app directly instead:

```powershell
fly apps create ik-claude-spike
fly deploy .
```

## 4. Run the spike

This app has **no scheduler and no `[http_service]`** — it's a one-shot batch
job you trigger manually, same shape as `anomaly-engine`'s scheduled runs but
without the Cloudflare cron wrapper (not needed for a spike).

```powershell
fly machine run . --app ik-claude-spike
```

This builds/uses the image, starts a machine, runs `entrypoint.sh` ->
`spike_test.py`, prints PASS/FAIL, and the machine stops itself
(`restart.policy = "never"` in `fly.toml` — it must not auto-retry, since
each run is a deliberate, observed test).

## 5. Read the logs

```powershell
fly logs -a ik-claude-spike
```

Look for one of:
- `PASS: claude -p OK, raw output: ...` — the OAuth token worked. **This is the target outcome.**
- `FALLBACK-PASS: Anthropic API fallback OK, model=claude-haiku-4-5-20251001` — OAuth failed, API key fallback worked.
- `RESULT: both paths failed` — investigate `FAIL:` / `FALLBACK-FAIL:` detail lines in the log; likely causes: token not set as secret, token expired/revoked, egress blocked, wrong env var name, network timeout.

## 6. Verify token persistence across a second machine start

The whole point of the OAuth token (vs. re-running `claude setup-token`
every time) is that it survives across machine starts/stops without
re-authentication. Verify this explicitly:

```powershell
# Run #1 (already done in step 4) -- confirm PASS in the logs.

# Run #2 -- a fresh machine start, same secret, no re-auth:
fly machine run . --app ik-claude-spike
fly logs -a ik-claude-spike
```

If run #2 also PASSes without any new `claude setup-token` step, the token
is confirmed durable across restarts — the OAuth pattern is safe to reuse
for scheduled (cron-triggered) deploys where no human is present to
re-authenticate.

## 7. Expected cost

Effectively nothing. `shared-cpu-1x` / 512mb, no `[http_service]` (so no
idle-machine billing), machine runs for well under a minute per invocation
and stops itself. A handful of manual test runs costs Fly machine-seconds
only — budget a few cents at most. Destroy the app when done:

```powershell
fly apps destroy ik-claude-spike
```

## 8. Decision rule

- **If OAuth (`CLAUDE_CODE_OAUTH_TOKEN`) PASSes and survives a second machine
  start (step 6):** use it everywhere — the pending Intelligence Fly deploys
  (weekly pipeline) and the future `ik-ad-generator` (PLAN.md Step 14) both
  adopt this exact secret + entrypoint pattern. No API-key billing needed;
  reuses the existing Max-plan subscription.
- **If OAuth fails (expired, revoked, blocked, or unreliable) but the
  `ANTHROPIC_API_KEY` fallback PASSes:** the API-key path becomes the
  standard pattern going forward. Apply `llm_fallback.patch.md` to
  `market_research/shared/llm.py` (Intelligence) and to the future
  `generation/llm.py` (this project) so both departments' `claude()` wrapper
  transparently falls back to a direct API call when the CLI path fails.
  Budget ~$2–9/mo per deploy per `PROJECT_TODO.md`'s own estimate.
- **If both fail:** do not proceed to PLAN.md Step 6 (Intelligence cadence)
  or Step 14 (schedule the generator) until resolved — this is a hard
  blocker for any Fly-hosted `claude -p` step in either department.

---

## Files in this directory

| File | Purpose |
|---|---|
| `Dockerfile` | Minimal image: Node 20 + `@anthropic-ai/claude-code` CLI + python3, non-root user. Cloned/stripped from the proven `anomaly-engine/fly-deploy/Dockerfile`. |
| `spike_test.py` | Stdlib-only test: tries `claude -p` first, falls back to a direct Anthropic Messages API call via `urllib` if the CLI path fails and `ANTHROPIC_API_KEY` is set. Prints PASS / FALLBACK-PASS / FAIL. |
| `entrypoint.sh` | Container PID 1: checks at least one auth secret is present, runs `spike_test.py`, logs the result, exits (machine stops; `restart = never`). |
| `fly.toml` | App `ik-claude-spike`, no `http_service`, `restart.policy = "never"`, `shared-cpu-1x` / 512mb. |
| `llm_fallback.patch.md` | Unapplied diff showing how to add the `ANTHROPIC_API_KEY` fallback to `market_research/shared/llm.py`'s `claude()` wrapper — apply only if the decision rule above lands on the fallback path. |

## Local verification already done (by the agent, before handing off)

`python3 spike_test.py` was run locally in WSL, where the `claude` CLI is
already authenticated from an existing session (no Fly, no OAuth token
generation needed for this local check — it validates the *script logic*,
not the Fly deployment itself). Result: see the task report / commit
message for the exact PASS output. `bash -n entrypoint.sh` confirmed the
script has no syntax errors.

The remaining unknowns that only the real Fly environment can answer are:
does the CLI authenticate correctly from a bare `CLAUDE_CODE_OAUTH_TOKEN`
env var with no prior local session, and does that token survive a second
machine start. Steps 1–6 above answer those.
