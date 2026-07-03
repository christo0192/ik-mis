#!/bin/bash
# Container PID 1 for the claude -p / Fly de-risk spike.
# Runs spike_test.py once, prints the result, and exits -- the machine stops
# itself (restart policy = "never" in fly.toml, same as anomaly-engine's
# batch pattern). No secrets are materialized here beyond what Fly injects
# as plain env vars (CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY) -- this
# spike has no other credentials to handle.

set -uo pipefail
log() { echo "[entrypoint] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }

log "Starting ik-claude-spike container (host=$(hostname))"

if [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
  log "CLAUDE_CODE_OAUTH_TOKEN is set (len=${#CLAUDE_CODE_OAUTH_TOKEN})"
else
  log "CLAUDE_CODE_OAUTH_TOKEN is NOT set"
fi
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  log "ANTHROPIC_API_KEY is set (len=${#ANTHROPIC_API_KEY}) -- fallback available"
else
  log "ANTHROPIC_API_KEY is NOT set -- no fallback available"
fi

if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] && [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  log "FATAL: neither CLAUDE_CODE_OAUTH_TOKEN nor ANTHROPIC_API_KEY is set."
  log "Fix: fly secrets set CLAUDE_CODE_OAUTH_TOKEN=... -a ik-claude-spike"
  log "  or: fly secrets set ANTHROPIC_API_KEY=... -a ik-claude-spike"
  exit 2
fi

python3 /app/spike_test.py
rc=$?

log "spike_test.py exit code: $rc"
if [ $rc -eq 0 ]; then
  log "SPIKE RESULT: PASS (some auth path succeeded)"
else
  log "SPIKE RESULT: FAIL (both paths failed or no path available)"
fi

exit $rc
