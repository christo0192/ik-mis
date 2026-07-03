#!/usr/bin/env python3
"""
Lead-quality signal (CPQL) -- read-only pull-forward.

PLAN.md Phase 1 / Step 7: "Read-only job joining lead outcomes (Sale / Junk /
Unsuitable from India_Leads + Swati Sheet ...) to campaigns/segments ... ->
write lead_quality_scores (campaign, segment, period, leads, quality_leads,
quality_ratio, cpql)." India_Leads is READ-ONLY, non-negotiable, and the
Swati Sheet (not ad-platform conversions) is the revenue/outcome truth --
see the module-level discovery notes below and README.md's "Step 7" section
for the full writeup (source tables, columns, and the semantic assumptions
that need data-team sign-off).

------------------------------------------------------------------------
DISCOVERY (how lead outcomes are actually labeled -- verified by reading
/mnt/c/mcp-servers/anomaly-engine/*.py and /mnt/c/mcp-servers/google-sheets/,
and by a live BigQuery read against both candidate sources; see README.md):

  * Base truth lives in `ik-marketing-data.India_Leads.master_view_leads_india`
    (per-lead grain). A BQ view, `ik-marketing-data.India_Leads.v_campaign_funnel`
    (defined in /mnt/c/mcp-servers/google-sheets/v_campaign_funnel.sql),
    pre-aggregates it to (date, campaign_name=UTM_Campaign, channel,
    utm_source) with: iql = COUNT(*) [total leads], wa, dcd, rte,
    junk_unsuitable = COUNTIF(lead_status IN ('Junk','Unsuitable')),
    sales = COUNTIF(sale_date IS NOT NULL), revenue = SUM(net_revenue).
    Both keys tested for this job (ads-anomaly-bq@ and claude-code-dev-bq@)
    got `403 Access Denied` on the view's underlying `Tech_ML_Projects.
    lead_history` table -- direct India_Leads reads are NOT reachable with
    the credentials available in this environment (see README "VERIFY WITH
    DATA TEAM").

  * The anomaly-engine's own ETL (build_dashboard.py) already re-joins
    v_campaign_funnel against Google/LinkedIn Ads spend AND the Swati Sheet
    (Google Sheet id in /mnt/c/mcp-servers/google-sheets/.env,
    GOOGLE_SHEETS_SHEET_ID, tab "swati sheet"; sales/revenue columns =
    "Deal amount in INR" x (1 - 18% GST), joined to a campaign via the
    sheet's "Course/Campaign (From UTM)" trailing campaign-id) and writes
    the result to `ik-marketing-data.SIP_pattern_rag.pmis_full_funnel`, one
    row per (date, campaign). That table IS reachable with the
    `claude-code-dev-bq@` key (confirmed via a live SELECT -- 3,760 rows,
    219 distinct campaigns, 2026-05-11..2026-07-03 at probe time) and is
    exactly the join this job needs already assembled: campaign, channel,
    platform, spend, iql, junk, sales, revenue per (date, campaign). This
    job reads `pmis_full_funnel` rather than India_Leads directly --
    same underlying India_Leads-derived numbers, reachable credentials,
    read-only either way.

  * "Quality" here = NOT Junk/Unsuitable (leads - junk), not "became a Sale"
    -- see README "VERIFY WITH DATA TEAM" for why, and what a Sale-based
    definition would require.
------------------------------------------------------------------------

Live path: SELECT (date, campaign, channel, platform, spend, iql, junk) from
`pmis_full_funnel` for the period, group by campaign (deriving `segment` via
_segment_for -- see below), then derive quality_ratio / cpl_inr / cpql_inr
with division-by-zero guards. Write via bq_writer.write_rows("lead_quality_
scores", rows) (DRY_RUN default true, per config.py).

This module's read path is entirely separate from bq_writer.py: it builds
its own read-only `google.cloud.bigquery.Client` (lazy import, only needed
for the live --period path -- `--from-fixture` needs neither the package nor
credentials) and never constructs a LoadJobConfig or calls a write method.
The only way this job writes anything, live or DRY_RUN, is through
bq_writer.write_rows(), which independently re-validates the allowlist and
schema.

Usage:
    python3 lead_quality.py                                    # trailing 30 days ending yesterday (IST)
    python3 lead_quality.py --period-days 7                    # trailing 7 days
    python3 lead_quality.py --run-date 2026-07-03               # pin the run date (period is relative to it)
    python3 lead_quality.py --from-fixture fixtures/lead_quality_sample.json
                                                                  # skip BigQuery entirely; load raw rows from
                                                                  # a JSON fixture (for testing without credentials)
"""
import argparse
import glob
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import bq_writer
import config
from collect_kw_perf import classify_google_campaign

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("lead_quality")

# ---------------------------------------------------------------------------
# Source table (read-only). Independent of config.TARGET_DATASET/TABLE_PREFIX
# on purpose: pmis_full_funnel is the anomaly-engine's own table, physically
# always in SIP_pattern_rag, regardless of which dataset Generation's own
# writes are currently pointed at (config.GENERATION_DATASET_MODE).
# ---------------------------------------------------------------------------
PMIS_SOURCE_DATASET = os.environ.get("GENERATION_PMIS_SOURCE_DATASET", "SIP_pattern_rag")
PMIS_FULL_FUNNEL_TABLE = os.environ.get("GENERATION_PMIS_FULL_FUNNEL_TABLE", "pmis_full_funnel")


# ---------------------------------------------------------------------------
# Segment classification -- reuses collect_kw_perf.py's interim Google-name
# substring classifier verbatim (imported, not duplicated), per PLAN.md's
# instruction for this job. One addition this job needs that collect_kw_perf
# never did: this job also sees LinkedIn rows (via pmis_full_funnel), which a
# classifier named `classify_google_campaign` cannot and should not try to
# classify by name. Mirrors build_segment_tables.py's own convention of
# keying LinkedIn off channel/platform rather than a name substring. Falls
# through to the Google-name classifier, then "Other" -- the same terminal
# fallback collect_kw_perf.py's _segment_for() uses. PLAN.md Phase 0 Step 2
# (campaign_registry) is the permanent replacement for all of this.
# ---------------------------------------------------------------------------
def _segment_for(campaign_name, channel=None, platform=None):
    if (channel or "").strip().lower() == "linkedin" or (platform or "").strip().lower() == "linkedin":
        return "LinkedIn"
    return classify_google_campaign(campaign_name) or "Other"


# ---------------------------------------------------------------------------
# Read-only BigQuery client -- lazy import, own credential resolution.
# Deliberately has no write path: no LoadJobConfig, no write_disposition, no
# call to any *.load_table_from_json/insert_rows method. Writes go
# exclusively through bq_writer.write_rows() (see main()).
# ---------------------------------------------------------------------------
_read_client = None


def _resolve_read_credentials_path() -> Path:
    """Resolve the service-account key for this job's read-only SELECTs.

    Same style as config.py: env-var override with a sane default, no
    machine-specific path hardcoded as the primary answer. Defaults to the
    same key config.py's writer would use for live writes
    (config.BQ_SERVICE_ACCOUNT) -- the anomaly-engine's own
    `claude-code-dev-bq@` key already has read+write on `SIP_pattern_rag`
    (it is the account that writes pmis_full_funnel), so no new credential
    is required for this read-only job. `GENERATION_BQ_READ_SERVICE_ACCOUNT`
    overrides independently, in case a narrower read-only key is issued
    later.

    WSL fallback: under WSL, Path.home() resolves to the Linux home (no
    `.gcp-keys` there in this environment) while the real key lives under
    the Windows user profile mounted at /mnt/c/Users/<name>/.gcp-keys/ --
    the same class of Windows/WSL path mismatch collect_kw_perf.py's
    _resolve_mcp_base() documents for IK_MCP_DIR. Glob for it rather than
    hardcoding a username.
    """
    env_override = os.environ.get("GENERATION_BQ_READ_SERVICE_ACCOUNT")
    candidate = Path(env_override) if env_override else Path(config.BQ_SERVICE_ACCOUNT)
    if candidate.exists():
        return candidate
    for hit in sorted(glob.glob(f"/mnt/c/Users/*/.gcp-keys/{candidate.name}")):
        hit_path = Path(hit)
        if hit_path.exists():
            return hit_path
    return candidate  # let the caller raise FileNotFoundError naming this path


def get_read_client():
    """Lazy, read-only BigQuery client. Raises FileNotFoundError if no
    credential file is found at the resolved path."""
    global _read_client
    if _read_client is None:
        from google.cloud import bigquery          # lazy: only needed for live reads
        from google.oauth2 import service_account
        path = _resolve_read_credentials_path()
        if not path.exists():
            raise FileNotFoundError(f"Lead-quality BQ read credentials not found: {path}")
        creds = service_account.Credentials.from_service_account_file(
            str(path), scopes=["https://www.googleapis.com/auth/cloud-platform"])
        _read_client = bigquery.Client(project=config.BQ_PROJECT, credentials=creds,
                                        location=config.BQ_LOCATION)
    return _read_client


def fetch_lead_outcomes(period_start: date, period_end: date) -> list[dict]:
    """Live SELECT against pmis_full_funnel for [period_start, period_end].

    Returns raw per-(date, campaign) rows (the same shape
    fixtures/lead_quality_sample.json uses) -- aggregation across dates and
    derivation of quality_ratio/cpl/cpql happen in aggregate_and_derive(),
    shared with the --from-fixture path. SELECT only; never writes."""
    from google.cloud import bigquery          # lazy: only needed for live reads
    client = get_read_client()
    query = f"""
        SELECT date, campaign, channel, platform, spend, iql, junk
        FROM `{config.BQ_PROJECT}.{PMIS_SOURCE_DATASET}.{PMIS_FULL_FUNNEL_TABLE}`
        WHERE date BETWEEN @period_start AND @period_end
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("period_start", "DATE", period_start),
        bigquery.ScalarQueryParameter("period_end", "DATE", period_end),
    ])
    rows = []
    for r in client.query(query, job_config=job_config).result(timeout=120):
        rows.append({
            "date": str(r["date"]) if r["date"] else None,
            "campaign": r.get("campaign") or "",
            "channel": r.get("channel"),
            "platform": r.get("platform"),
            "spend": float(r.get("spend") or 0),
            "iql": int(r.get("iql") or 0),
            "junk": int(r.get("junk") or 0),
        })
    return rows


# ---------------------------------------------------------------------------
# Fixture loading (--from-fixture; no API/credentials needed)
# ---------------------------------------------------------------------------
def load_fixture(path) -> list[dict]:
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = obj["rows"] if isinstance(obj, dict) and "rows" in obj else obj
    if not isinstance(rows, list):
        raise ValueError(f"Fixture {path} must contain a JSON list of rows "
                          f"(or an object with a 'rows' list)")
    return rows


# ---------------------------------------------------------------------------
# Aggregation + derivation (shared by the fixture path and the live path)
# ---------------------------------------------------------------------------
def aggregate_and_derive(raw_rows: list[dict], period_start: date, period_end: date,
                          run_date: date) -> list[dict]:
    """Group raw per-(date, campaign) rows into per-(campaign, segment) sums
    over the period, then derive quality_ratio/cpl_inr/cpql_inr with
    division-by-zero guards (cpql_inr is null, not inf, when quality_leads
    is 0). Output rows conform to schemas.LeadQualityScoreRow field-for-field.
    """
    grouped = defaultdict(lambda: {"leads": 0, "junk_leads": 0, "spend_inr": 0.0})
    for r in raw_rows:
        campaign = r.get("campaign") or r.get("campaign_name")
        if not campaign:
            continue
        segment = _segment_for(campaign, r.get("channel"), r.get("platform"))
        g = grouped[(campaign, segment)]
        g["leads"] += int(r.get("iql") or 0)
        g["junk_leads"] += int(r.get("junk") or 0)
        g["spend_inr"] += float(r.get("spend") or 0)

    rows = []
    for (campaign, segment), g in grouped.items():
        leads = g["leads"]
        # Defensive clamp: junk_unsuitable is a COUNTIF subset of iql at the
        # source view's grain, so junk_leads should never exceed leads, but
        # a clamp keeps quality_ratio in [0, 1] (the schema's own guarantee)
        # even if a future source violates that invariant.
        junk_leads = min(g["junk_leads"], leads) if leads else g["junk_leads"]
        quality_leads = max(leads - junk_leads, 0)
        spend_inr = round(g["spend_inr"], 2)
        quality_ratio = round(quality_leads / leads, 4) if leads > 0 else None
        cpl_inr = round(spend_inr / leads, 2) if leads > 0 else None
        cpql_inr = round(spend_inr / quality_leads, 2) if quality_leads > 0 else None
        rows.append({
            "period_start": str(period_start),
            "period_end": str(period_end),
            "run_date": str(run_date),
            "campaign_name": campaign,
            "segment": segment,
            "leads": leads,
            "quality_leads": quality_leads,
            "junk_leads": junk_leads,
            "quality_ratio": quality_ratio,
            "spend_inr": spend_inr,
            "cpl_inr": cpl_inr,
            "cpql_inr": cpql_inr,
        })
    return rows


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Lead-quality signal (CPQL) job -- PLAN.md Phase 1 Step 7 "
                     "(read-only pull-forward; writes only to lead_quality_scores).")
    p.add_argument("--period-days", type=int, default=30,
                    help="Trailing window size in days, ending the day before "
                         "--run-date (default 30).")
    p.add_argument("--run-date", type=str, default=None,
                    help="Run date YYYY-MM-DD (default: today IST via "
                         "bq_writer.run_date_today_ist()).")
    p.add_argument("--from-fixture", type=str, default=None,
                    help="Path to a JSON fixture of RAW pmis_full_funnel-shaped "
                         "rows -- skips the BigQuery read entirely (for testing "
                         "without credentials/the google-cloud-bigquery package).")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.run_date:
        try:
            run_date = date.fromisoformat(args.run_date)
        except ValueError:
            log.error("--run-date must be YYYY-MM-DD (got %r)", args.run_date)
            return 1
    else:
        run_date = date.fromisoformat(bq_writer.run_date_today_ist())

    if args.period_days < 1:
        log.error("--period-days must be >= 1 (got %d)", args.period_days)
        return 1

    period_end = run_date - timedelta(days=1)
    period_start = period_end - timedelta(days=args.period_days - 1)

    if args.from_fixture:
        raw_rows = load_fixture(args.from_fixture)
        log.info("Loaded %d raw row(s) from fixture %s (BigQuery read skipped)",
                 len(raw_rows), args.from_fixture)
    else:
        log.info("Pulling lead-quality outcomes for %s..%s from %s.%s.%s",
                 period_start, period_end, config.BQ_PROJECT, PMIS_SOURCE_DATASET,
                 PMIS_FULL_FUNNEL_TABLE)
        try:
            raw_rows = fetch_lead_outcomes(period_start, period_end)
        except FileNotFoundError as e:
            log.error("BigQuery read credentials not found: %s", e)
            return 1
        except ImportError as e:
            log.error("google-cloud-bigquery not importable: %s", e)
            return 1
        except Exception as e:
            log.error("Live read of %s failed: %s", PMIS_FULL_FUNNEL_TABLE, e)
            return 1
        log.info("Pulled %d raw row(s)", len(raw_rows))

    if not raw_rows:
        log.info("No rows for %s..%s -- nothing to write.", period_start, period_end)
        return 0

    rows = aggregate_and_derive(raw_rows, period_start, period_end, run_date)
    log.info("Aggregated to %d campaign x segment row(s)", len(rows))

    try:
        written = bq_writer.write_rows("lead_quality_scores", rows)
    except Exception as e:
        log.error("Write to lead_quality_scores failed: %s", e)
        return 1

    if config.DRY_RUN:
        log.info("DRY_RUN: preview written to %s (%d row(s))",
                 config.STAGING_DIR / "bq_preview_lead_quality_scores.json", len(rows))
    else:
        log.info("Wrote %d row(s) to BigQuery (lead_quality_scores).", written)
    return 0


if __name__ == "__main__":
    sys.exit(main())
