#!/usr/bin/env python3
"""
Tests for the freshness watchdog -- M001 T05 (ops/freshness_watchdog.py).

Run: `python ops/tests_freshness_watchdog.py` (from the MIS repo root, or
anywhere -- imports resolve relative to this file's directory). Needs only
the stdlib: no google-cloud-bigquery, no credentials, no network.

Same harness style as generation/tests_notify.py: a plain `check(cond, msg)`
printing PASS/FAIL and exiting 1 on the first failure -- no pytest. Every
BigQuery client and Slack opener here is an in-memory fake; main() runs with
build_client/fetch_latest/post_slack monkeypatched, so nothing can reach a
live service.
"""
from __future__ import annotations

import importlib
import io
import json
import logging
import os
import re
import sys
import urllib.error
from contextlib import redirect_stdout
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# importlib (not a top-level import) so the sys.path insert above needs no
# E402 suppression under any ruff ruleset.
fw = importlib.import_module("freshness_watchdog")

REPO = HERE.parent
WORKFLOW = REPO / ".github" / "workflows" / "freshness-watchdog.yml"
CI_WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"
D = date.fromisoformat
TODAY = D("2026-10-27")            # a Tuesday
MONDAY = D("2026-10-26")

# Synthetic expiry calendars. No value assertion reads a REAL EXPIRIES date:
# renewing a deadline is a one-line date edit (the entry's action text and
# ops/README.md say so) and must never turn this suite red. The real list
# gets structure-only and date-agnostic checks (see "expiry calendar").
CAL = [fw.Expiry("Tok", D("2027-03-24"), "do X")]                  # 148d after TODAY
CAL2 = [fw.Expiry("Later", D("2027-08-01"), "do Y"), *CAL]        # unsorted on purpose


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"PASS: {msg}")


def spec(key):
    return next(s for s in fw.SPECS if s.key == key)


def at_lag(lags: dict, today=TODAY):
    """{key: lag_days} -> {key: date} for evaluate()."""
    return {k: today - timedelta(days=v) for k, v in lags.items()}


def all_at(offset, today=TODAY):
    """Every SPECS table at max_lag + offset."""
    return {s.key: today - timedelta(days=s.max_lag_days + offset) for s in fw.SPECS}


# The REAL snapshot, read-only probe 2026-10-27 ~08:19 UTC (research-watchdog.md).
SNAPSHOT_2026_10_27 = {
    "SIP_pattern_rag.ce_generation_runs": "2026-10-27",
    "SIP_pattern_rag.ce_keyword_candidates": "2026-10-27",
    "SIP_pattern_rag.ce_ad_copy_drafts": "2026-10-27",
    "SIP_pattern_rag.ce_audience_angles": "2026-10-27",
    "SIP_pattern_rag.ce_lead_quality_scores": "2026-10-27",
    "SIP_pattern_rag.ce_kw_search_term_perf": "2026-10-26",
    "SIP_pattern_rag.ce_value_ledger": "2026-10-25",
    "SIP_pattern_rag.ce_keyword_rejects": "2026-10-26",
    "SIP_pattern_rag.ce_campaign_registry": "2026-07-04",
    "SIP_pattern_rag.pmis_run_metadata": "2026-10-27",
    "SIP_pattern_rag.pmis_full_funnel": "2026-10-27",
    "SIP_pattern_rag.pmis_daily_segment_facts": "2026-10-15",
    "SIP_pattern_rag.pmis_anomaly_facts": "2026-10-26",
    "SIP_pattern_rag.pmis_daily_summary": "2026-10-27",
    "SIP_pattern_rag.pmis_platform_summary": "2026-10-27",
    "SIP_pattern_rag.pmis_program_summary": "2026-10-27",
    "SIP_pattern_rag.pmis_recommendations": "2026-10-26",
    "Market_Research.job_market_demand": "2026-08-18",
    "Market_Research.social_trends": "2026-08-25",
    "Market_Research.blog_publications": "2026-08-25",
    "Market_Research.competitor_intel": "2026-08-25",
    "Market_Research.executive_summary": "2026-08-25",
}
EXPECTED_REAL_BREACHES = {
    "SIP_pattern_rag.pmis_daily_segment_facts",
    "SIP_pattern_rag.ce_campaign_registry",
    "Market_Research.job_market_demand",
    "Market_Research.social_trends",
    "Market_Research.blog_publications",
    "Market_Research.competitor_intel",
    "Market_Research.executive_summary",
}


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeJob:
    def __init__(self, rows=None, exc=None):
        self._rows, self._exc = rows, exc

    def result(self, timeout=None):
        if self._exc:
            raise self._exc
        return list(self._rows)


class FakeClient:
    """`handler(sql) -> rows | Exception`; records every SQL it was sent."""

    def __init__(self, handler):
        self.handler = handler
        self.sqls = []

    def query(self, sql):
        self.sqls.append(sql)
        out = self.handler(sql)
        return FakeJob(exc=out) if isinstance(out, Exception) else FakeJob(rows=out)


def _key_of(sql):
    return re.search(r"SELECT '([^']+)' AS k", sql).group(1)


class FakeResp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    """Replays a list of outcomes: a dict payload or an HTTPError."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return FakeResp(out)


def http_error(code, headers=None):
    return urllib.error.HTTPError(fw.SLACK_POST_URL, code, "err", headers or {}, None)


# ---------------------------------------------------------------------------
# SPECS integrity
# ---------------------------------------------------------------------------
keys = [s.key for s in fw.SPECS]
check(len(keys) == len(set(keys)), f"SPECS keys are unique ({len(keys)} tables)")
check(all(fw._IDENT_RE.match(x) for s in fw.SPECS for x in (s.dataset, s.table, s.date_col)),
      "every SPECS identifier matches ^[A-Za-z0-9_]+$")
check(all(s.severity in fw.SEVERITIES for s in fw.SPECS), "every severity is alert|warn")
check(all(s.max_lag_days >= 1 for s in fw.SPECS), "every max_lag_days >= 1")
check(all(s.pipeline in fw.RUNBOOK for s in fw.SPECS), "every pipeline has a runbook hint")
check(set(SNAPSHOT_2026_10_27) == set(keys), "the replayed snapshot covers exactly the SPECS keys")
check(spec("SIP_pattern_rag.ce_campaign_registry").max_lag_days == 8
      and spec("SIP_pattern_rag.ce_campaign_registry").severity == "alert",
      "registry SLA = 8d alert (weekly Monday refresh + >7d catch-up)")
mr = [s for s in fw.SPECS if s.dataset == "Market_Research"]
check(len(mr) == 5 and all(s.max_lag_days == 35 and s.severity == "alert" for s in mr),
      "all 5 Market_Research tables SLA = 35d alert (monthly)")
for k, lag in [("ce_generation_runs", 1), ("ce_kw_search_term_perf", 2), ("ce_value_ledger", 2),
               ("pmis_full_funnel", 1), ("pmis_run_metadata", 1),
               ("pmis_daily_segment_facts", 2), ("pmis_anomaly_facts", 2)]:
    s = spec(f"SIP_pattern_rag.{k}")
    check(s.max_lag_days == lag and s.severity == "alert", f"{k} SLA = {lag}d alert")
check(spec("SIP_pattern_rag.ce_keyword_rejects").severity == "warn"
      and spec("SIP_pattern_rag.ce_keyword_rejects").max_lag_days == 5,
      "ce_keyword_rejects is sparse: SLA 5d warn")
check("google.cloud.bigquery" not in sys.modules and "google.oauth2" not in sys.modules,
      "importing the module does not import google-cloud-bigquery (lazy)")

# ---------------------------------------------------------------------------
# (a) daily lag-0 table, max 1
# ---------------------------------------------------------------------------
K0 = "SIP_pattern_rag.ce_generation_runs"
only0 = [spec(K0)]
check(fw.evaluate(at_lag({K0: 0}), TODAY, only0) == [], "(a) lag-0 table at lag 0: OK")
check(fw.evaluate(at_lag({K0: 1}), TODAY, only0) == [], "(a) lag-0 table at lag 1 (one miss): OK")
b = fw.evaluate(at_lag({K0: 2}), TODAY, only0)
check(len(b) == 1 and b[0].lag_days == 2, "(a) lag-0 table at lag 2 (two misses): breach")
check(b[0].late_since == TODAY - timedelta(days=2) + timedelta(days=2) and b[0].days_late == 1,
      "(a) late_since = latest + 2 and days_late = 1")
check(b[0].latest == D("2026-10-25") and b[0].late_since == TODAY, "(a) late_since is today on day 1")

# ---------------------------------------------------------------------------
# (b) lag-1 table, max 2
# ---------------------------------------------------------------------------
K1 = "SIP_pattern_rag.pmis_daily_segment_facts"
only1 = [spec(K1)]
check(fw.evaluate(at_lag({K1: 2}), TODAY, only1) == [], "(b) lag-1 table at lag 2: OK")
b = fw.evaluate(at_lag({K1: 3}), TODAY, only1)
check(len(b) == 1 and b[0].days_late == 1 and b[0].late_since == TODAY,
      "(b) lag-1 table at lag 3: breach, day 1")

# ---------------------------------------------------------------------------
# (c) NO DATA
# ---------------------------------------------------------------------------
b = fw.evaluate({K0: None}, TODAY, only0)
check(len(b) == 1 and b[0].no_data and b[0].latest is None and b[0].lag_days is None
      and b[0].late_since is None and b[0].days_late == 0, "(c) latest=None is a NO DATA breach")
b = fw.evaluate({}, TODAY, only0)
check(len(b) == 1 and b[0].no_data, "(c) key missing from latest is a NO DATA breach")
check("NO DATA / unreadable" in fw._breach_line(b[0]), "(c) NO DATA line is labelled")
check(fw.evaluate({K0: "2026-10-27"}, TODAY, only0) == [], "(c) ISO string dates are accepted")
check(fw.evaluate({K0: datetime(2026, 10, 27, 4, 33)}, TODAY, only0) == [],
      "(c) datetime values are coerced to dates")

# ---------------------------------------------------------------------------
# (d) Market Research monthly: 35 OK, 36 breach
# ---------------------------------------------------------------------------
KM = "Market_Research.job_market_demand"
check(fw.evaluate(at_lag({KM: 35}), TODAY, [spec(KM)]) == [], "(d) Market Research at lag 35: OK")
b = fw.evaluate(at_lag({KM: 36}), TODAY, [spec(KM)])
check(len(b) == 1 and b[0].days_late == 1, "(d) Market Research at lag 36: breach, day 1")
check(fw.evaluate(at_lag({KM: 31}), TODAY, [spec(KM)]) == [],
      "(d) one monthly run a day late in a 31-day month (lag 31): OK")

# ---------------------------------------------------------------------------
# (e) replay the real 2026-10-27 snapshot
# ---------------------------------------------------------------------------
real = fw.evaluate(SNAPSHOT_2026_10_27, TODAY)
check({x.key for x in real} == EXPECTED_REAL_BREACHES and len(real) == 7,
      "(e) real 2026-10-27 snapshot breaches exactly the 7 expected tables")
seg = next(x for x in real if x.key == K1)
check(fw._breach_line(seg) == "- `SIP_pattern_rag.pmis_daily_segment_facts` latest 2026-10-15 "
      "(12d old, SLA 2d) -- LATE since 2026-10-18 (day 10)",
      "(e) segment_facts line: 12d old, LATE since 2026-10-18 (day 10)")
jm = next(x for x in real if x.key == KM)
check(jm.lag_days == 70 and jm.days_late == 35, "(e) job_market_demand: 70d old, day 35")
reg = next(x for x in real if x.key == "SIP_pattern_rag.ce_campaign_registry")
check(reg.lag_days == 115 and reg.late_since == D("2026-07-13") and reg.days_late == 107,
      "(e) registry: 115d old, LATE since 2026-07-13 (day 107)")
check(real[0].key == "SIP_pattern_rag.ce_campaign_registry",
      "(e) breaches sorted longest-late first within alert")
real_msg = fw.render_message(real, TODAY, len(fw.SPECS), False,
                             expiries=fw.expiry_status(TODAY, CAL), mention="<!here>")
check(real_msg.startswith("<!here> :rotating_light: Freshness watchdog -- 7 table(s) LATE "
                          "(2026-10-27 IST)"),
      "(e) header pages: Market Research is on day 35/28 (multiples of 7)")
check(all(f"`{k}`" in real_msg for k in EXPECTED_REAL_BREACHES), "(e) message names every late table")
check("`fly logs -a ik-anomaly`" in real_msg and "`fly logs -a ik-ad-generator`" in real_msg
      and "ik-market-research-jobs" in real_msg and "ik-market-research-weekly" in real_msg,
      "(e) per-pipeline runbook hints present")
check("Expiry calendar" not in real_msg, "(e) no expiry section on 2026-10-27 (148d left)")
check(real_msg.isascii() and len(real_msg) <= fw.MAX_MESSAGE_CHARS, "(e) ASCII and within the cap")

# ---------------------------------------------------------------------------
# (f) no-false-alarm guards
# ---------------------------------------------------------------------------
healthy_1030 = dict(SNAPSHOT_2026_10_27)
healthy_1030.update({  # 10:30 UTC on a day the generation chain is still running
    "SIP_pattern_rag.ce_generation_runs": "2026-10-26",
    "SIP_pattern_rag.ce_keyword_candidates": "2026-10-26",
    "SIP_pattern_rag.ce_ad_copy_drafts": "2026-10-26",
    "SIP_pattern_rag.ce_audience_angles": "2026-10-26",
    "SIP_pattern_rag.ce_lead_quality_scores": "2026-10-26",
    "SIP_pattern_rag.ce_kw_search_term_perf": "2026-10-25",
    "SIP_pattern_rag.ce_value_ledger": "2026-10-25",
    "SIP_pattern_rag.ce_keyword_rejects": "2026-10-24",
    "SIP_pattern_rag.ce_campaign_registry": "2026-10-19",   # last Monday's run failed soft
    "SIP_pattern_rag.pmis_daily_segment_facts": "2026-10-26",
    "Market_Research.job_market_demand": "2026-10-01",
    "Market_Research.social_trends": "2026-10-02",
    "Market_Research.blog_publications": "2026-10-02",
    "Market_Research.competitor_intel": "2026-10-02",
    "Market_Research.executive_summary": "2026-10-02",
})
check(fw.evaluate(healthy_1030, TODAY) == [],
      "(f) healthy snapshot at 10:30 UTC (generation not finished): zero breaches")
check(fw.evaluate(all_at(0), TODAY) == [], "(f) every table exactly at its SLA: zero breaches")
over = fw.evaluate(all_at(1), TODAY)
check(len(over) == len(fw.SPECS) and all(x.days_late == 1 for x in over),
      "(f) every table one day past its SLA: every table breaches on day 1")
check(fw.evaluate({**all_at(0), "Nope.not_a_spec": D("2020-01-01")}, TODAY) == [],
      "(k) a key that is not in SPECS can never breach")
check({x.key for x in fw.evaluate({}, TODAY)} == set(keys),
      "(k) empty latest -> breach key set equals the SPECS key set")
check(fw.evaluate({k: TODAY + timedelta(days=3) for k in keys}, TODAY) == [],
      "future dates (clock skew) never breach")

# ---------------------------------------------------------------------------
# sort order
# ---------------------------------------------------------------------------
mixed = fw.evaluate({**all_at(0),
                     "SIP_pattern_rag.ce_keyword_rejects": TODAY - timedelta(days=60),  # warn, day 55
                     K0: TODAY - timedelta(days=3),                                     # alert, day 2
                     "SIP_pattern_rag.pmis_full_funnel": None,                         # alert, NO DATA
                     KM: TODAY - timedelta(days=45)}, TODAY)                            # alert, day 10
check([x.key for x in mixed] == ["SIP_pattern_rag.pmis_full_funnel", KM, K0,
                                 "SIP_pattern_rag.ce_keyword_rejects"],
      "sort: alert before warn; NO DATA first, then days_late descending")

# ---------------------------------------------------------------------------
# (g) escalation / mention
# ---------------------------------------------------------------------------
paged = []
for n in range(1, 31):
    br = fw.evaluate(at_lag({K0: 1 + n}), TODAY, only0)
    assert br[0].days_late == n
    msg = fw.render_message(br, TODAY, 1, False, mention="<!here>")
    if "<!here>" in msg:
        paged.append(n)
        check(msg.startswith("<!here> "), f"(g) mention is a prefix on day {n}")
check(paged == [1, 3, 7, 14, 21, 28], f"(g) <!here> only on days 1/3/7/14/21/28 (got {paged})")
warn_d1 = fw.evaluate(at_lag({"SIP_pattern_rag.ce_keyword_rejects": 6}), TODAY,
                      [spec("SIP_pattern_rag.ce_keyword_rejects")])
check(warn_d1[0].days_late == 1 and "<!here>" not in fw.render_message(warn_d1, TODAY, 1, False),
      "(g) a warn-severity breach on day 1 never pages")
check(fw.render_message(warn_d1, TODAY, 1, False).startswith(":warning:"),
      "(g) warn-only message uses :warning:, not :rotating_light:")
d1 = fw.evaluate(at_lag({K0: 2}), TODAY, only0)
check("<!here>" not in fw.render_message(d1, TODAY, 1, False, mention=""),
      "(g) mention='' disables paging")
check(fw.render_message(d1, TODAY, 1, False, mention="<!subteam^S123>").startswith("<!subteam^S123> "),
      "(g) a custom mention is used verbatim")
nodata = fw.evaluate({}, TODAY, only0)
check("<!here>" not in fw.render_message(nodata, TODAY, 1, False), "(g) NO DATA alone never pages")
check([n for n in range(30) if fw.is_escalation_day(n)] == [1, 3, 7, 14, 21, 28],
      "(g) is_escalation_day: 1, 3 and multiples of 7 (never 0)")

# ---------------------------------------------------------------------------
# (h) heartbeat / nothing to post
# ---------------------------------------------------------------------------
check(fw.render_message([], TODAY, 21, False) is None, "(h) no breaches, no heartbeat -> None")
hb = fw.render_message([], TODAY, 21, True)
check(hb == ":white_check_mark: Freshness watchdog -- all 21 tables within SLA (2026-10-27 IST)",
      "(h) heartbeat text exact")
hb2 = fw.render_message([], TODAY, 21, True, expiries=fw.expiry_status(TODAY, CAL2))
check(hb2.startswith(":white_check_mark:") and "Next calendar deadline: Tok on 2027-03-24 (148d)." in hb2
      and "Later" not in hb2 and "<!here>" not in hb2,
      "(h) heartbeat names the soonest calendar deadline and never pages")

# ---------------------------------------------------------------------------
# expiry calendar
# ---------------------------------------------------------------------------
# The REAL calendar: structure and date-agnostic checks only, so renewing an
# entry (editing its date) or adding one keeps this suite green.
check(fw.EXPIRIES and all(type(e.expires) is date and e.name.strip() and "update this date" in e.action
                          for e in fw.EXPIRIES),
      "expiry: every real entry has a name, a plain date (not a datetime) and an action "
      "ending in 'update this date'")
check(len({e.name for e in fw.EXPIRIES}) == len(fw.EXPIRIES), "expiry: real entry names are unique")
li = next(e for e in fw.EXPIRIES if e.name == "LinkedIn OAuth refresh token")
check("get_token.py" in li.action and "LINKEDIN_ENV_B64" in li.action
      and "ik-anomaly" in li.action and "ik-ad-generator" in li.action and "--stage" in li.action,
      "expiry: LinkedIn refresh token entry with the re-auth action")
nxt = min(fw.EXPIRIES, key=lambda e: (e.expires, e.name))      # expiry_status() order
quiet = nxt.expires - timedelta(days=100)                       # every real entry >= 100d out
hb_real = fw.render_message([], quiet, 21, True, expiries=fw.expiry_status(quiet))
check(f"Next calendar deadline: {fw._esc(nxt.name)} on {nxt.expires} (100d)." in hb_real,
      "expiry: the real calendar renders; the heartbeat names its soonest entry")
st = fw.expiry_status(TODAY)
check([s.days_left for s in st] == sorted(s.days_left for s in st), "expiry: real statuses soonest first")
st2 = fw.expiry_status(TODAY, CAL2)
check([s.name for s in st2] == ["Tok", "Later"] and st2[0].days_left == 148,
      "expiry: statuses soonest first; 148 days left on 2026-10-27")
check(fw.due_expiries(st2) == [], "expiry: nothing due 148d out")
cal = CAL
for day, due_n, expired, late in [("2027-02-21", 0, False, 0), ("2027-02-22", 1, False, 0),
                                  ("2027-03-23", 1, False, 0), ("2027-03-24", 1, True, 1),
                                  ("2027-03-26", 1, True, 3)]:
    s = fw.due_expiries(fw.expiry_status(D(day), cal))
    check(len(s) == due_n and (not s or (s[0].expired == expired and s[0].days_late == late)),
          f"expiry: {day} -> due={due_n} expired={expired} day={late}")
soon_day = D("2027-03-04")                              # 20 days left, nothing late
soon = fw.render_message([], soon_day, 21, False, expiries=fw.expiry_status(soon_day, cal))
check(soon is not None and soon.startswith(":warning: Freshness watchdog -- all 21 tables within SLA, "
                                           "1 deadline(s) expiring within 30d"),
      "expiry: within 30d posts even with zero breaches (warn header)")
check("*Tok* expires 2027-03-24 (in 20d) -- do X" in soon and "<!here>" not in soon,
      "expiry: upcoming entry rendered, never pages")
for day, pages in [("2027-03-24", True), ("2027-03-25", False), ("2027-03-26", True),
                   ("2027-03-30", True), ("2027-03-31", False)]:
    m = fw.render_message([], D(day), 21, False, expiries=fw.expiry_status(D(day), cal))
    check(("<!here>" in m) == pages and ":rotating_light:" in m and "EXPIRED 2027-03-24" in m,
          f"expiry: expired on {day} -> pages={pages}")
both = fw.render_message(d1, soon_day, 21, False, expiries=fw.expiry_status(soon_day, cal))
check("1 table(s) LATE, 1 deadline(s) expiring within 30d" in both, "expiry + breach share one header")
check(fw.render_message([], D("2027-03-25"), 21, False, expiries=fw.expiry_status(D("2027-03-25"), cal))
      .startswith(":rotating_light: Freshness watchdog -- all 21 tables within SLA, 1 deadline(s) EXPIRED"),
      "expiry: an expired entry alone gives a :rotating_light: EXPIRED header")
esc = fw.render_message([], soon_day, 1, False,
                        expiries=fw.expiry_status(soon_day, [fw.Expiry("A<b>&c", D("2027-03-10"), "x")]))
check("A&lt;b&gt;&amp;c" in esc, "free text is escaped for Slack mrkdwn")

# ---------------------------------------------------------------------------
# (i) size cap
# ---------------------------------------------------------------------------
# Every real entry's text (it sets the size) on synthetic dates, alternately
# due in 5d and EXPIRED 3d ago -- a renewal can never make this nothing-due.
cal_due = fw.expiry_status(TODAY, [fw.Expiry(e.name, TODAY + timedelta(days=-3 if i % 2 else 5), e.action)
                                   for i, e in enumerate(fw.EXPIRIES)])
for label, latest_map in [("all SPECS 100 days late", all_at(100)), ("all SPECS NO DATA", {})]:
    m = fw.render_message(fw.evaluate(latest_map, TODAY), TODAY, len(fw.SPECS), False,
                          expiries=cal_due, mention="<!here>",
                          run_url="https://github.com/o/r/actions/runs/123")
    check(len(m) <= fw.MAX_MESSAGE_CHARS and m.isascii(), f"(i) {label} + expiries: {len(m)} chars <= 3900")
# Soft guard: today's SPECS, all late, no expiry due, should fit untruncated.
# If adding tables trips this, truncation still keeps the post valid -- shorten
# lines or relax this check deliberately.
m = fw.render_message(fw.evaluate(all_at(100), TODAY), TODAY, len(fw.SPECS), False,
                      mention="<!here>", run_url="https://github.com/o/r/actions/runs/123")
check("cut to fit" not in m, f"(i) all {len(fw.SPECS)} SPECS late fit without truncation ({len(m)} chars)")
many = [fw.TableSpec("SIP_pattern_rag", f"t_{i:03d}_some_long_table_name", "date", 1, fw.GEN)
        for i in range(300)]
big = fw.render_message(fw.evaluate({}, TODAY, many), TODAY, 300, False, expiries=cal_due,
                        run_url="https://github.com/o/r/actions/runs/123")
check(len(big) <= fw.MAX_MESSAGE_CHARS, f"(i) 300 synthetic breaches: {len(big)} chars <= 3900")
check("cut to fit Slack" in big and "Expiry calendar" in big and "workflow run>" in big,
      "(i) truncation drops breach lines but keeps the expiry section and footer")

# ---------------------------------------------------------------------------
# (j) IST clock
# ---------------------------------------------------------------------------
check(fw.today_ist(datetime(2026, 10, 27, 19, 0, tzinfo=UTC)) == D("2026-10-28"),
      "(j) 2026-10-27T19:00Z is 2026-10-28 IST")
check(fw.today_ist(datetime(2026, 10, 27, 10, 30, tzinfo=UTC)) == TODAY,
      "(j) 2026-10-27T10:30Z is 2026-10-27 IST")
check(fw.today_ist(datetime(2026, 10, 27, 18, 29)) == TODAY
      and fw.today_ist(datetime(2026, 10, 27, 18, 30)) == D("2026-10-28"),
      "(j) naive input is UTC; the IST date flips at 18:30 UTC")

# ---------------------------------------------------------------------------
# SQL + fetch_latest (fake client)
# ---------------------------------------------------------------------------
sql = fw.build_union_sql(fw.SPECS, "ik-marketing-data")
check(sql.count("UNION ALL") == len(fw.SPECS) - 1 and all(f"'{k}' AS k" in sql for k in keys),
      "union SQL: one SELECT per spec")
check("FROM `ik-marketing-data.SIP_pattern_rag.pmis_daily_segment_facts`" in sql
      and "CAST(MAX(`snapshot_date`) AS DATE) AS d" in sql, "union SQL: fully-qualified, CAST to DATE")
check(not re.search(r"\b(INSERT|UPDATE|DELETE|MERGE|CREATE|DROP|TRUNCATE)\b", sql, re.IGNORECASE),
      "union SQL is SELECT-only")
for bad in [fw.TableSpec("ds", "t; DROP TABLE x", "d", 1, fw.GEN),
            fw.TableSpec("ds", "t", "d`) --", 1, fw.GEN)]:
    try:
        fw.build_union_sql([bad])
        check(False, "unsafe identifier rejected")
    except ValueError:
        check(True, f"unsafe identifier rejected: {bad.table!r}/{bad.date_col!r}")
try:
    fw.build_union_sql(fw.SPECS, "proj`; x")
    check(False, "unsafe project rejected")
except ValueError:
    check(True, "unsafe project id rejected")

# union succeeds
snap_dates = {k: D(v) for k, v in SNAPSHOT_2026_10_27.items()}
c = FakeClient(lambda q: [{"k": k, "d": v} for k, v in snap_dates.items()])
got = fw.fetch_latest(c, fw.SPECS, project="ik-marketing-data")
check(got == snap_dates and len(c.sqls) == 1, "fetch_latest: one UNION ALL query when it succeeds")

# union fails -> per-table fallback, one table 403s
def per_table(q):
    if "UNION ALL" in q:
        return RuntimeError("403 Access Denied: Table ...competitor_intel")
    k = _key_of(q)
    if k == "Market_Research.competitor_intel":
        return RuntimeError("403 Access Denied")
    return [{"k": k, "d": snap_dates[k]}]

c = FakeClient(per_table)
got = fw.fetch_latest(c, fw.SPECS, project="ik-marketing-data")
check(len(c.sqls) == 1 + len(fw.SPECS), "fetch_latest: falls back to one query per table")
check(got["Market_Research.competitor_intel"] is None
      and all(got[k] == snap_dates[k] for k in keys if k != "Market_Research.competitor_intel"),
      "fetch_latest: the failing table is None, the rest are read")
fb = fw.evaluate(got, TODAY)
check(any(x.key == "Market_Research.competitor_intel" and x.no_data for x in fb),
      "fetch_latest: one 403 becomes a NO DATA breach, not a crash")

# every query fails -> FreshnessQueryError
c = FakeClient(lambda q: RuntimeError("RefreshError: invalid_grant"))
try:
    fw.fetch_latest(c, fw.SPECS, project="ik-marketing-data")
    check(False, "all-tables failure raises")
except fw.FreshnessQueryError:
    check(True, "fetch_latest: every table failing raises FreshnessQueryError")

# empty table -> MAX() NULL -> None (not an error)
c = FakeClient(lambda q: [{"k": k, "d": None} for k in keys])
check(all(v is None for v in fw.fetch_latest(c, fw.SPECS, project="p").values()),
      "fetch_latest: NULL MAX (empty table) -> None")


# A BigQuery OUTAGE (review-2) must not fan out into one query per table, and
# the read has a hard time budget: either way FreshnessQueryError -> exit 1
# well before the workflow's job timeout. Name/attribute-alike stand-ins for
# the google.api_core exceptions (the pure run has no google packages).
class RetryError(Exception):            # google.api_core.exceptions.RetryError
    pass


class InternalServerError(Exception):   # GoogleAPICallError subclasses carry .code
    code = 500


class Forbidden(Exception):
    code = 403

    def __init__(self, msg, reason):
        super().__init__(msg)
        self.errors = [{"reason": reason}]


for exc, label in [(RetryError("Timeout of 30.0s exceeded"), "RetryError"),
                   (TimeoutError(), "TimeoutError (result() timeout)"),
                   (InternalServerError("backendError"), "HTTP 500"),
                   (Forbidden("Exceeded rate limits", "rateLimitExceeded"), "403 rateLimitExceeded")]:
    c = FakeClient(lambda q, e=exc: e)
    try:
        fw.fetch_latest(c, fw.SPECS, project="p")
        check(False, f"{label} on the UNION ALL raises")
    except fw.FreshnessQueryError as err:
        check(len(c.sqls) == 1 and "fallback skipped" in str(err),
              f"fetch_latest: {label} on the UNION ALL -> FreshnessQueryError, no per-table fan-out")
check(not fw._is_systemic(Forbidden("Access Denied", "accessDenied"))
      and not fw._is_systemic(RuntimeError("404 Not found: Table")),
      "fetch_latest: a per-table 403 accessDenied / 404 is NOT systemic (keeps the fallback)")


def outage_at_third_table(q):
    if "UNION ALL" in q:
        return RuntimeError("400 Cannot query over tables in different locations")
    return InternalServerError("backendError") if _key_of(q) == fw.SPECS[2].key else [
        {"k": _key_of(q), "d": TODAY}]


c = FakeClient(outage_at_third_table)
try:
    fw.fetch_latest(c, fw.SPECS, project="p")
    check(False, "a 5xx inside the per-table fallback raises")
except fw.FreshnessQueryError:
    check(len(c.sqls) == 1 + 3, "fetch_latest: a 5xx mid-fallback stops the remaining per-table queries")


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


clk = FakeClock()


def slow(q):                             # every query "takes" 30s; the union is non-systemic
    clk.t += 30
    if "UNION ALL" in q:
        return RuntimeError("400 Cannot query over tables in different locations")
    return [{"k": _key_of(q), "d": TODAY}]


c = FakeClient(slow)
try:
    fw.fetch_latest(c, fw.SPECS, project="p", budget_s=240, clock=clk)
    check(False, "fetch_latest over budget raises")
except fw.FreshnessQueryError as err:
    check("240s budget" in str(err) and len(c.sqls) == 1 + 7 and clk.t <= 240 + 30,
          f"fetch_latest: stops at its time budget ({len(c.sqls)} queries, {clk.t:.0f}s)")

# build_client config errors (never reaches the google import)
saved_env = dict(os.environ)


def reset_env(**kv):
    os.environ.clear()
    os.environ.update(saved_env)
    for name in ("WATCHDOG_BQ_SA_JSON", "SLACK_BOT_TOKEN", "SLACK_CHANNEL_ID", "WATCHDOG_DRY_RUN",
                 "WATCHDOG_MENTION", "GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID",
                 "BQ_PROJECT"):
        os.environ.pop(name, None)
    os.environ.update(kv)


for raw, label in [(None, "unset"), ("not json {", "not JSON"), ('{"type": "user"}', "not an SA key")]:
    reset_env(**({} if raw is None else {"WATCHDOG_BQ_SA_JSON": raw}))
    try:
        fw.build_client()
        check(False, f"build_client rejects {label}")
    except fw.WatchdogConfigError as exc:
        check("not json {" not in str(exc), f"build_client: {label} -> WatchdogConfigError ({exc})")
# A leading UTF-8 BOM (Windows PowerShell 5.1 pipe into `gh secret set`,
# 2026-10-27) must not make a valid key or token look broken.
reset_env(WATCHDOG_BQ_SA_JSON='﻿{"type": "service_account"}\r\n', SLACK_BOT_TOKEN="﻿xoxb-1-abc \n")
check(fw._secret_env("WATCHDOG_BQ_SA_JSON") == '{"type": "service_account"}',
      "_secret_env: strips a leading BOM + whitespace from the SA JSON")
check(fw._secret_env("SLACK_BOT_TOKEN") == "xoxb-1-abc", "_secret_env: strips a leading BOM from the Slack token")
check(fw._secret_env("SLACK_CHANNEL_ID") == "", "_secret_env: unset -> empty string")
reset_env(BQ_PROJECT="bad project!")
try:
    fw.bq_project()
    check(False, "bad BQ_PROJECT rejected")
except fw.WatchdogConfigError:
    check(True, "bq_project: a BQ_PROJECT with unsafe characters is rejected")
reset_env()
check(fw.bq_project() == "ik-marketing-data", "bq_project: default ik-marketing-data")

# ---------------------------------------------------------------------------
# post_slack (fake opener)
# ---------------------------------------------------------------------------
TOKEN = "xoxb-test-SECRET-0123456789"
op = FakeOpener([{"ok": True, "ts": "1.2"}])
res = fw.post_slack(TOKEN, "C123", "hello", opener=op)
req = op.requests[0]
body = json.loads(req.data.decode("utf-8"))
check(res["ok"] and req.full_url == "https://slack.com/api/chat.postMessage" and req.get_method() == "POST",
      "post_slack: POSTs to chat.postMessage")
check(req.get_header("Authorization") == f"Bearer {TOKEN}", "post_slack: Bearer token header")
check(body == {"channel": "C123", "text": "hello", "unfurl_links": False, "unfurl_media": False},
      "post_slack: body {channel, text, unfurl_links:false, unfurl_media:false}")
sleeps = []
op = FakeOpener([http_error(429, {"Retry-After": "7"}), {"ok": True}])
fw.post_slack(TOKEN, "C123", "hi", opener=op, sleep=sleeps.append)
check(len(op.requests) == 2 and sleeps == [7], "post_slack: one retry after 429 Retry-After")
op = FakeOpener([http_error(429), http_error(429)])
try:
    fw.post_slack(TOKEN, "C123", "hi", opener=op, sleep=sleeps.append)
    check(False, "second 429 raises")
except fw.SlackPostError as exc:
    check("429" in str(exc) and TOKEN not in str(exc), "post_slack: a second 429 raises")
for outcome, needle in [({"ok": False, "error": "not_in_channel"}, "invite the bot"),
                        ({"ok": False, "error": "invalid_auth"}, "invalid_auth"),
                        (http_error(500), "HTTP 500"),
                        (urllib.error.URLError("dns"), "URLError")]:
    try:
        fw.post_slack(TOKEN, "C123", "hi", opener=FakeOpener([outcome]))
        check(False, f"post_slack raises on {needle}")
    except fw.SlackPostError as exc:
        check(needle in str(exc) and TOKEN not in str(exc), f"post_slack: raises on {needle}, token not echoed")

# ---------------------------------------------------------------------------
# (l) main() with monkeypatched I/O
# ---------------------------------------------------------------------------
log_buf = io.StringIO()
log_handler = logging.StreamHandler(log_buf)
fw.log.addHandler(log_handler)
fw.log.propagate = False              # capture main()'s logs here, keep the PASS list readable
real_build, real_fetch, real_post = fw.build_client, fw.fetch_latest, fw.post_slack
real_expiry_status = fw.expiry_status
calls = {"post": [], "fetch": 0}


def patch(latest=None, fetch_exc=None, post_exc=None, calendar=CAL):
    calls["post"].clear()
    calls["fetch"] = 0
    # main() reads the module calendar: pin it (synthetic by default) so an
    # EXPIRIES renewal or a new entry can never flip a main() assertion.
    fw.expiry_status = lambda today, expiries=calendar: real_expiry_status(today, expiries)

    def fake_fetch(client, specs=fw.SPECS, project=None):
        calls["fetch"] += 1
        if fetch_exc:
            raise fetch_exc
        return dict(SNAPSHOT_2026_10_27 if latest is None else latest)

    def fake_post(token, channel, text, **kw):
        calls["post"].append((token, channel, text))
        if post_exc:
            raise post_exc
        return {"ok": True}

    fw.build_client = lambda: object()
    fw.fetch_latest = fake_fetch
    fw.post_slack = fake_post


def run_main(argv):
    out = io.StringIO()
    with redirect_stdout(out):
        rc = fw.main(argv)
    return rc, out.getvalue()


healthy_today = {k: TODAY for k in keys}

reset_env()
patch()
rc, out = run_main(["--dry-run", "--today", "2026-10-27"])
check(rc == 0 and calls["post"] == [], "(l) --dry-run: returns 0 and never calls post_slack")
check("[DRY RUN] Would post to Slack:" in out and "7 table(s) LATE (2026-10-27 IST)" in out,
      "(l) --dry-run prints the rendered message")

reset_env(WATCHDOG_DRY_RUN="true")
patch()
rc, out = run_main(["--today", "2026-10-27"])
check(rc == 0 and calls["post"] == [] and "[DRY RUN]" in out,
      "(l) WATCHDOG_DRY_RUN=true behaves like --dry-run (no Slack env needed)")

reset_env(SLACK_BOT_TOKEN=TOKEN, SLACK_CHANNEL_ID="C123",
          GITHUB_SERVER_URL="https://github.com", GITHUB_REPOSITORY="o/r", GITHUB_RUN_ID="42")
patch()
rc, out = run_main(["--today", "2026-10-27"])
check(rc == 0 and len(calls["post"]) == 1 and calls["post"][0][:2] == (TOKEN, "C123"),
      "(l) live mode posts exactly once with the env token/channel")
posted = calls["post"][0][2]
check(posted.startswith("<!here> ") and "<https://github.com/o/r/actions/runs/42|workflow run>" in posted,
      "(l) default mention <!here> on an escalation day; footer links the workflow run")

patch(post_exc=fw.SlackPostError("Slack API error: not_in_channel"))
rc, _ = run_main(["--today", "2026-10-27"])
check(rc == 1 and len(calls["post"]) == 1, "(l) a post_slack exception returns 1")

patch(post_exc=RuntimeError(f"boom {TOKEN}"))
rc, _ = run_main(["--today", "2026-10-27"])
check(rc == 1, "(l) any post failure returns 1")

reset_env(SLACK_CHANNEL_ID="C123")
patch()
rc, _ = run_main(["--today", "2026-10-27"])
check(rc == 1 and calls["fetch"] == 0 and calls["post"] == [],
      "(l) missing SLACK_BOT_TOKEN in live mode: exit 1 before any BigQuery read")

reset_env()
patch(fetch_exc=fw.FreshnessQueryError("all 21 per-table freshness queries failed"))
rc, _ = run_main(["--dry-run", "--today", "2026-10-27"])
check(rc == 1, "(l) BigQuery failure for ALL tables returns 1")

fw.fetch_latest = real_fetch
fw.build_client = real_build          # real build_client: WATCHDOG_BQ_SA_JSON unset
rc, _ = run_main(["--dry-run", "--today", "2026-10-27"])
check(rc == 1 and "WATCHDOG_BQ_SA_JSON is not set" in log_buf.getvalue(),
      "(l) missing WATCHDOG_BQ_SA_JSON returns 1 with a clear message")

reset_env(SLACK_BOT_TOKEN=TOKEN, SLACK_CHANNEL_ID="C123")
patch(latest=healthy_today)
rc, out = run_main(["--today", "2026-10-27"])
check(rc == 0 and calls["post"] == [], "(l) healthy Tuesday, heartbeat auto: nothing posted")
patch(latest={k: MONDAY for k in keys})
rc, _ = run_main(["--today", "2026-10-26"])
check(rc == 0 and len(calls["post"]) == 1 and calls["post"][0][2].startswith(":white_check_mark:"),
      "(l) healthy Monday, heartbeat auto: one all-green post")
patch(latest=healthy_today)
rc, _ = run_main(["--today", "2026-10-27", "--heartbeat", "always"])
check(len(calls["post"]) == 1, "(l) --heartbeat always posts all-green on a Tuesday")
patch(latest={k: MONDAY for k in keys})
rc, _ = run_main(["--today", "2026-10-26", "--heartbeat", "never"])
check(calls["post"] == [], "(l) --heartbeat never: no post on a healthy Monday")

patch()
rc, _ = run_main(["--today", "2026-10-27", "--fail-on-breach"])
check(rc == 2 and len(calls["post"]) == 1, "(l) --fail-on-breach: exit 2 after posting an alert breach")
warn_only = {**healthy_today, "SIP_pattern_rag.ce_keyword_rejects": D("2026-10-01")}
patch(latest=warn_only)
rc, _ = run_main(["--today", "2026-10-27", "--fail-on-breach"])
check(rc == 0 and len(calls["post"]) == 1, "(l) --fail-on-breach ignores warn-only breaches")
patch(latest=healthy_today)
rc, _ = run_main(["--today", "2026-10-27", "--fail-on-breach"])
check(rc == 0, "(l) --fail-on-breach with nothing late: exit 0")
rc, _ = run_main(["--today", "27-10-2026"])
check(rc == 1, "(l) a malformed --today returns 1")

for value, expect in [(None, "<!here> "), ("", ":rotating_light:"), ("none", ":rotating_light:"),
                      ("OFF", ":rotating_light:"), ("<!channel>", "<!channel> ")]:
    reset_env(SLACK_BOT_TOKEN=TOKEN, SLACK_CHANNEL_ID="C123",
              **({} if value is None else {"WATCHDOG_MENTION": value}))
    patch()
    run_main(["--today", "2026-10-27"])
    check(calls["post"][0][2].startswith(expect), f"(l) WATCHDOG_MENTION={value!r} -> starts {expect!r}")

# main() wires the REAL EXPIRIES (date-agnostic: 10 days before its soonest entry).
warn_day = nxt.expires - timedelta(days=10)
reset_env(SLACK_BOT_TOKEN=TOKEN, SLACK_CHANNEL_ID="C123")
patch(latest={k: warn_day for k in keys}, calendar=fw.EXPIRIES)
rc, _ = run_main(["--today", warn_day.isoformat()])
check(rc == 0 and len(calls["post"]) == 1 and "Expiry calendar" in calls["post"][0][2]
      and f"*{fw._esc(nxt.name)}* expires {nxt.expires} (in 10d)" in calls["post"][0][2],
      "(l) main() reads the real EXPIRIES: its soonest entry posts 10d ahead")

all_out = log_buf.getvalue()
check(TOKEN not in all_out and "[REDACTED]" in all_out,
      "(l) the Slack token never appears in logs (redacted from a third-party error)")
check("google.cloud.bigquery" not in sys.modules, "(l) main() never imported google-cloud-bigquery")

fw.build_client, fw.fetch_latest, fw.post_slack = real_build, real_fetch, real_post
fw.expiry_status = real_expiry_status
fw.log.removeHandler(log_handler)
fw.log.propagate = True
reset_env()

# ---------------------------------------------------------------------------
# workflow drift guards (text-level: no pyyaml in the credential-free run)
# ---------------------------------------------------------------------------
if WORKFLOW.exists():
    wf = WORKFLOW.read_text(encoding="utf-8")
    check("cron: '30 10 * * *'" in wf, "workflow: cron 10:30 UTC (16:00 IST)")
    check(re.search(r"^permissions:\n  contents: read\n", wf, re.MULTILINE) is not None,
          "workflow: permissions contents: read")
    check("group: freshness-watchdog" in wf and "cancel-in-progress: false" in wf,
          "workflow: concurrency group, no cancel")
    check("timeout-minutes:" in wf and "python-version: '3.12'" in wf, "workflow: timeout + Python 3.12")
    exprs = [ln for ln in wf.splitlines() if "${{" in ln and not ln.lstrip().startswith("#")]
    check(exprs and all(re.match(r"^\s+[A-Z_]+: ", ln) for ln in exprs),
          "workflow: every ${{ }} expression sits in an env: mapping, none inside run:")
    for name in ("WATCHDOG_BQ_SA_JSON", "SLACK_BOT_TOKEN", "SLACK_CHANNEL_ID"):
        check(f"{name}: ${{{{ secrets.{name} }}}}" in wf, f"workflow: secret {name} passed via env")
    run_m = re.search(r"run: timeout (\d+)m python ops/freshness_watchdog\.py", wf)
    job_m = re.search(r"\n  watchdog:\n(?:    .*\n)*?    timeout-minutes: (\d+)", wf)
    check(run_m is not None and job_m is not None, "workflow: runs the watchdog under `timeout <N>m`")
    shell_s, job_s = int(run_m.group(1)) * 60, int(job_m.group(1)) * 60
    # A hang must end as a job FAILURE (shell timeout, exit 124), never as the
    # job-timeout "cancelled", and the script's own budget must fit inside it.
    check(shell_s < job_s, f"workflow: shell timeout {shell_s}s < job timeout-minutes {job_s}s")
    check(fw.FETCH_BUDGET_S + fw.QUERY_WORST_CASE_S < shell_s,
          f"workflow: FETCH_BUDGET_S + QUERY_WORST_CASE_S "
          f"({fw.FETCH_BUDGET_S}+{fw.QUERY_WORST_CASE_S}s) < shell timeout {shell_s}s")
    gating = [ln for ln in wf.splitlines()
              if "tests_freshness_watchdog" in ln and not ln.lstrip().startswith("#")]
    check(gating == [], "workflow: the live run is NOT gated on the unit tests (a red test must "
                        "never switch alerting off; ci.yml runs them)")
    # Backstop alert: independent of GitHub's failure-email routing (which a
    # bot re-enable can move to github-actions[bot]) and of timeouts.
    alert = wf[wf.find("\n  alert-on-failure:\n"):] if "\n  alert-on-failure:\n" in wf else ""
    check("needs: [watchdog, keepalive]" in alert
          and "if: always() && (needs.watchdog.result != 'success' || "
              "needs.keepalive.result != 'success')" in alert,
          "workflow: alert-on-failure runs on ANY non-success watchdog/keepalive result "
          "(failure, timeout-cancel)")
    check("issues: write" in alert and "gh issue create" in alert and "gh issue comment" in alert
          and '--assignee "$OWNER"' in alert and "@$OWNER" in alert
          and "OWNER: ${{ github.repository_owner }}" in alert,
          "workflow: alert-on-failure opens/updates an issue assigned to and @-mentioning the owner")
    keep = wf[wf.find("\n  keepalive:\n"):wf.find("\n  alert-on-failure:\n")]
    check('if [ "$age" -lt 45 ]' in keep and "age=999" in keep
          and "actions/workflows/freshness-watchdog.yml/enable" in keep,
          "workflow: keepalive re-enables only after 45 quiet days (fail-safe when the date is unknown)")
else:
    print(f"SKIP: workflow drift guards ({WORKFLOW} not found)")

if CI_WORKFLOW.exists():
    ci = CI_WORKFLOW.read_text(encoding="utf-8")
    ci_code = "\n".join(ln for ln in ci.splitlines() if not ln.lstrip().startswith("#"))
    check("run: python ops/tests_freshness_watchdog.py" in ci_code,
          "ci.yml: runs these unit tests on every PR and push to main")
    # `bash -n a b` parses only a (b becomes $1), so every `bash -n` gets one file.
    multi = [m.group(0) for m in re.finditer(r"bash -n((?:[ \t]+[^\s;|&]+)+)", ci_code)
             if len(m.group(1).split()) > 1]
    check(multi == [], f"ci.yml: every `bash -n` checks exactly one file {multi}")
    loop = re.search(r'for f in ([^;\n]+); do bash -n "\$f"', ci_code)
    check(loop is not None and {"run_daily.sh", "fly-deploy/entrypoint.sh"} <= set(loop.group(1).split()),
          "ci.yml: shell syntax checks both run_daily.sh and fly-deploy/entrypoint.sh")
else:
    print(f"SKIP: ci.yml drift guards ({CI_WORKFLOW} not found)")

# ---------------------------------------------------------------------------
# _BoundedClient against the REAL google-cloud-bigquery retry machinery
# (review-2). Only when the library is installed (the unit command adds it;
# the stdlib-only run skips). No network: anonymous credentials, every HTTP
# call stubbed to a 500 backendError, and a fake clock that advances only on
# the library's own retry sleeps. With the library defaults one query alone
# retried for ~1100 fake seconds; bounded, the whole read must give up within
# one query's worst case.
try:
    from google.api_core import exceptions as gexc
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery
except ImportError:
    print("SKIP: _BoundedClient outage simulation (google-cloud-bigquery not installed)")
else:
    import time as _time
    real_client = bigquery.Client(project="p", credentials=AnonymousCredentials())
    http_calls = [0]

    def api_request(*a, **kw):
        http_calls[0] += 1
        raise gexc.InternalServerError("backendError", errors=[{"reason": "backendError"}])

    real_client._connection.api_request = api_request
    fake_now = [0.0]
    real_monotonic, real_sleep = _time.monotonic, _time.sleep
    _time.monotonic = lambda: fake_now[0]
    _time.sleep = lambda s: fake_now.__setitem__(0, fake_now[0] + s)
    raised = None
    try:
        fw.fetch_latest(fw._BoundedClient(real_client), fw.SPECS, project="p")
    except Exception as exc:              # noqa: BLE001 -- asserted below
        raised = exc
    finally:
        _time.monotonic, _time.sleep = real_monotonic, real_sleep
    check(isinstance(raised, fw.FreshnessQueryError),
          f"_BoundedClient: a sustained BigQuery 500 -> FreshnessQueryError ({fw._err(raised)})")
    check(fake_now[0] <= fw.QUERY_WORST_CASE_S,
          f"_BoundedClient: gives up after {fake_now[0]:.0f}s of retries ({http_calls[0]} HTTP "
          f"calls), within QUERY_WORST_CASE_S={fw.QUERY_WORST_CASE_S}s")

print("\nAll freshness watchdog tests passed.")
