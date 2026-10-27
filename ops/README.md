# ops/ -- freshness watchdog (M001 T05)

On 2026-10-27 the MIS dashboard tiles were found stale and **nothing had
alerted**. `pmis_daily_segment_facts` had been stuck at 2026-10-15 for 12 days
(LinkedIn-Version 202503 started returning 426), the five `Market_Research`
tables were 63-70 days old (the Fly-native monthly schedule never fired), and
`ce_campaign_registry` was 115 days old (it was never scheduled). Each of those
runs looked successful from the inside.

`freshness_watchdog.py` checks the **data**, not the runs. Once a day, from
GitHub Actions (`.github/workflows/freshness-watchdog.yml`, 10:30 UTC = 16:00
IST), it reads `MAX(<date col>)` for every pipeline table and posts to Slack
(#ib-perf) when any table is older than its SLA, or when a dated credential/API
deadline is close. It is **SELECT-only**, and there is no BigQuery write path.

| File | What |
|---|---|
| `freshness_watchdog.py` | SPECS (SLAs), EXPIRIES (calendar), pure `evaluate()` / `render_message()`, BigQuery read, Slack post, CLI |
| `tests_freshness_watchdog.py` | stdlib-only tests (no BigQuery package, no credentials, no network). `../.github/workflows/ci.yml` runs them on every PR and push to main |
| `../.github/workflows/freshness-watchdog.yml` | daily schedule + manual dispatch (`dry_run`, `heartbeat`). It deliberately does **not** run the tests first, so a red test can never switch alerting off |

## SLA table

Lags are IST calendar days: `lag = today_IST - MAX(date col)`. A table is
**late** when `lag > max_lag`, or when it has no readable date (NO DATA).

| Table | Date col | Healthy base lag | SLA (max_lag) | Severity | Pipeline (runbook) |
|---|---|---|---|---|---|
| `SIP_pattern_rag.ce_generation_runs` | run_date | 0 | 1 | alert | ik-ad-generator |
| `SIP_pattern_rag.ce_keyword_candidates` | run_date | 0 | 1 | alert | ik-ad-generator |
| `SIP_pattern_rag.ce_ad_copy_drafts` | run_date | 0 | 1 | alert | ik-ad-generator |
| `SIP_pattern_rag.ce_audience_angles` | run_date | 0 | 1 | alert | ik-ad-generator |
| `SIP_pattern_rag.ce_lead_quality_scores` | run_date | 0 | 1 | alert | ik-ad-generator |
| `SIP_pattern_rag.ce_kw_search_term_perf` | date | 1 | 2 | alert | ik-ad-generator |
| `SIP_pattern_rag.ce_value_ledger` | run_date | 1 | 2 | alert | ik-ad-generator |
| `SIP_pattern_rag.ce_keyword_rejects` | run_date | sparse (gaps up to 3) | 5 | warn | ik-ad-generator |
| `SIP_pattern_rag.ce_campaign_registry` | snapshot_date | 7 (weekly, Monday IST) | 8 | alert | ik-ad-generator (`registry_refresh` step) |
| `SIP_pattern_rag.pmis_run_metadata` | run_date | 0 | 1 | alert | ik-anomaly |
| `SIP_pattern_rag.pmis_full_funnel` | date | 0 | 1 | alert | ik-anomaly |
| `SIP_pattern_rag.pmis_daily_segment_facts` | date | 1 | 2 | alert | ik-anomaly |
| `SIP_pattern_rag.pmis_anomaly_facts` | run_date | 1 | 2 | alert | ik-anomaly |
| `SIP_pattern_rag.pmis_daily_summary` | date | 0 | 1 | warn | ik-anomaly |
| `SIP_pattern_rag.pmis_platform_summary` | date | 0 | 1 | warn | ik-anomaly |
| `SIP_pattern_rag.pmis_program_summary` | date | 0 | 1 | warn | ik-anomaly |
| `SIP_pattern_rag.pmis_recommendations` | run_date | 1 | 2 | warn | ik-anomaly |
| `Market_Research.job_market_demand` | run_date | monthly (1st) | 35 | alert | ik-market-research-jobs |
| `Market_Research.social_trends` | run_date | monthly (2nd) | 35 | alert | ik-market-research-weekly |
| `Market_Research.blog_publications` | run_date | monthly (2nd) | 35 | alert | ik-market-research-weekly |
| `Market_Research.competitor_intel` | run_date | monthly (2nd) | 35 | alert | ik-market-research-weekly |
| `Market_Research.executive_summary` | run_date | monthly (2nd) | 35 | alert | ik-market-research-weekly |

**The rule is `max_lag = healthy base lag + 1`.** One missed run never alarms,
and two consecutive misses always do. "Base lag" is the lag observed right
after a healthy run, measured on 2026-10-27 over the last 30 distinct dates.
For example, `ce_generation_runs` stamps the IST run day, so its base lag is 0;
`pmis_daily_segment_facts` stamps the data date (yesterday), so its base lag
is 1. The two cadence-based exceptions:

- **Weekly registry, SLA 8.** The `registry_refresh` step runs Mondays IST,
  plus a catch-up whenever the snapshot is more than 7 days old.
- **Monthly Market Research, SLA 35.** The longest healthy gap is a 31-day
  month, plus 4 days of grace. Jobs run on the 1st and the pipeline on the
  2nd, both at 03:30 UTC from the Cloudflare `ik-intelligence-trigger`.

The `pmis_*_summary` and `pmis_recommendations` tables are **warn** because the
MIS dashboard does not read them, and if the whole anomaly run dies,
`pmis_run_metadata` and `pmis_full_funnel` already alert.
`ce_keyword_rejects` is **warn** because it is written only when the gate
rejects something.

The schedule is tuned so a healthy pipeline never false-alarms at 10:30 UTC.
Even if the generation chain is still running (ce_* at lag 1, value_ledger at
lag 2), everything is inside SLA. The test suite pins this case with a fixture.

## Reading an alert

```
<!here> :rotating_light: Freshness watchdog -- 7 table(s) LATE (2026-10-27 IST)

*ik-anomaly* -- `fly logs -a ik-anomaly` (daily run, 04:30 UTC)
- `SIP_pattern_rag.pmis_daily_segment_facts` latest 2026-10-15 (12d old, SLA 2d) -- LATE since 2026-10-18 (day 10)
```

- **latest**: `MAX(date col)` in BigQuery.
- **LATE since**: the first IST date the check would have failed, computed as
  `latest + max_lag + 1`. It comes from the data itself, so a missed watchdog
  run or a GitHub delay cannot shift it.
- **day N**: `today - late_since + 1`. On the first failing day, N = 1.
- **NO DATA / unreadable**: the table is empty, missing, or the watchdog's
  service account cannot read it. NO DATA lines come first in their severity
  but never page by themselves, because there is no date to count from. If
  every table is unreadable, the run exits 1 instead, which is an
  infrastructure failure (see exit codes).
- Lines are grouped by pipeline. Each group starts with that pipeline's first
  runbook command.

## Escalation (stateless)

The alert repeats **once a day** while anything is late, with no state store:
the cron firing once a day is the dedup. The mention (`WATCHDOG_MENTION`,
default `<!here>`) is added only on **escalation days**: day 1, day 3, and
every 7th day (7, 14, 21, ...), and only for **alert**-severity tables. Other
days post the same message without the mention. Warn tables never page.

Change or disable the mention with a repo variable. GitHub variables cannot be
empty, so `none` (or `off`) disables it:

```bash
gh variable set WATCHDOG_MENTION -R christo0192/ik-mis --body 'none'                # no paging
gh variable set WATCHDOG_MENTION -R christo0192/ik-mis --body '<!subteam^S0123>'    # a user group
gh variable delete WATCHDOG_MENTION -R christo0192/ik-mis                           # back to <!here>
```

When nothing is late, nothing is posted, except an all-green **heartbeat** on
Mondays IST (`--heartbeat auto`). The heartbeat also names the next calendar
deadline.

## Expiry calendar

`EXPIRIES` in `freshness_watchdog.py` lists dated deadlines that otherwise fail
silently. The 2026-10-27 root cause was one of these.

| Entry | Date | Basis |
|---|---|---|
| LinkedIn OAuth refresh token | 2027-03-24 | Probed 2026-10-27: about 148 days left |
| LinkedIn-Version 202610 pin | 2027-08-01 | **Estimate.** On 2026-10-27, versions up to 202512 returned 426 and 202601..202610 returned 200, so a version lives about 10-12 months |

From **30 days before** a deadline, the message includes an "Expiry calendar"
section and posts daily, even when every table is fresh. This is warn
severity, so it does not page. Once the date has **passed**, the entry pages
on escalation days, using the same day 1/3/7k rule, counting the expiry date
as day 1. After renewing, follow the entry's action text and **update its date
in `EXPIRIES`**. That edit is the only thing that clears the warning.

No test edit is needed for a renewal or a new entry. The tests never pin a real
`EXPIRIES` date. They use synthetic calendars and check only the structure of
the real entries: a unique name, a plain `date(...)` (not a `datetime`), and an
action ending in "then update this date". Run `python
ops/tests_freshness_watchdog.py` after the edit; CI also runs it on the PR.

## Changing an SLA

When a pipeline's cadence changes, edit its `TableSpec` in `SPECS` using the
same rule, `max_lag = new base lag + 1`. For example, a daily registry refresh
would make `ce_campaign_registry` SLA 1. Then:

1. Update the SLA table above.
2. Update the matching assertions in `tests_freshness_watchdog.py` (the
   "SPECS integrity" block and the healthy-at-10:30 fixture).
3. Run `python ops/tests_freshness_watchdog.py`.

To add a table, append a `TableSpec`. Identifiers must match
`^[A-Za-z0-9_]+$`, because they are interpolated into SQL and come only from
this file. Add the table to the replay snapshot in the tests too, since one
test asserts that the snapshot covers exactly the SPECS keys.

The tests also pin `freshness-watchdog.yml` with text-level drift guards: the
cron (`30 10 * * *`), Python 3.12, `permissions: contents: read`, secrets via
`env:` only, no unit-test step before the live run, the `timeout 8m` wrapper
(shorter than the job's `timeout-minutes`, longer than the script's BigQuery
budget), the `alert-on-failure` issue job, and the 45-day keepalive gate. If
you change the schedule or the Python version, update the "workflow drift
guards" block too.
A red test only fails CI. It never stops the scheduled watchdog.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Run succeeded: breaches posted, or nothing to post |
| 1 | Watchdog infrastructure failure: a missing env var, a BigQuery auth/query failure for **all** tables, a BigQuery outage (5xx, rate limit, retries exhausted), the read running past its 240s budget, or a failed Slack post |
| 2 | Only with `--fail-on-breach`: an alert-severity table is late (after posting). The workflow does not use this flag by default |
| 124 | The `timeout 8m` wrapper killed a run that hung anyway. It counts as a job failure |

**Backstop when Slack or BigQuery credentials break.** Any run where the
`watchdog` or `keepalive` job does not succeed (exit 1, exit 124, or a
job-timeout cancel) makes the `alert-on-failure` job open a GitHub issue titled
`freshness-watchdog is failing`, assigned to and @-mentioning the repo owner.
While that issue stays open, later failures add a comment to it. Close it once
the cause is fixed. This does not rely on GitHub's "run failed" email, for two
reasons. That email goes to whoever GitHub treats as the workflow's actor,
which can become `github-actions[bot]` after a keepalive re-enable. And a job
that hits `timeout-minutes` ends as cancelled, which sends no failure email.

The BigQuery read is one `UNION ALL` query. If it fails, the watchdog falls
back to one query per table, so a single 403 or missing table becomes a NO
DATA line, not a crash. A service-wide failure (timeout, HTTP 5xx/429,
`backendError`/`internalError`/`rateLimitExceeded`, retries exhausted) skips
that fallback and exits 1, because 22 more queries would only repeat the
outage. Each query has bounded retries (30s per API call, no job-level retry;
the library defaults are 600s and 2400s). The fallback also stops once the
whole read has used 240s (`FETCH_BUDGET_S`).

## Running locally

```bash
# tests: stdlib only, no credentials
uv run --no-project --python 3.12 python ops/tests_freshness_watchdog.py

# live read, print only (needs a key that can read both datasets)
WATCHDOG_BQ_SA_JSON="$(cat ~/.gcp-keys/claude-code-dev-bq.json)" \
  uv run --no-project --python 3.12 --with 'google-cloud-bigquery>=3,<4' \
  python ops/freshness_watchdog.py --dry-run --heartbeat always
```

Flags: `--dry-run` (or env `WATCHDOG_DRY_RUN=true`), `--heartbeat
{auto,always,never}`, `--today YYYY-MM-DD` (replay a date), `--fail-on-breach`.
The service-account JSON is parsed in memory and never written to disk.
Secrets are read from env only and are never logged; error text is redacted.
The OAuth scope is `cloud-platform`: `bigquery.readonly` gets 403 on
`jobs.insert`. Read-only behavior comes from IAM plus SELECT-only code.

## GitHub setup (one time)

These commands are for Git Bash. Values are piped in and never echoed.

```bash
export PATH="/c/Program Files/GitHub CLI:$PATH"
tr -d '\r' < ~/.gcp-keys/claude-code-dev-bq.json | gh secret set WATCHDOG_BQ_SA_JSON -R christo0192/ik-mis
grep '^SLACK_BOT_TOKEN=' /c/mcp-servers/slack-tools/.env | cut -d= -f2- | tr -d '\r"' | gh secret set SLACK_BOT_TOKEN -R christo0192/ik-mis
grep '^SLACK_CHANNEL_ID=' /c/mcp-servers/slack-tools/.env | cut -d= -f2- | tr -d '\r"' | gh secret set SLACK_CHANNEL_ID -R christo0192/ik-mis
gh secret list -R christo0192/ik-mis                       # 3 names, values never shown

# after merge to main (schedules and dispatch only run from the default branch)
gh workflow run freshness-watchdog.yml -R christo0192/ik-mis -f dry_run=true -f heartbeat=always
gh run watch -R christo0192/ik-mis
gh workflow run freshness-watchdog.yml -R christo0192/ik-mis -f dry_run=false -f heartbeat=always   # one real post
```

- **Identity.** `claude-code-dev-bq` reads both datasets, but it is the
  generation *writer* service account. Prefer a dedicated service account
  with `bigquery.dataViewer` on `SIP_pattern_rag` and `Market_Research`, plus
  `bigquery.jobUser` on the project, then rotate the secret to it.
- **Bot.** The Slack bot (`lead_context_builder`) has `chat:write` only. If it
  is removed from private #ib-perf, posts fail with `not_in_channel`, and the
  run exits 1, which opens or updates the `freshness-watchdog is failing`
  issue (see "Exit codes").
- **First run.** The first live run is expected to report today's true
  positives until the M001 fixes land: segment_facts, the 5 Market_Research
  tables, and the registry.
- **Cron delay.** A GitHub cron can start late, but that cannot cause a false
  alarm. "Today" is the IST date, and IST equals the UTC date until 18:30 UTC.
- **Public-repo keepalive.** ik-mis is public as of 2026-10-27. GitHub
  disables scheduled workflows in public repos after 60 days with no
  repository activity. Once the last commit on the default branch is 45+
  days old, the workflow's `keepalive` job re-enables the workflow through
  the REST API, which resets that timer (if it can't read the commit date, it
  re-enables anyway). It does not do this on every run, because GitHub sends
  scheduled-run notifications to whoever last re-enabled the workflow, and
  here that would be `github-actions[bot]`. A failing keepalive also opens the
  failure issue. If the repo goes private again, the job is harmless and can
  stay. Private repos need working Actions billing, though: an Actions
  billing lock stops this watchdog completely.
- **Check the backstop once.** The first time the failure issue opens,
  confirm that its notification actually reached you (email or GitHub
  inbox). `gh run list -R christo0192/ik-mis -w freshness-watchdog.yml --json
  event,triggeringActor` shows who GitHub treats as each run's actor.
