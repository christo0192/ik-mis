#!/usr/bin/env python3
"""
Persisted keyword / search-term performance feed collector.

PLAN.md Phase 0 / Step 3: "New collector following the collect_drilldown.py
pattern -- direct GoogleAdsClient GAQL against search_term_view, keyword_view,
ad_group_ad (note: collect_drilldown.py does *not* call the google-ads MCP
tools; it runs its own GAQL -- clone that approach). Land results into
kw_search_term_perf ... via the guarded writer."

Cloned from /mnt/c/mcp-servers/anomaly-engine/collect_drilldown.py's client/
auth pattern (itself identical to collect_data.py's `collect_google_ads()`):
same `IK_MCP_DIR` env var, same `<base>/google-ads/.env` path, same
GOOGLE_ADS_* keys, same GoogleAdsClient.load_from_dict() shape, so this
collector finds credentials wherever the anomaly engine finds them (laptop,
Fly `/app`, macOS `~/mcp-servers`) with zero extra setup.

The one deliberate deviation from collect_drilldown.py: that script writes a
transient staging JSON (staging/drilldown.json) consumed once by the same
day's LLM run. This collector's output is durable -- every row goes through
generation/bq_writer.py's guarded `write_rows("kw_search_term_perf", rows)`,
which validates against schemas.KwSearchTermPerfRow and then either previews
to generation/staging/bq_preview_kw_search_term_perf.json (DRY_RUN, default)
or atomically replaces the affected `date` partition(s) in BigQuery.

Segment classification is INTERIM: it reuses build_segment_tables.py's
classify_google_campaign() substring rule (Brand / Discovery / _nb_)
verbatim, falling back to "Other" for anything unclassified (PMax, YouTube,
etc. -- kept, not dropped, since this feed should cover every search-network
campaign). PLAN.md Phase 0 Step 2 (campaign_registry) is the permanent
replacement for this stopgap -- once that table exists, swap the lookup
inside `_segment_for()` for a campaign_registry read and delete
`classify_google_campaign()` from this file.

Usage:
    python3 collect_kw_perf.py                                  # yesterday, single day
    python3 collect_kw_perf.py --date 2026-07-01                 # a specific day
    python3 collect_kw_perf.py --date 2026-07-01 --days 7        # 7-day backfill window ending on --date
    python3 collect_kw_perf.py --from-fixture fixtures/kw_perf_sample.json
                                                                  # skip the API entirely; load rows from
                                                                  # a JSON fixture (for testing without
                                                                  # google-ads credentials/package)

DRY_RUN (default true, see config.py) always applies on the write side,
regardless of --from-fixture -- set DRY_RUN=false to perform a live
BigQuery load.
"""
import argparse
import json
import logging
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import bq_writer
import config

# ---------------------------------------------------------------------------
# Credential / env resolution -- cloned verbatim from collect_data.py /
# collect_drilldown.py so this collector works wherever the anomaly engine
# works, with no new configuration.
# ---------------------------------------------------------------------------
def _resolve_mcp_base() -> Path:
    """Resolve the anomaly-engine's mcp-servers directory.

    Respects IK_MCP_DIR when set (unchanged). Otherwise defaults to the
    Windows path C:\\mcp-servers -- but under WSL that bare Windows-style
    path does not resolve on Linux, so if it doesn't exist and the WSL mount
    /mnt/c/mcp-servers does, use that instead: same physical box, same
    files, reached via the WSL /mnt/c mount rather than a drive letter.
    Hit this in practice running this collector from WSL.
    """
    env_override = os.environ.get("IK_MCP_DIR")
    if env_override:
        return Path(env_override)
    default = Path(r"C:\mcp-servers")
    wsl_fallback = Path("/mnt/c/mcp-servers")
    if not default.exists() and wsl_fallback.exists():
        return wsl_fallback
    return default


_MCP_BASE = _resolve_mcp_base()
GOOGLE_ADS_ENV = _MCP_BASE / "google-ads" / ".env"


def load_env(path: Path) -> dict:
    """Load a .env file into a dict. Skips comments and blank lines.

    Verbatim clone of collect_data.py's load_env -- plain-text .env reader,
    no YAML, no fallback path. Raises FileNotFoundError if `path` is missing.
    """
    env = {}
    if not path.exists():
        raise FileNotFoundError(f"Env file not found: {path}")
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                val = value.strip()
                # Strip surrounding quotes (single or double)
                if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                    val = val[1:-1]
                env[key.strip()] = val
    return env


def _sanitize_error(error_str):
    """Remove potential credential fragments from error messages.

    Verbatim clone of collect_data.py's _sanitize_error.
    """
    if not error_str:
        return error_str
    for pattern in (r'Bearer [A-Za-z0-9\-_.]+', r'token=[A-Za-z0-9\-_.]+',
                    r'key=[A-Za-z0-9\-_.]+', r'secret=[A-Za-z0-9\-_.]+',
                    r'refresh_token=[A-Za-z0-9\-_.]+'):
        error_str = re.sub(pattern, '[REDACTED]', error_str, flags=re.IGNORECASE)
    return error_str[:500]


# ---------------------------------------------------------------------------
# Segment classification -- INTERIM (see module docstring). Cloned verbatim
# from build_segment_tables.classify_google_campaign(); PLAN.md Phase 0 Step
# 2 (campaign_registry) replaces this once it exists.
# ---------------------------------------------------------------------------
def classify_google_campaign(name):
    """Classify a Google Ads campaign name into a paid segment.

    Order matters: Brand before NB/Discovery. Returns None for YouTube/PMax/
    anything unrecognised (caller maps that to "Other" -- see _segment_for).
    """
    n = (name or "").lower()
    if "brand" in n:
        return "Brand"
    if "discovery" in n:
        return "Discovery"
    if "_nb_" in n:
        return "NB"
    return None


def _segment_for(campaign_name):
    return classify_google_campaign(campaign_name) or "Other"


logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("collect_kw_perf")


# ---------------------------------------------------------------------------
# Google Ads client + GAQL pulls
# ---------------------------------------------------------------------------
def _client_and_customer_id():
    """Build a GoogleAdsClient from the same .env credentials
    collect_drilldown.py / collect_data.py use. Raises FileNotFoundError if
    the .env is missing, KeyError if a required key is absent from it."""
    from google.ads.googleads.client import GoogleAdsClient  # lazy: only needed for live pulls
    env = load_env(GOOGLE_ADS_ENV)
    customer_id = env["GOOGLE_ADS_CUSTOMER_ID"].replace("-", "")
    client = GoogleAdsClient.load_from_dict({
        "developer_token": env["GOOGLE_ADS_DEVELOPER_TOKEN"],
        "client_id": env["GOOGLE_ADS_CLIENT_ID"],
        "client_secret": env["GOOGLE_ADS_CLIENT_SECRET"],
        "refresh_token": env["GOOGLE_ADS_REFRESH_TOKEN"],
        "login_customer_id": customer_id,
        "use_proto_plus": True,
    })
    return client, customer_id


def fetch_search_terms(client, customer_id, start: date, end: date) -> list[dict]:
    """search_term_view rows -> raw dicts (pre-segment; term_type="search_term").

    No quality score at this grain (QS is a keyword-level signal) --
    quality_score is always None here.
    """
    ga = client.get_service("GoogleAdsService")
    query = f"""
        SELECT segments.date, campaign.name, search_term_view.search_term,
               segments.search_term_match_type,
               metrics.impressions, metrics.clicks, metrics.ctr,
               metrics.average_cpc, metrics.cost_micros
        FROM search_term_view
        WHERE segments.date BETWEEN '{start}' AND '{end}'
          AND metrics.impressions > 0
    """
    rows = []
    for r in ga.search(customer_id=customer_id, query=query):
        mt = r.segments.search_term_match_type
        rows.append({
            "date": r.segments.date,
            "campaign_name": r.campaign.name,
            "keyword_or_term": r.search_term_view.search_term,
            "term_type": "search_term",
            "match_type": mt.name.lower() if mt is not None and mt.name != "UNKNOWN" else None,
            "impressions": int(r.metrics.impressions),
            "clicks": int(r.metrics.clicks),
            "ctr": round(float(r.metrics.ctr), 4),
            "avg_cpc_inr": round(r.metrics.average_cpc / 1e6, 2),
            "cost_inr": round(r.metrics.cost_micros / 1e6, 2),
            "quality_score": None,
        })
    return rows


def fetch_keywords(client, customer_id, start: date, end: date) -> list[dict]:
    """keyword_view rows -> raw dicts (pre-segment; term_type="keyword"),
    including Quality Score (1-10; 0/unset -> None to satisfy the schema's
    ge=1 constraint)."""
    ga = client.get_service("GoogleAdsService")
    query = f"""
        SELECT segments.date, campaign.name, ad_group_criterion.keyword.text,
               ad_group_criterion.keyword.match_type,
               ad_group_criterion.quality_info.quality_score,
               metrics.impressions, metrics.clicks, metrics.ctr,
               metrics.average_cpc, metrics.cost_micros
        FROM keyword_view
        WHERE segments.date BETWEEN '{start}' AND '{end}'
          AND metrics.impressions > 0
    """
    rows = []
    for r in ga.search(customer_id=customer_id, query=query):
        mt = r.ad_group_criterion.keyword.match_type
        qs = int(r.ad_group_criterion.quality_info.quality_score)
        rows.append({
            "date": r.segments.date,
            "campaign_name": r.campaign.name,
            "keyword_or_term": r.ad_group_criterion.keyword.text,
            "term_type": "keyword",
            "match_type": mt.name.lower() if mt is not None and mt.name != "UNKNOWN" else None,
            "impressions": int(r.metrics.impressions),
            "clicks": int(r.metrics.clicks),
            "ctr": round(float(r.metrics.ctr), 4),
            "avg_cpc_inr": round(r.metrics.average_cpc / 1e6, 2),
            "cost_inr": round(r.metrics.cost_micros / 1e6, 2),
            "quality_score": qs if 1 <= qs <= 10 else None,
        })
    return rows


def to_perf_rows(raw_rows: list[dict]) -> list[dict]:
    """Attach the interim `segment` field -- the last step before these dicts
    are schema-shaped enough for bq_writer.write_rows()."""
    out = []
    for r in raw_rows:
        row = dict(r)
        row["segment"] = _segment_for(row["campaign_name"])
        out.append(row)
    return out


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
# CLI
# ---------------------------------------------------------------------------
def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Persisted keyword/search-term performance feed collector "
                     "(PLAN.md Phase 0 Step 3).")
    p.add_argument("--date", type=str, default=None,
                    help="End date YYYY-MM-DD for the pull window (default: yesterday).")
    p.add_argument("--days", type=int, default=1,
                    help="Backfill window size in days, ending at --date (default: 1, a single day).")
    p.add_argument("--force", action="store_true",
                    help="Required to allow --days > 90 (safety cap against accidental huge pulls).")
    p.add_argument("--from-fixture", type=str, default=None,
                    help="Path to a JSON fixture of rows -- skips the Google Ads API entirely "
                         "(for testing without credentials/the google-ads package).")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.from_fixture:
        rows = load_fixture(args.from_fixture)
        log.info("Loaded %d row(s) from fixture %s (API pull skipped)", len(rows), args.from_fixture)
    else:
        if args.date:
            try:
                end = date.fromisoformat(args.date)
            except ValueError:
                log.error("--date must be YYYY-MM-DD (got %r)", args.date)
                return 1
        else:
            # IST, not container-local: bq_writer.run_date_today_ist() is the
            # same IST-aware clock the writer stamps runs with, so "yesterday"
            # here matches what the writer considers "today" (a container
            # running UTC must not label this with the wrong calendar date).
            end = date.fromisoformat(bq_writer.run_date_today_ist()) - timedelta(days=1)

        if args.days < 1:
            log.error("--days must be >= 1 (got %d)", args.days)
            return 1
        if args.days > 90 and not args.force:
            log.error("--days %d exceeds the 90-day safety cap; pass --force to override.",
                      args.days)
            return 1
        start = end - timedelta(days=args.days - 1)
        log.info("Pulling keyword/search-term performance for %s..%s", start, end)

        try:
            client, customer_id = _client_and_customer_id()
        except FileNotFoundError as e:
            log.error("Google Ads credentials not found: %s", _sanitize_error(str(e)))
            return 1
        except KeyError as e:
            log.error("Google Ads .env is missing a required key: %s", _sanitize_error(str(e)))
            return 1
        except ImportError as e:
            log.error("google-ads package not importable: %s", _sanitize_error(str(e)))
            return 1

        try:
            search_terms = fetch_search_terms(client, customer_id, start, end)
            keywords = fetch_keywords(client, customer_id, start, end)
        except Exception as e:  # GoogleAdsException + anything else the API can raise
            log.error("Google Ads API pull failed: %s", _sanitize_error(str(e)))
            return 1

        log.info("Pulled %d search-term row(s), %d keyword row(s)", len(search_terms), len(keywords))
        rows = to_perf_rows(search_terms) + to_perf_rows(keywords)

    if not rows:
        log.info("No rows to write -- nothing pulled for this window.")
        return 0

    try:
        written = bq_writer.write_rows("kw_search_term_perf", rows)
    except Exception as e:
        log.error("Write to kw_search_term_perf failed: %s", _sanitize_error(str(e)))
        return 1
    if config.DRY_RUN:
        log.info("DRY_RUN: preview written to %s (%d row(s))",
                  config.STAGING_DIR / "bq_preview_kw_search_term_perf.json", len(rows))
    else:
        log.info("Wrote %d row(s) to BigQuery (kw_search_term_perf).", written)
    return 0


if __name__ == "__main__":
    sys.exit(main())
