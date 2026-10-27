#!/bin/bash
# Container PID 1 for ik-ad-generator. Materializes credentials from Fly
# secrets into the file paths the Generation-department scripts already
# expect (IK_MCP_DIR=/app layout, GENERATION_BQ_SERVICE_ACCOUNT), then runs
# run_daily.sh. Pattern cloned from the proven
# /mnt/c/mcp-servers/anomaly-engine/fly-deploy/entrypoint.sh.
#
# Secrets are passed base64-encoded (single-line, shell-safe across
# PowerShell/Bash) and decoded to files here. Generate them locally with,
# e.g.:
#   [Convert]::ToBase64String([IO.File]::ReadAllBytes("C:\mcp-servers\google-ads\.env"))
#
# LINKEDIN_ENV_B64 is OPTIONAL (added 2026-10-27 for run_daily.sh's weekly
# registry_refresh) and deliberately NOT in `required` below: without it the
# refresh is Google-only and LinkedIn registry rows are carried forward, so
# the rest of the chain must not depend on it. Set it (staged, so a running
# machine isn't restarted mid-run) from PowerShell:
#   $b=[Convert]::ToBase64String([IO.File]::ReadAllBytes("C:\mcp-servers\linkedin-ads\.env"))
#   flyctl secrets set LINKEDIN_ENV_B64=$b -a ik-ad-generator --stage
# Only the refresh token needs to stay valid: the registry builder refreshes
# the (60-day) access token in memory on every 401.
#
# CLAUDE_CODE_OAUTH_TOKEN is the one exception -- it's a plain string secret
# (not base64/file-backed), matching spikes/claude-p-fly's proven pattern:
# the Claude Code CLI reads it directly from the environment.

set -euo pipefail
log() { echo "[entrypoint] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }

log "Starting ik-ad-generator container (TZ=$TZ, host=$(hostname))"

required=(
  GOOGLE_ADS_ENV_B64
  SLACK_ENV_B64
  GENERATION_BQ_SERVICE_ACCOUNT_B64
  CLAUDE_CODE_OAUTH_TOKEN
)
missing=0
for v in "${required[@]}"; do
  if [ -z "${!v:-}" ]; then
    log "FATAL: required Fly secret '$v' is not set."
    log "Fix: flyctl secrets set $v=... -a ik-ad-generator"
    missing=1
  fi
done
if [ "$missing" -ne 0 ]; then
  exit 2
fi
log "All ${#required[@]} required secrets present."

# --- Materialize the IK_MCP_DIR (/app) layout collect_kw_perf.py and
# notify_slack.py already read by path. ---
mkdir -p /app/google-ads /app/slack-tools

echo "$GOOGLE_ADS_ENV_B64" | base64 -d > /app/google-ads/.env
echo "$SLACK_ENV_B64"      | base64 -d > /app/slack-tools/.env
chmod 600 /app/google-ads/.env /app/slack-tools/.env

# --- Optional LinkedIn creds (registry_refresh only -- see header). Guarded:
# under `set -euo pipefail` an unguarded bad base64 would abort the whole
# container (and so the whole day's run) over an optional secret. Values are
# never echoed; only presence/outcome is logged. ---
if [ -n "${LINKEDIN_ENV_B64:-}" ]; then
  mkdir -p /app/linkedin-ads
  if echo "$LINKEDIN_ENV_B64" | base64 -d > /app/linkedin-ads/.env 2>/dev/null \
      && [ -s /app/linkedin-ads/.env ]; then
    chmod 600 /app/linkedin-ads/.env
    log "LinkedIn creds materialized (registry refresh will include LinkedIn)."
  else
    rm -f /app/linkedin-ads/.env
    log "WARN: LINKEDIN_ENV_B64 failed to decode (or decoded empty) -- registry refresh will be Google-only (LinkedIn rows carried forward)."
  fi
else
  log "LINKEDIN_ENV_B64 not set -- registry refresh will be Google-only (LinkedIn rows carried forward)."
fi

# --- Generation BQ service account (guarded writer + the read-only clients
# in notify_slack.py/instrument.py/lead_quality.py). One key covers both:
# claude-code-dev-bq@ already has write on SIP_pattern_rag/ce_* (config.py's
# own doc) and, being the writer identity, also has read on the same tables
# -- no separate GENERATION_BQ_READ_SERVICE_ACCOUNT secret is needed unless
# a narrower read-only key is issued later. ---
echo "$GENERATION_BQ_SERVICE_ACCOUNT_B64" | base64 -d > /tmp/generation_bq_service_account.json
chmod 600 /tmp/generation_bq_service_account.json
export GENERATION_BQ_SERVICE_ACCOUNT=/tmp/generation_bq_service_account.json

export IK_MCP_DIR=/app
mkdir -p "$HOME"

log "Credentials materialized. Handing off to run_daily.sh"
exec /app/run_daily.sh
