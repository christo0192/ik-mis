#!/bin/bash
# Daily Generation-department pipeline chain -- PLAN.md Phase 1 Step 14
# ("Schedule the generator").
#
# Chain (in order): collect_kw_perf -> lead_quality (Mondays-IST-only, or
# --force/FORCE_LEAD_QUALITY=true) -> generate_assets -> gate_keywords ->
# export_negatives -> instrument -> notify_slack.
#
# Fail-fast, unlike the anomaly-engine's own run-daily.sh (which
# deliberately accumulates failures and keeps going): PLAN.md Step 14 calls
# for "non-zero exit stops the chain" here, so a mid-chain failure (e.g. the
# relevance gate erroring out) does not let a later step write on top of a
# broken/partial run. `instrument.py` and `notify_slack.py` are read-only/
# notify-only by construction (see their own module docstrings) -- they
# never mutate BigQuery, so running them after a stopped chain would be
# safe too, but a stopped chain simply never reaches them, which is also
# the more conservative choice output-wise (no digest for a run that didn't
# finish).
#
# DRY_RUN is read from the environment (default "true", matching
# generation/config.py's own default) and is NOT overridden here -- every
# step's write behavior (or notify_slack's post-vs-print behavior) is
# controlled by the one env var, set once by whoever invokes this script
# (locally, in fly-deploy/entrypoint.sh, or via `fly machine run -e
# DRY_RUN=true` for a smoke test).
#
# Usage:
#   ./run_daily.sh                          # DRY_RUN=true (default), lead_quality only on Mondays IST
#   DRY_RUN=false ./run_daily.sh            # live run
#   FORCE_LEAD_QUALITY=true ./run_daily.sh  # run lead_quality regardless of weekday
#   GENERATION_PYTHON_BIN=/path/to/venv/bin/python3 ./run_daily.sh   # local testing outside the container

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GENDIR="$SCRIPT_DIR/generation"

# In the Fly image, dependencies are pip-installed into the system
# interpreter (no venv -- mirrors anomaly-engine's fly-deploy/Dockerfile),
# so the default is plain `python3`. Override for local testing against
# this repo's own .venv.
PYTHON="${GENERATION_PYTHON_BIN:-python3}"

export DRY_RUN="${DRY_RUN:-true}"
FORCE_LEAD_QUALITY="${FORCE_LEAD_QUALITY:-false}"
# Default under `set -u` (unset would crash the whole chain -- the 2026-07-12..14
# outage: this var was referenced at line ~80 without a default, so every run
# died right after collect_kw_perf, before generate_assets ever ran).
LEAD_QUALITY_WEEKLY="${LEAD_QUALITY_WEEKLY:-false}"
INSTRUMENT_WINDOW_DAYS="${INSTRUMENT_WINDOW_DAYS:-7}"

log() { echo "[run_daily] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }

log "================ ik-ad-generator daily run ($(date -u +%A), UTC) ================"
log "GENDIR=$GENDIR PYTHON=$PYTHON DRY_RUN=$DRY_RUN FORCE_LEAD_QUALITY=$FORCE_LEAD_QUALITY"

# run_step <step-name> <command...> -- logs start/ok, and on a non-zero
# exit, logs the failure and stops the whole chain immediately (the
# fail-fast behavior this step's PLAN.md entry calls for).
run_step() {
  local name="$1"; shift
  log "[$name] starting: $*"
  "$@"
  local rc=$?
  if [ "$rc" -ne 0 ]; then
    log "[$name] FAILED (exit=$rc) -- stopping chain, remaining steps will NOT run"
    exit "$rc"
  fi
  log "[$name] OK"
}

# --- 1. collect_kw_perf -- yesterday's search-term/keyword performance feed.
run_step "collect_kw_perf" "$PYTHON" "$GENDIR/collect_kw_perf.py"

# --- 2. lead_quality (CPQL signal). Runs DAILY by default: its source
#     (pmis_daily_segment_facts) refreshes daily, the job is cheap (a couple of
#     BQ reads over a 30-day window, no LLM), and CPQL feeds EVERY daily
#     generation run -- the old Monday-only cadence left the signal up to 6 days
#     stale (2026-07-11 hygiene finding). Set LEAD_QUALITY_WEEKLY=true to restore
#     the Mondays-IST-only cadence; FORCE_LEAD_QUALITY=true always runs it.
#     `date +%u`: 1=Monday .. 7=Sunday, via TZ=Asia/Kolkata (correct in the Fly
#     image and from any local shell).
IST_WEEKDAY="$(TZ='Asia/Kolkata' date +%u)"
if [ "$LEAD_QUALITY_WEEKLY" != "true" ] || [ "$IST_WEEKDAY" = "1" ] || [ "$FORCE_LEAD_QUALITY" = "true" ]; then
  run_step "lead_quality" "$PYTHON" "$GENDIR/lead_quality.py"
else
  log "[lead_quality] SKIPPED -- LEAD_QUALITY_WEEKLY=true and not Monday IST (weekday=$IST_WEEKDAY); FORCE_LEAD_QUALITY=true overrides"
fi

# --- 3. generate_assets -- keyword + copy/angle candidate generation (the
#     conductor; both paths, today's run_date, by default).
run_step "generate_assets" "$PYTHON" "$GENDIR/generate_assets.py"

# --- 4. gate_keywords -- tech/edtech relevance gate (deterministic
#     pre-filter + LLM judge on the ambiguous middle). Picks up the newest
#     raw_keyword_candidates_*.json generate_assets.py just wrote.
run_step "gate_keywords" "$PYTHON" "$GENDIR/gate_keywords.py"

# --- 5. export_negatives -- negative-keyword CSV + ₹ waste-baseline
#     value_ledger entry (reads keyword_rejects + kw_search_term_perf).
#     BUG FOUND 2026-07-07 (first fully-live scheduled run): the CLI's
#     defaults are --terms-source staging (a local DRY_RUN preview file that
#     collect_kw_perf no longer writes once DRY_RUN=false -- silently 0 rows,
#     not an error) and --rejects-source fixture (canned test data -- every
#     prior scheduled run blended 3 fake rows into the "real" negatives CSV
#     without ever reading the live ce_keyword_rejects table). Explicit now.
run_step "export_negatives" "$PYTHON" "$GENDIR/export_negatives.py" \
  --terms-source bq --rejects-source bq

# --- 6. instrument -- read-only value-ledger/acceptance-rate report.
#     Never writes to BigQuery (see instrument.py's own module docstring);
#     prints the "waste eliminated / CPQL trend / acceptance %" headline.
run_step "instrument" "$PYTHON" "$GENDIR/instrument.py" --window-days "$INSTRUMENT_WINDOW_DAYS"

# --- 7. notify_slack -- after-run digest, notify-only, zero interactive
#     elements. Posts for real only when DRY_RUN=false; otherwise prints
#     the composed message (this run's default, and every dry-run smoke
#     test's behavior).
NOTIFY_ARGS=()
if [ "$DRY_RUN" = "false" ]; then
  NOTIFY_ARGS+=(--no-dry-run)
fi
run_step "notify_slack" "$PYTHON" "$GENDIR/notify_slack.py" "${NOTIFY_ARGS[@]}"

log "================ ik-ad-generator daily run COMPLETE -- all steps ok ================"
exit 0
