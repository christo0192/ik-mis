#!/usr/bin/env python3
"""
After-run Slack digest -- PLAN.md Phase 1 Step 13 ("Slack notify digest,
notify-only").

Posts a per-run summary after each generator run: counts by path/verdict/
status, a top-5 items preview, and a deep link back to the dashboard review
queue. **Notify-only** -- this module never writes to BigQuery, never
mutates review status, and has zero interactive elements (no buttons, no
Slack callbacks). The dashboard (Mission Control, Step 12) is the sole
approve/reject anchor; this is a link + a summary, nothing else.

Data sources (read-only, latest `run_date`):
  - `generation_runs`   -- per-path run stats (candidates/pass/borderline/reject).
  - `keyword_candidates` -- counts by relevance_verdict + status; top-5 by score.
  - `ad_copy_drafts`     -- counts by status; top-5 headlines.

Read strategy (lazy, degrades gracefully -- this pipeline is still early in
Phase 0/1 and the tables may be empty or not yet queryable live):
  1. `--from-fixture <path>`: a JSON object shaped
     `{"generation_runs": [...], "keyword_candidates": [...], "ad_copy_drafts": [...]}`
     (rows shaped like the corresponding schemas.*Row model) -- skips BigQuery
     entirely. For tests and for running without credentials.
  2. Live BigQuery read via a lazy client (mirrors lead_quality.py's
     get_read_client() pattern: `google-cloud-bigquery` imported only here,
     own credential resolution, SELECT-only, no write path).
  3. Fallback to the local DRY_RUN preview files bq_writer.py itself writes
     (`generation/staging/bq_preview_<table>.json`) -- covers the common case
     where the same run just executed with DRY_RUN=true and never touched
     live BigQuery at all.
  4. If none of the above yield data, the digest says so plainly (0 counts,
     a "no data available yet" note) rather than crashing the run.

Posting: shells out to `slack_post.py` (the existing, credential-resolving
Slack helper) via subprocess -- this module carries no Slack credentials or
HTTP logic of its own, per the task's "post via slack_post.py's mechanism"
instruction. `--dry-run` (default true) prints the composed message instead
of posting; pass `--no-dry-run` (or run with env `DRY_RUN=false`) to post
for real.

Usage:
    python3 notify_slack.py                              # dry-run, prints only
    python3 notify_slack.py --no-dry-run                  # posts to Slack
    python3 notify_slack.py --run-date 2026-07-04
    python3 notify_slack.py --from-fixture /tmp/digest_fixture.json --no-dry-run
    python3 notify_slack.py --soft-failure 'registry_refresh(exit=4)'
                                       # run_daily.sh passes each fail-soft step that
                                       # failed; the main message flags it
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Optional

import bq_writer
import config

logging.basicConfig(level=logging.INFO,
                     format="%(asctime)s [%(levelname)s] %(message)s",
                     datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("generation.notify_slack")

HERE = Path(__file__).resolve().parent

DASHBOARD_URL = os.environ.get("MIS_DASHBOARD_URL", "http://localhost:3000/generation")

# Tables this digest reads. Kept in one place so tests and the fixture
# loader agree on the exact set.
DIGEST_TABLES = ("generation_runs", "keyword_candidates", "ad_copy_drafts")

TOP_N = 5


# ---------------------------------------------------------------------------
# slack-tools resolution -- same IK_MCP_DIR / WSL-fallback pattern
# collect_kw_perf.py and lead_quality.py use for their own credential dirs,
# applied here to find slack_post.py + its .env.
# ---------------------------------------------------------------------------
def _resolve_mcp_base() -> Path:
    env_override = os.environ.get("IK_MCP_DIR")
    if env_override:
        return Path(env_override)
    default = Path(r"C:\mcp-servers")
    wsl_fallback = Path("/mnt/c/mcp-servers")
    if not default.exists() and wsl_fallback.exists():
        return wsl_fallback
    return default


def _slack_post_path() -> Path:
    override = os.environ.get("SLACK_POST_SCRIPT")
    if override:
        return Path(override)
    return _resolve_mcp_base() / "slack-tools" / "slack_post.py"


# ---------------------------------------------------------------------------
# Lazy, read-only BigQuery client -- mirrors lead_quality.py's
# get_read_client() pattern verbatim (own credential resolution, SELECT
# only, no write path, no module-load-time import).
# ---------------------------------------------------------------------------
_read_client = None


def _resolve_read_credentials_path() -> Path:
    import glob
    env_override = os.environ.get("GENERATION_BQ_READ_SERVICE_ACCOUNT")
    candidate = Path(env_override) if env_override else Path(config.BQ_SERVICE_ACCOUNT)
    if candidate.exists():
        return candidate
    for hit in sorted(glob.glob(f"/mnt/c/Users/*/.gcp-keys/{candidate.name}")):
        hit_path = Path(hit)
        if hit_path.exists():
            return hit_path
    return candidate


def get_read_client():
    """Lazy read-only BigQuery client. Raises FileNotFoundError if no
    credential file is found; raises ImportError if the package is missing.
    Callers must catch both and degrade gracefully -- this module never lets
    a missing BQ dependency crash the digest."""
    global _read_client
    if _read_client is None:
        from google.cloud import bigquery          # lazy: only needed for live reads
        from google.oauth2 import service_account
        path = _resolve_read_credentials_path()
        if not path.exists():
            raise FileNotFoundError(f"notify_slack BQ read credentials not found: {path}")
        creds = service_account.Credentials.from_service_account_file(
            str(path), scopes=["https://www.googleapis.com/auth/cloud-platform"])
        _read_client = bigquery.Client(project=config.BQ_PROJECT, credentials=creds,
                                        location=config.BQ_LOCATION)
    return _read_client


def _table_fqn(logical_table: str) -> str:
    """Fully-qualified table id for a Generation-owned logical table name,
    honoring config.TARGET_DATASET/TABLE_PREFIX exactly like bq_writer.py's
    writes do (so reads always point at the same physical table a same-run
    write just landed in)."""
    return bq_writer.validate_target(logical_table)


def fetch_latest_run_date(logical_table: str, date_col: str = "run_date") -> Optional[str]:
    """SELECT MAX(date_col) from a Generation table. Returns None (not an
    exception) if the table has zero rows, doesn't exist yet, or the read
    fails for any reason -- callers treat None as "no live data available"."""
    try:
        from google.cloud import bigquery  # noqa: F401  (import-availability probe)
        client = get_read_client()
        table_id = _table_fqn(logical_table)
        query = f"SELECT MAX({date_col}) AS max_date FROM `{table_id}`"
        rows = list(client.query(query).result(timeout=60))
        if rows and rows[0]["max_date"]:
            return str(rows[0]["max_date"])
        return None
    except Exception as e:
        log.debug("fetch_latest_run_date(%s): %s", logical_table, e)
        return None


def fetch_table_rows_bq(logical_table: str, run_date_: str,
                         date_col: str = "run_date") -> list[dict]:
    """Live SELECT * for one run_date's rows. SELECT-only; never writes."""
    from google.cloud import bigquery
    client = get_read_client()
    table_id = _table_fqn(logical_table)
    query = f"SELECT * FROM `{table_id}` WHERE {date_col} = @run_date"
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("run_date", "DATE", run_date_),
    ])
    return [dict(r.items()) for r in client.query(query, job_config=job_config).result(timeout=120)]


def fetch_table_rows_staging(logical_table: str) -> list[dict]:
    """Fallback: read bq_writer.py's own DRY_RUN preview file for this
    table, if present. Returns [] if the file doesn't exist -- this is a
    normal, expected state (nothing has run in DRY_RUN mode yet)."""
    preview_path = config.STAGING_DIR / f"bq_preview_{logical_table}.json"
    if not preview_path.exists():
        return []
    try:
        obj = json.loads(preview_path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("Failed to parse %s: %s", preview_path, e)
        return []
    return obj.get("rows", [])


def resolve_run_date(fixture: Optional[dict], explicit_run_date: Optional[str]) -> str:
    """Priority: --run-date override > fixture's own max run_date > latest
    live BQ generation_runs.run_date > latest staging-preview run_date >
    today (IST) as a last-resort label (an empty digest, clearly marked)."""
    if explicit_run_date:
        return explicit_run_date
    if fixture is not None:
        dates = {str(r.get("run_date")) for r in fixture.get("generation_runs", []) if r.get("run_date")}
        if dates:
            return sorted(dates)[-1]
    live = fetch_latest_run_date("generation_runs")
    if live:
        return live
    staging_rows = fetch_table_rows_staging("generation_runs")
    staging_dates = {str(r.get("run_date")) for r in staging_rows if r.get("run_date")}
    if staging_dates:
        return sorted(staging_dates)[-1]
    return bq_writer.run_date_today_ist()


def fetch_digest_rows(logical_table: str, run_date_: str,
                       fixture: Optional[dict]) -> tuple[list[dict], str]:
    """Returns (rows, source_label) where source_label is one of
    "fixture" / "bq" / "staging-preview" / "none" -- surfaced in the digest
    so a reader can tell how fresh/authoritative the numbers are."""
    if fixture is not None:
        rows = [r for r in fixture.get(logical_table, [])
                 if str(r.get("run_date")) == run_date_]
        return rows, "fixture"
    try:
        rows = fetch_table_rows_bq(logical_table, run_date_)
        if rows:
            return rows, "bq"
    except Exception as e:
        log.debug("fetch_table_rows_bq(%s): %s", logical_table, e)
    staging_rows = [r for r in fetch_table_rows_staging(logical_table)
                     if str(r.get("run_date")) == run_date_]
    if staging_rows:
        return staging_rows, "staging-preview"
    return [], "none"


# ---------------------------------------------------------------------------
# Digest composition
# ---------------------------------------------------------------------------
def build_digest(run_date_: str, fixture: Optional[dict] = None) -> dict:
    """Pure(ish) aggregation step: reads the three digest tables for
    run_date_ and returns a structured summary dict. Kept separate from
    `render_digest_text()` so tests can assert on structured counts without
    parsing rendered Slack text."""
    run_rows, run_source = fetch_digest_rows("generation_runs", run_date_, fixture)
    kw_rows, kw_source = fetch_digest_rows("keyword_candidates", run_date_, fixture)
    copy_rows, copy_source = fetch_digest_rows("ad_copy_drafts", run_date_, fixture)

    by_path = {}
    for r in run_rows:
        path = r.get("path") or "unknown"
        by_path[path] = {
            "status": r.get("status"),
            "candidates_generated": r.get("candidates_generated"),
            "passed_filter": r.get("passed_filter"),
            "borderline": r.get("borderline"),
            "rejected": r.get("rejected"),
        }

    verdict_counts = dict(Counter(r.get("relevance_verdict") or "unknown" for r in kw_rows))
    kw_status_counts = dict(Counter(r.get("status") or "unknown" for r in kw_rows))
    copy_status_counts = dict(Counter(r.get("status") or "unknown" for r in copy_rows))

    top_keywords = sorted(
        (r for r in kw_rows if r.get("keyword")),
        key=lambda r: r.get("relevance_score") or 0, reverse=True,
    )[:TOP_N]
    top_copy = copy_rows[:TOP_N]

    return {
        "run_date": run_date_,
        "sources": {"generation_runs": run_source, "keyword_candidates": kw_source,
                    "ad_copy_drafts": copy_source},
        "by_path": by_path,
        "keyword_candidates": {
            "total": len(kw_rows),
            "by_verdict": verdict_counts,
            "by_status": kw_status_counts,
            "top": [{"keyword": r.get("keyword"), "campaign_name": r.get("campaign_name"),
                     "relevance_verdict": r.get("relevance_verdict"),
                     "relevance_score": r.get("relevance_score")} for r in top_keywords],
        },
        "ad_copy_drafts": {
            "total": len(copy_rows),
            "by_status": copy_status_counts,
            "top": [{"headline": r.get("headline"), "campaign_name": r.get("campaign_name"),
                     "platform": r.get("platform"), "status": r.get("status")}
                    for r in top_copy],
        },
        "dashboard_url": DASHBOARD_URL,
    }


def render_digest_text(digest: dict) -> tuple[str, str]:
    """Renders the structured digest into (main_message, thread_detail) text
    for Slack. Main message = headline + link (short, always fits one
    message); thread_detail = the full breakdown (chunked automatically by
    slack_post.py's post_chunked() if it's long)."""
    run_date_ = digest["run_date"]
    kw = digest["keyword_candidates"]
    copy_ = digest["ad_copy_drafts"]

    # Deliberately checks actual row totals, not source labels -- a fixture/
    # BQ/staging read can succeed (source != "none") yet still yield zero
    # rows for this run_date (e.g. an empty fixture, or a real run that
    # generated nothing), and that must render as "no data," not a fake
    # "0 candidates" headline claiming the run happened.
    any_data = bool(digest["by_path"]) or kw["total"] > 0 or copy_["total"] > 0

    # Fail-soft steps run_daily.sh let the chain continue past (--soft-failure,
    # e.g. "registry_refresh(exit=4)") -- in the MAIN message, so a degraded
    # step is never visible only in the Fly logs.
    soft = digest.get("soft_failures") or []
    soft_line = (f":warning: Fail-soft step(s) failed this run: {', '.join(soft)} -- the "
                 f"chain continued; check the run's Fly logs.") if soft else None

    main_lines = [
        f":robot_face: *Generation run digest -- {run_date_}*",
        f"Keywords: {kw['total']} candidates -- {copy_['total']} copy drafts generated"
        if any_data else f"No generation data found for {run_date_} yet.",
        *([soft_line] if soft_line else []),
        f"<{digest['dashboard_url']}|Open the Mission Control review queue>",
    ]
    main_message = "\n".join(main_lines)

    thread_lines = [f"*Run detail -- {run_date_}*"]
    thread_lines.append(f"_data sources: {digest['sources']}_")

    if digest["by_path"]:
        thread_lines.append("\n*By path* (generation_runs):")
        for path, stats in sorted(digest["by_path"].items()):
            thread_lines.append(
                f"  - `{path}`: status={stats['status']}, generated={stats['candidates_generated']}, "
                f"passed={stats['passed_filter']}, borderline={stats['borderline']}, "
                f"rejected={stats['rejected']}")
    else:
        thread_lines.append("\n*By path*: no generation_runs rows for this run_date.")

    thread_lines.append(f"\n*Keyword candidates* ({kw['total']} total):")
    thread_lines.append(f"  verdicts: {kw['by_verdict'] or '(none)'}")
    thread_lines.append(f"  status: {kw['by_status'] or '(none)'}")
    if kw["top"]:
        thread_lines.append(f"  top {len(kw['top'])} by relevance_score:")
        for item in kw["top"]:
            thread_lines.append(
                f"    - `{item['keyword']}` ({item['campaign_name']}) "
                f"verdict={item['relevance_verdict']} score={item['relevance_score']}")

    thread_lines.append(f"\n*Ad copy drafts* ({copy_['total']} total):")
    thread_lines.append(f"  status: {copy_['by_status'] or '(none)'}")
    if copy_["top"]:
        thread_lines.append(f"  top {len(copy_['top'])}:")
        for item in copy_["top"]:
            thread_lines.append(
                f"    - [{item['platform']}] \"{item['headline']}\" "
                f"({item['campaign_name']}) status={item['status']}")

    thread_detail = "\n".join(thread_lines)
    return main_message, thread_detail


# ---------------------------------------------------------------------------
# Posting
# ---------------------------------------------------------------------------
def post_digest(main_message: str, thread_detail: str, dry_run: bool) -> int:
    """Notify-only post: one message + one thread reply, zero interactive
    elements (no blocks, no buttons, no callbacks). In dry_run mode, prints
    what would be posted instead of shelling out."""
    if dry_run:
        print("[DRY RUN] Would post to Slack:")
        print("--- main message ---")
        print(main_message)
        print("--- thread reply ---")
        print(thread_detail)
        return 0

    script = _slack_post_path()
    if not script.exists():
        log.error("slack_post.py not found at %s -- cannot post (set SLACK_POST_SCRIPT or "
                  "IK_MCP_DIR)", script)
        return 1

    result = subprocess.run(
        [sys.executable, str(script), "--message", main_message, "--thread", thread_detail],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        log.error("slack_post.py failed (exit=%s): %s", result.returncode, result.stderr.strip())
        return result.returncode
    log.info("Posted digest to Slack (thread_ts=%s)", result.stdout.strip())
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="After-run Slack digest -- PLAN.md Phase 1 Step 13 (notify-only).")
    p.add_argument("--run-date", type=str, default=None,
                    help="Run date YYYY-MM-DD (default: latest available across "
                         "fixture/live BQ/staging preview).")
    p.add_argument("--from-fixture", type=str, default=None,
                    help="Path to a JSON fixture with "
                         "generation_runs/keyword_candidates/ad_copy_drafts keys -- "
                         "skips every BigQuery read entirely.")
    p.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True,
                    help="Print the composed digest instead of posting to Slack "
                         "(default: true). Pass --no-dry-run to post for real.")
    p.add_argument("--soft-failure", action="append", default=None, metavar="STEP",
                    help="Repeatable: a fail-soft step that failed this run (run_daily.sh "
                         "passes e.g. 'registry_refresh(exit=4)'); flagged in the main "
                         "message.")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    fixture = None
    if args.from_fixture:
        fixture = json.loads(Path(args.from_fixture).read_text(encoding="utf-8"))

    run_date_ = resolve_run_date(fixture, args.run_date)
    log.info("notify_slack: run_date=%s dry_run=%s from_fixture=%s",
             run_date_, args.dry_run, args.from_fixture)

    digest = build_digest(run_date_, fixture)
    digest["soft_failures"] = list(args.soft_failure or [])
    main_message, thread_detail = render_digest_text(digest)
    return post_digest(main_message, thread_detail, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
