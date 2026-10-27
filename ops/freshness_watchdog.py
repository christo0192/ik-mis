#!/usr/bin/env python3
"""
Freshness watchdog -- M001 T05 (pipeline-freshness incident, verified
2026-10-27).

Why this exists: on 2026-10-27 the MIS dashboard tiles were found stale and
NOTHING had alerted. pmis_daily_segment_facts had been stuck at 2026-10-15
for 12 days (LinkedIn-Version 202503 -> 426), the five Market_Research
tables were 63-70 days old (the Fly-native monthly schedule never fired),
ce_campaign_registry was 115 days old (never scheduled), and
ce_value_ledger had a silent 20-day gap (09-19..10-09). Every one of those
runs "succeeded" from its own point of view. This job checks the DATA, not
the runs: once a day it reads MAX(<date col>) per table and posts to Slack
when any table is older than its SLA.

Design (see ops/README.md for the SLA table and the runbook):
  - SELECT-only. One UNION ALL query of MAX(date) per table; if that fails
    (one 403, one missing table, or a cross-location union) it falls back to
    one query per table, so a single bad table becomes a "NO DATA" line
    instead of crashing the run. A service-wide failure (5xx, rate limit,
    retries exhausted) skips that fallback, and the whole read has a hard
    time budget, so an outage exits 1 instead of hanging past the job
    timeout. There is no BigQuery write path here.
  - Stateless daily dedup. `late_since` is derived from the data itself
    (latest + max_lag + 1), so the cron firing once a day IS the dedup: the
    alert repeats daily while a table is late and pages (WATCHDOG_MENTION,
    default <!here>) only on escalation days 1, 3, 7, 14, 21, ... No state
    store, no BQ write.
  - Pure core. evaluate(), expiry_status() and render_message() do no I/O,
    so tests run with plain stdlib and no credentials. google-cloud-bigquery
    is imported lazily inside build_client() only.
  - Expiry calendar. EXPIRIES lists dated credential/API deadlines (the
    LinkedIn refresh token, the LinkedIn-Version sunset) and warns 30 days
    ahead -- the 2026-10-27 root cause was exactly such a silent deadline.

Exit codes (any non-success run opens or updates a GitHub issue assigned to
the repo owner -- the workflow's alert-on-failure job -- which is the backstop
when Slack or BQ credentials themselves break):
  0  run succeeded (breaches posted, or nothing to post)
  1  watchdog infrastructure failure: missing env var, BQ auth/query failure
     for ALL tables, a BigQuery outage (5xx / rate limit / retry exhausted),
     the read running past FETCH_BUDGET_S, or the Slack post failed
  2  only with --fail-on-breach: at least one alert-severity table breach
     (after the message was posted / printed successfully)

Env (secrets are read from env only and never logged):
  WATCHDOG_BQ_SA_JSON  full service-account key JSON (never written to disk)
  SLACK_BOT_TOKEN      bot token with chat:write (not needed for --dry-run)
  SLACK_CHANNEL_ID     channel id, e.g. #ib-perf (not needed for --dry-run)
  BQ_PROJECT           default ik-marketing-data
  WATCHDOG_DRY_RUN     "true" = same as --dry-run
  WATCHDOG_MENTION     default "<!here>"; "" (or "none"/"off") disables it

Usage:
    python ops/freshness_watchdog.py --dry-run                 # print, never post
    python ops/freshness_watchdog.py --heartbeat always        # post all-green too
    python ops/freshness_watchdog.py --dry-run --today 2026-10-27
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("ops.freshness_watchdog")

# Same fixed-offset IST as generation/bq_writer.py:46. India has no DST, and
# zoneinfo("Asia/Kolkata") needs the tzdata package on Windows, which the
# credential-free local test run (`uv run --no-project ...`) does not have.
IST = timezone(timedelta(hours=5, minutes=30), "IST")

DEFAULT_PROJECT = "ik-marketing-data"
# NOT bigquery.readonly: that scope gets 403 "insufficient authentication
# scopes" on jobs.insert, which every query needs (verified 2026-10-27).
# Read-only behavior comes from IAM + this module being SELECT-only.
CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
# Hard time budget for the BigQuery read (review-2). google-cloud-bigquery
# 3.46 by default retries each API call for 600s, wraps jobs.insert in a
# private 600s insert retry and result() in a 2400s job retry, so a BigQuery
# outage would outlive the workflow's 10-minute job timeout: the run would end
# "cancelled" (no failure email) instead of exit 1. Every live query goes
# through _BoundedClient (30s call retry, job_retry=None -- the only public
# way to drop both job-level retries), and fetch_latest() stops the per-table
# fallback once FETCH_BUDGET_S is used up. FETCH_BUDGET_S + QUERY_WORST_CASE_S
# must stay under the workflow's `timeout 8m` wrapper (the tests pin that, and
# replay an outage through the real library on a fake clock).
QUERY_TIMEOUT_S = 60          # result(): wait for one query to finish
BQ_REQUEST_TIMEOUT_S = 20     # one HTTP request (jobs.insert)
BQ_RETRY_S = 30               # retries of one API call (insert / get / getQueryResults)
QUERY_WORST_CASE_S = (BQ_REQUEST_TIMEOUT_S + BQ_RETRY_S             # insert
                      + QUERY_TIMEOUT_S + BQ_RETRY_S + QUERY_TIMEOUT_S)  # result: wait + last poll
FETCH_BUDGET_S = 240          # whole read: UNION ALL + per-table fallback
# BigQuery error reasons that mean "the service", not "this table": the
# per-table fallback is skipped for these (it would only repeat the outage).
_SYSTEMIC_REASONS = frozenset({"backendError", "internalError", "jobBackendError",
                               "jobInternalError", "rateLimitExceeded"})

SLACK_POST_URL = "https://slack.com/api/chat.postMessage"
MAX_MESSAGE_CHARS = 3900          # same cap as slack-tools/slack_post.py:43
DEFAULT_MENTION = "<!here>"
EXPIRY_WARN_DAYS = 30

SEVERITIES = ("alert", "warn")
_IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")
_PROJECT_RE = re.compile(r"^[A-Za-z0-9-]+$")


# ---------------------------------------------------------------------------
# Table SLAs
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TableSpec:
    dataset: str
    table: str
    date_col: str
    max_lag_days: int
    pipeline: str
    severity: str = "alert"
    note: str = ""

    @property
    def key(self) -> str:
        return f"{self.dataset}.{self.table}"


_CE = "SIP_pattern_rag"     # ce_* (generation) and pmis_* (anomaly) both live here
_MR = "Market_Research"

GEN = "ik-ad-generator"
ANOMALY = "ik-anomaly"
MR_JOBS = "ik-market-research-jobs"
MR_PIPELINE = "ik-market-research-weekly"   # Fly app name; cadence is MONTHLY since M001

# The rule: max_lag_days = healthy base lag + 1, so ONE missed run never
# alarms and TWO consecutive misses do. "Base lag" = today_IST - MAX(date)
# right after a healthy run, observed 2026-10-27 over the last 30 distinct
# dates (research-watchdog.md). Lags are IST calendar days.
SPECS: list[TableSpec] = [
    # -- Generation (ik-ad-generator, run_daily.sh, Cloudflare cron 05:30 UTC).
    # run_date = IST run day, written 05:31-06:44 UTC -> base lag 0.
    TableSpec(_CE, "ce_generation_runs", "run_date", 1, GEN),
    TableSpec(_CE, "ce_keyword_candidates", "run_date", 1, GEN),
    TableSpec(_CE, "ce_ad_copy_drafts", "run_date", 1, GEN),
    TableSpec(_CE, "ce_audience_angles", "run_date", 1, GEN),
    TableSpec(_CE, "ce_lead_quality_scores", "run_date", 1, GEN),
    # date = yesterday's search terms -> base lag 1.
    TableSpec(_CE, "ce_kw_search_term_perf", "date", 2, GEN),
    # run_date = latest kw_search_term_perf date (export_negatives.py:472-475)
    # -> base lag 1. Had a silent 20-day gap 09-19..10-09.
    TableSpec(_CE, "ce_value_ledger", "run_date", 2, GEN),
    # Written only when the gate produces rejects (gate_keywords.py:494-499),
    # observed gaps up to 3 days -> 5, and only a warning.
    TableSpec(_CE, "ce_keyword_rejects", "run_date", 5, GEN, "warn",
              note="sparse: written only when the gate rejects"),
    # Weekly carry-forward refresh on Monday IST (run_daily.sh fail-soft step
    # registry_refresh), plus a catch-up whenever the snapshot is >7 days
    # old -> base lag 7, SLA 8. Alert, not warn: a stale registry silently
    # misroutes generation for new campaigns (115 days stale on 2026-10-27).
    TableSpec(_CE, "ce_campaign_registry", "snapshot_date", 8, GEN,
              note="weekly Mon IST registry_refresh step (fail-soft)"),

    # -- Anomaly engine (ik-anomaly, Cloudflare cron 04:30 UTC, done ~04:38).
    # Liveness signals: date/run_date = IST run day -> base lag 0.
    TableSpec(_CE, "pmis_run_metadata", "run_date", 1, ANOMALY),
    TableSpec(_CE, "pmis_full_funnel", "date", 1, ANOMALY),
    # date/run_date = data date (yesterday) -> base lag 1. segment_facts is the
    # table that froze at 2026-10-15 while every run said "all steps ok".
    TableSpec(_CE, "pmis_daily_segment_facts", "date", 2, ANOMALY),
    TableSpec(_CE, "pmis_anomaly_facts", "run_date", 2, ANOMALY),
    # Same run, not read by the MIS dashboard -> warn only (secondary signal;
    # if the whole run dies, pmis_run_metadata/full_funnel already alert).
    TableSpec(_CE, "pmis_daily_summary", "date", 1, ANOMALY, "warn"),
    TableSpec(_CE, "pmis_platform_summary", "date", 1, ANOMALY, "warn"),
    TableSpec(_CE, "pmis_program_summary", "date", 1, ANOMALY, "warn"),
    TableSpec(_CE, "pmis_recommendations", "run_date", 2, ANOMALY, "warn"),

    # -- Market Research, MONTHLY since M001 (Cloudflare ik-ad-generator-trigger,
    # cron 30 5 * * *: jobs on the 1st, the pipeline on the 2nd, 05:30 UTC;
    # ik-intelligence-trigger has no cron, free-plan quota). Longest healthy gap
    # is a 31-day month -> 35 (31 + 4 days grace). Was 63-70 days stale on
    # 2026-10-27 because the Fly-native schedule=monthly never fired.
    TableSpec(_MR, "job_market_demand", "run_date", 35, MR_JOBS),
    TableSpec(_MR, "social_trends", "run_date", 35, MR_PIPELINE),
    TableSpec(_MR, "blog_publications", "run_date", 35, MR_PIPELINE),
    TableSpec(_MR, "competitor_intel", "run_date", 35, MR_PIPELINE),
    TableSpec(_MR, "executive_summary", "run_date", 35, MR_PIPELINE),
]

# Per-pipeline first move for whoever picks up the alert.
RUNBOOK: dict[str, str] = {
    GEN: "`fly logs -a ik-ad-generator` (daily chain run_daily.sh, 05:30 UTC)",
    ANOMALY: "`fly logs -a ik-anomaly` (daily run, 04:30 UTC)",
    MR_JOBS: ("`fly machine list -a ik-market-research-jobs` (monthly, 1st, 05:30 UTC; started by "
              "Cloudflare Worker ik-ad-generator-trigger, check its logs for \"jobs start\")"),
    MR_PIPELINE: ("`fly machine list -a ik-market-research-weekly` (monthly, 2nd, 05:30 UTC; started by "
                  "Cloudflare Worker ik-ad-generator-trigger, check its logs for \"weekly start\")"),
}


# ---------------------------------------------------------------------------
# Expiry calendar -- dated deadlines that otherwise fail silently. Keep each
# entry's date current: the action text ends with "then update this date".
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Expiry:
    name: str
    expires: date
    action: str


EXPIRIES: list[Expiry] = [
    # Probed 2026-10-27: refresh via refresh_token works, ~148 days left.
    Expiry("LinkedIn OAuth refresh token", date(2027, 3, 24),
           "Re-auth: run linkedin-ads/get_token.py locally, then set LINKEDIN_ENV_B64 "
           "on ik-anomaly and ik-ad-generator (flyctl secrets set ... --stage), "
           "then update this date"),
    # ESTIMATE, not a published date: on 2026-10-27 versions <=202512 returned
    # 426 and 202601..202610 returned 200, i.e. a version lives ~10-12 months.
    # 202610 is the M001 pin (anomaly-engine collect_data.py, MIS
    # generation/build_campaign_registry.py), so warn from ~2027-07.
    Expiry("LinkedIn-Version 202610 pin (estimated sunset)", date(2027, 8, 1),
           "Probe a newer LinkedIn-Version (GET /rest/adAccounts returns 200, not 426), "
           "bump the pin in anomaly-engine collect_data.py, linkedin-ads/* and "
           "generation/build_campaign_registry.py, then update this date"),
]


@dataclass(frozen=True)
class ExpiryStatus:
    name: str
    expires: date
    action: str
    days_left: int          # (expires - today).days; <= 0 means expired

    @property
    def expired(self) -> bool:
        return self.days_left <= 0

    @property
    def days_late(self) -> int:
        """Expiry day itself = day 1 (same convention as Breach.days_late)."""
        return 1 - self.days_left if self.expired else 0


def expiry_status(today: date, expiries: Iterable[Expiry] = EXPIRIES) -> list[ExpiryStatus]:
    """Pure. Every calendar entry with its days_left, soonest first."""
    out = [ExpiryStatus(e.name, e.expires, e.action, (e.expires - today).days)
           for e in expiries]
    out.sort(key=lambda s: (s.days_left, s.name))
    return out


def due_expiries(statuses: Iterable[ExpiryStatus],
                 warn_days: int = EXPIRY_WARN_DAYS) -> list[ExpiryStatus]:
    """Pure. Entries within `warn_days` of expiry, or already past it."""
    return [s for s in statuses if s.days_left <= warn_days]


# ---------------------------------------------------------------------------
# Pure evaluation
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Breach:
    key: str
    pipeline: str
    severity: str
    latest: date | None
    lag_days: int | None
    max_lag_days: int
    late_since: date | None   # first IST date the check would have failed
    days_late: int               # late_since = day 1; 0 for NO DATA (no anchor date)
    note: str = ""

    @property
    def no_data(self) -> bool:
        return self.latest is None


def today_ist(now_utc: datetime | None = None) -> date:
    """IST calendar date for a UTC instant (naive input is treated as UTC)."""
    if now_utc is None:
        now_utc = datetime.now(UTC)
    elif now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=UTC)
    return now_utc.astimezone(IST).date()


def _coerce_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        return date.fromisoformat(value.strip()[:10])
    return None


def _severity_rank(severity: str) -> int:
    return SEVERITIES.index(severity) if severity in SEVERITIES else len(SEVERITIES)


def _breach_sort_key(b: Breach):
    # alert before warn; within a severity NO DATA first (it means "cannot
    # even check"), then the longest-late first; key as a stable tiebreak.
    return (_severity_rank(b.severity), 0 if b.no_data else 1, -b.days_late, b.key)


def evaluate(latest: dict, today: date, specs: Iterable[TableSpec] = SPECS) -> list[Breach]:
    """Pure. One Breach per spec whose latest date is missing or older than
    its SLA. Keys in `latest` that are not in `specs` are ignored, so the
    breach key set is always a subset of the SPECS key set.

    A missing/None value is a NO DATA breach (table empty, missing or not
    readable): latest/lag/late_since None and days_late 0, because there is
    no anchor date to count from -- it posts daily but never pages by itself
    (an all-tables failure is an exit-1 infrastructure failure instead)."""
    breaches: list[Breach] = []
    for spec in specs:
        latest_d = _coerce_date(latest.get(spec.key))
        if latest_d is None:
            breaches.append(Breach(spec.key, spec.pipeline, spec.severity, None, None,
                                   spec.max_lag_days, None, 0, spec.note))
            continue
        lag = (today - latest_d).days
        if lag <= spec.max_lag_days:
            continue
        late_since = latest_d + timedelta(days=spec.max_lag_days + 1)
        breaches.append(Breach(spec.key, spec.pipeline, spec.severity, latest_d, lag,
                               spec.max_lag_days, late_since,
                               (today - late_since).days + 1, spec.note))
    breaches.sort(key=_breach_sort_key)
    return breaches


def is_escalation_day(days_late: int) -> bool:
    """Day 1, day 3, then every 7th day (7, 14, 21, ...)."""
    return days_late == 1 or days_late == 3 or (days_late > 0 and days_late % 7 == 0)


def should_mention(breaches: Iterable[Breach], expiries: Iterable[ExpiryStatus] = ()) -> bool:
    """Pure. Page only for alert-severity breaches on an escalation day, or an
    already-EXPIRED calendar entry on an escalation day (an upcoming expiry
    is warn severity: it posts daily but never pages)."""
    if any(b.severity == "alert" and is_escalation_day(b.days_late) for b in breaches):
        return True
    return any(e.expired and is_escalation_day(e.days_late) for e in expiries)


# ---------------------------------------------------------------------------
# Pure rendering (Slack mrkdwn, ASCII only, <= MAX_MESSAGE_CHARS)
# ---------------------------------------------------------------------------
def _esc(text: str) -> str:
    """Slack mrkdwn control characters in free text (names, actions, notes)."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _breach_line(b: Breach) -> str:
    if b.no_data:
        line = (f"- `{b.key}` NO DATA / unreadable (SLA {b.max_lag_days}d) -- "
                f"empty, missing or no read access")
    else:
        line = (f"- `{b.key}` latest {b.latest} ({b.lag_days}d old, SLA {b.max_lag_days}d)"
                f" -- LATE since {b.late_since} (day {b.days_late})")
    if b.note:
        line += f" [{_esc(b.note)}]"
    if b.severity != "alert":
        line += f" _({b.severity})_"
    return line


def _expiry_line(e: ExpiryStatus) -> str:
    when = (f"EXPIRED {e.expires} (day {e.days_late})" if e.expired
            else f"expires {e.expires} (in {e.days_left}d)")
    return f"- *{_esc(e.name)}* {when} -- {_esc(e.action)}"


def _fit(head: list[str], body: list[str], tail: list[str], limit: int) -> str:
    """Join head+body+tail; if over `limit`, drop body lines from the end
    (lowest priority, since breaches are sorted) and say how many were cut.
    The full list is always in the workflow log (main() logs every breach)."""
    text = "\n".join(head + body + tail)
    if len(text) <= limit:
        return text
    kept = list(body)
    dropped = 0
    while kept:
        kept.pop()
        dropped += 1
        marker = f"_... {dropped} more line(s) cut to fit Slack -- full list in the workflow log_"
        text = "\n".join(head + kept + [marker] + tail)
        if len(text) <= limit:
            return text
    return text[: limit - 3] + "..."   # pathological head/tail; still honor the cap


def render_message(breaches: Iterable[Breach], today: date, total_tables: int,
                   heartbeat: bool, *, expiries: Iterable[ExpiryStatus] = (),
                   mention: str = DEFAULT_MENTION, run_url: str | None = None,
                   warn_days: int = EXPIRY_WARN_DAYS,
                   limit: int = MAX_MESSAGE_CHARS) -> str | None:
    """Pure. The Slack text to post, or None when there is nothing to say.

    `expiries` is the full expiry_status() list: only entries within
    `warn_days` (or expired) are rendered, and they count as something to
    post exactly like a breach. With no breach and no due expiry the result
    is None unless `heartbeat`, in which case it is the all-green line."""
    breaches = list(breaches)
    statuses = list(expiries)
    due = due_expiries(statuses, warn_days)

    if not breaches and not due:
        if not heartbeat:
            return None
        text = (f":white_check_mark: Freshness watchdog -- all {total_tables} tables "
                f"within SLA ({today} IST)")
        if statuses:
            nxt = statuses[0]
            text += f"\nNext calendar deadline: {_esc(nxt.name)} on {nxt.expires} ({nxt.days_left}d)."
        return text[:limit]

    alert = any(b.severity == "alert" for b in breaches) or any(e.expired for e in due)
    icon = ":rotating_light:" if alert else ":warning:"
    parts = [f"{len(breaches)} table(s) LATE" if breaches
             else f"all {total_tables} tables within SLA"]
    n_expired = sum(1 for e in due if e.expired)
    if n_expired:
        parts.append(f"{n_expired} deadline(s) EXPIRED")
    if len(due) - n_expired:
        parts.append(f"{len(due) - n_expired} deadline(s) expiring within {warn_days}d")
    header = f"{icon} Freshness watchdog -- {', '.join(parts)} ({today} IST)"
    if mention and should_mention(breaches, due):
        header = f"{mention} {header}"

    body: list[str] = []
    pipelines: list[str] = []
    for b in breaches:                       # pipelines in order of their worst breach
        if b.pipeline not in pipelines:
            pipelines.append(b.pipeline)
    for p in pipelines:
        body.append("")
        body.append(f"*{p}* -- {RUNBOOK.get(p, 'check the job logs')}")
        body.extend(_breach_line(b) for b in breaches if b.pipeline == p)

    tail: list[str] = []
    if due:
        tail.append("")
        tail.append(":key: *Expiry calendar* (ops/freshness_watchdog.py EXPIRIES)")
        tail.extend(_expiry_line(e) for e in due)
    tail.append("")
    footer = "_Repeats daily while late; pages on day 1, 3 and every 7th day._"
    if run_url:
        footer += f" <{run_url}|workflow run>"
    tail.append(footer)

    return _fit([header], body, tail, limit)


# ---------------------------------------------------------------------------
# I/O: BigQuery (SELECT-only) and Slack
# ---------------------------------------------------------------------------
class WatchdogConfigError(RuntimeError):
    """A required env var is missing or malformed."""


class FreshnessQueryError(RuntimeError):
    """Every freshness query failed (auth, permissions, or BigQuery down)."""


class SlackPostError(RuntimeError):
    """chat.postMessage failed (HTTP error or ok:false)."""


def bq_project() -> str:
    project = os.environ.get("BQ_PROJECT", "").strip() or DEFAULT_PROJECT
    if not _PROJECT_RE.match(project):
        raise WatchdogConfigError("BQ_PROJECT must match ^[A-Za-z0-9-]+$")
    return project


def _secret_env(name: str) -> str:
    """Env secret with surrounding whitespace AND a leading UTF-8 BOM removed.
    Windows PowerShell 5.1 can prepend a BOM when a value is piped into
    `gh secret set` (2026-10-27: WATCHDOG_BQ_SA_JSON arrived as 'not valid
    JSON'); str.strip() alone leaves U+FEFF in place."""
    return os.environ.get(name, "").strip().lstrip("﻿").strip()


def build_client():
    """BigQuery client from WATCHDOG_BQ_SA_JSON (parsed in memory, never
    written to disk). Raises WatchdogConfigError / ImportError; never
    includes the key material in an error message."""
    raw = _secret_env("WATCHDOG_BQ_SA_JSON")
    if not raw:
        raise WatchdogConfigError("WATCHDOG_BQ_SA_JSON is not set")
    try:
        info = json.loads(raw)
    except ValueError:
        raise WatchdogConfigError(
            "WATCHDOG_BQ_SA_JSON is not valid JSON (paste the whole key file)") from None
    if not isinstance(info, dict) or info.get("type") != "service_account":
        raise WatchdogConfigError("WATCHDOG_BQ_SA_JSON is not a service-account key")
    from google.cloud import bigquery  # lazy: only the live path needs it
    from google.oauth2 import service_account
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=[CLOUD_PLATFORM_SCOPE])
    # No `location`: BigQuery infers it from the referenced tables, which
    # keeps the per-table fallback working even if datasets differ in region.
    return _BoundedClient(bigquery.Client(project=bq_project(), credentials=creds))


class _BoundedClient:
    """The only client fetch_latest() sees on the live path: every query gets
    the bounded retry/timeout settings above instead of the library defaults.
    Same .query(sql).result(timeout=) shape as bigquery.Client, so the tests'
    fakes need no change."""

    def __init__(self, client):
        from google.cloud.bigquery.retry import DEFAULT_RETRY  # lazy, like build_client
        self._client = client
        self._retry = DEFAULT_RETRY.with_timeout(BQ_RETRY_S)

    def query(self, sql):
        # job_retry=None: no 600s jobs.insert retry and no 2400s re-run of a
        # failed job. A read that fails today is retried by tomorrow's cron;
        # a hang would silence the alert.
        job = self._client.query(sql, retry=self._retry, timeout=BQ_REQUEST_TIMEOUT_S,
                                 job_retry=None)
        return _BoundedJob(job, self._retry)


class _BoundedJob:
    def __init__(self, job, retry):
        self._job, self._retry = job, retry

    def result(self, timeout=QUERY_TIMEOUT_S):
        # job_retry left at its default: result() resolves that to the
        # job_retry=None the query() call above stored on the job.
        return self._job.result(timeout=timeout, retry=self._retry)


def _validate_spec(spec: TableSpec) -> None:
    for ident in (spec.dataset, spec.table, spec.date_col):
        if not _IDENT_RE.match(ident):
            raise ValueError(f"unsafe identifier in SPECS: {ident!r}")


def _select_sql(spec: TableSpec, project: str) -> str:
    _validate_spec(spec)
    return (f"SELECT '{spec.key}' AS k, CAST(MAX(`{spec.date_col}`) AS DATE) AS d "
            f"FROM `{project}.{spec.dataset}.{spec.table}`")


def build_union_sql(specs: Iterable[TableSpec], project: str = DEFAULT_PROJECT) -> str:
    """Identifiers come only from the hardcoded SPECS, each checked against
    ^[A-Za-z0-9_]+$ (project against ^[A-Za-z0-9-]+$) -- never user input."""
    if not _PROJECT_RE.match(project):
        raise ValueError(f"unsafe project id: {project!r}")
    return "\nUNION ALL\n".join(_select_sql(s, project) for s in specs)


_SECRET_ENVS = ("SLACK_BOT_TOKEN", "WATCHDOG_BQ_SA_JSON")


def _redact(text: str) -> str:
    """Belt and braces: no code path here formats a secret into a message,
    but a third-party exception string could echo one back."""
    for name in _SECRET_ENVS:
        value = _secret_env(name)
        if len(value) >= 8 and value in text:
            text = text.replace(value, "[REDACTED]")
    return text


def _err(exc: BaseException) -> str:
    """One-line, length-capped, redacted description of an exception."""
    lines = str(exc).splitlines()
    msg = _redact(lines[0][:200]) if lines else ""
    return f"{type(exc).__name__}: {msg}" if msg else type(exc).__name__


def _is_systemic(exc: BaseException) -> bool:
    """True for failures of the BigQuery service rather than of one table:
    a timeout, retries exhausted (google.api_core RetryError), HTTP 5xx/429,
    or a backendError/internalError/rateLimitExceeded reason. Matched by
    name/attribute so the pure path never imports google.api_core."""
    if isinstance(exc, TimeoutError):             # incl. concurrent.futures.TimeoutError (3.11+)
        return True
    if any(cls.__name__ == "RetryError" for cls in type(exc).__mro__):
        return True
    code = getattr(exc, "code", None)
    if isinstance(code, int) and (code >= 500 or code == 429):
        return True
    errors = getattr(exc, "errors", None)
    reasons = {e.get("reason") for e in errors if isinstance(e, dict)} if isinstance(errors, list) else set()
    return bool(reasons & _SYSTEMIC_REASONS)


def fetch_latest(client, specs: Iterable[TableSpec] = SPECS,
                 project: str | None = None, *, budget_s: float = FETCH_BUDGET_S,
                 clock=None) -> dict:
    """{key: date|None} for every spec. One UNION ALL query; on failure, one
    query per table with None for each table that errors. Raises
    FreshnessQueryError when EVERY per-table query fails, when a failure is
    service-wide (_is_systemic: no per-table fallback, it would only repeat
    the outage 22 times), or when the read uses up `budget_s` seconds."""
    specs = list(specs)
    project = project or bq_project()
    clock = clock or time.monotonic
    deadline = clock() + budget_s
    union_sql = build_union_sql(specs, project)   # unsafe identifier -> ValueError, loudly
    try:
        rows = client.query(union_sql).result(timeout=QUERY_TIMEOUT_S)
        out = {s.key: None for s in specs}
        for row in rows:
            if row["k"] in out:
                out[row["k"]] = _coerce_date(row["d"])
        return out
    except Exception as exc:                      # noqa: BLE001 -- any BQ failure -> fallback
        if _is_systemic(exc):
            raise FreshnessQueryError(
                f"BigQuery unavailable ({_err(exc)}) -- per-table fallback skipped") from None
        log.warning("UNION ALL freshness query failed (%s) -- falling back to one "
                    "query per table", _err(exc))

    out: dict = {}
    failures = 0
    for i, spec in enumerate(specs):
        if clock() >= deadline:
            raise FreshnessQueryError(
                f"freshness read used up its {budget_s:.0f}s budget after {i}/{len(specs)} "
                "per-table queries")
        try:
            rows = list(client.query(_select_sql(spec, project)).result(timeout=QUERY_TIMEOUT_S))
            out[spec.key] = _coerce_date(rows[0]["d"]) if rows else None
        except Exception as exc:                  # noqa: BLE001
            if _is_systemic(exc):
                raise FreshnessQueryError(
                    f"BigQuery unavailable at {spec.key} ({_err(exc)}) -- "
                    "remaining per-table queries skipped") from None
            failures += 1
            out[spec.key] = None
            log.warning("freshness query failed for %s (%s) -- reporting NO DATA",
                        spec.key, _err(exc))
    if specs and failures == len(specs):
        raise FreshnessQueryError(f"all {len(specs)} per-table freshness queries failed")
    return out


_SLACK_HINTS = {
    "not_in_channel": "invite the bot to the channel",
    "channel_not_found": "SLACK_CHANNEL_ID must be the channel id (C...), not the name",
    "invalid_auth": "SLACK_BOT_TOKEN is wrong or revoked (should start with xoxb-)",
    "missing_scope": "the bot needs chat:write",
}


def _retry_after_s(headers) -> int:
    try:
        value = int((headers or {}).get("Retry-After", "5"))
    except (TypeError, ValueError):
        value = 5
    return max(1, min(value, 60))


def post_slack(token: str, channel: str, text: str, *, opener=None, sleep=time.sleep) -> dict:
    """chat.postMessage via urllib, mirroring slack-tools/slack_post.py:50-119
    without its sys.exit or dotenv dependency: one retry on 429 Retry-After,
    SlackPostError on any HTTP error or ok:false. The token is only ever put
    in the Authorization header, never in an error message."""
    opener = opener or urllib.request.urlopen
    body = json.dumps({"channel": channel, "text": text,
                       "unfurl_links": False, "unfurl_media": False}).encode("utf-8")
    for attempt in (1, 2):
        req = urllib.request.Request(
            SLACK_POST_URL, data=body, method="POST",
            headers={"Content-Type": "application/json; charset=utf-8",
                     "Authorization": f"Bearer {token}"})
        try:
            with opener(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt == 1:
                wait = _retry_after_s(exc.headers)
                log.warning("Slack rate limited -- retrying once in %ss", wait)
                sleep(wait)
                continue
            raise SlackPostError(f"Slack HTTP {exc.code}") from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise SlackPostError(f"Slack request failed ({type(exc).__name__})") from None
        if not payload.get("ok"):
            error = str(payload.get("error", "unknown"))
            hint = _SLACK_HINTS.get(error)
            raise SlackPostError(f"Slack API error: {error}" + (f" -- {hint}" if hint else ""))
        return payload
    raise SlackPostError("Slack rate limited twice")  # pragma: no cover (loop always returns/raises)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _env_true(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def resolve_mention() -> str:
    """WATCHDOG_MENTION unset -> <!here>. Empty disables. "none"/"off" also
    disable, because a GitHub repo variable cannot hold an empty string."""
    value = os.environ.get("WATCHDOG_MENTION")
    if value is None:
        return DEFAULT_MENTION
    value = value.strip()
    return "" if value.lower() in ("", "none", "off") else value


def _run_url() -> str | None:
    server = os.environ.get("GITHUB_SERVER_URL")
    repo = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if server and repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return None


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Freshness watchdog: Slack alert when any pipeline table is late "
                    "(SELECT-only; see ops/README.md).")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the message instead of posting (also env WATCHDOG_DRY_RUN=true). "
                        "BigQuery is still read.")
    p.add_argument("--heartbeat", choices=("auto", "always", "never"), default="auto",
                   help="All-green post when nothing is late: auto = Mondays IST only (default).")
    p.add_argument("--today", type=str, default=None,
                   help="IST date override YYYY-MM-DD (tests / replays). Default: today IST.")
    p.add_argument("--fail-on-breach", action="store_true",
                   help="Exit 2 if any alert-severity table is late (after posting).")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.today:
        try:
            today = date.fromisoformat(args.today)
        except ValueError:
            log.error("--today must be YYYY-MM-DD (got %r)", args.today)
            return 1
    else:
        today = today_ist()
    dry_run = args.dry_run or _env_true("WATCHDOG_DRY_RUN")

    token = _secret_env("SLACK_BOT_TOKEN")
    channel = _secret_env("SLACK_CHANNEL_ID")
    if not dry_run and (not token or not channel):
        missing = [n for n, v in (("SLACK_BOT_TOKEN", token), ("SLACK_CHANNEL_ID", channel))
                   if not v]
        log.error("missing env %s -- cannot post (use --dry-run to only print)",
                  ", ".join(missing))
        return 1

    try:
        latest = fetch_latest(build_client(), SPECS)
    except Exception as exc:                      # noqa: BLE001 -- infra failure -> exit 1
        log.error("watchdog infrastructure failure reading BigQuery: %s", _err(exc))
        return 1

    breaches = evaluate(latest, today, SPECS)
    statuses = expiry_status(today)
    late_keys = {b.key for b in breaches}
    for spec in SPECS:
        d = _coerce_date(latest.get(spec.key))
        lag = f"{(today - d).days}d" if d else "n/a"
        log.info("%-5s %-45s latest=%s lag=%s sla=%sd", "LATE" if spec.key in late_keys else "ok",
                 spec.key, d, lag, spec.max_lag_days)
    for b in breaches:
        log.info("BREACH %s", _breach_line(b))
    for e in due_expiries(statuses):
        log.info("EXPIRY %s", _expiry_line(e))

    heartbeat = args.heartbeat == "always" or (args.heartbeat == "auto" and today.weekday() == 0)
    message = render_message(breaches, today, len(SPECS), heartbeat, expiries=statuses,
                             mention=resolve_mention(), run_url=_run_url())

    if message is None:
        log.info("all %d tables within SLA on %s IST -- nothing to post (heartbeat=%s)",
                 len(SPECS), today, args.heartbeat)
    elif dry_run:
        print("[DRY RUN] Would post to Slack:")
        print(message)
    else:
        try:
            post_slack(token, channel, message)
        except Exception as exc:                  # noqa: BLE001
            log.error("Slack post failed: %s", _err(exc))
            return 1
        log.info("posted to Slack (%d breach(es), %d chars)", len(breaches), len(message))

    if args.fail_on_breach and any(b.severity == "alert" for b in breaches):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
