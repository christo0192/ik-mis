# Generation department -- data layer (Phase 0 / Step 1)

The new "Generation" department (PLAN.md) reads market intelligence + ad
performance and writes candidate keywords, ad copy, audience angles, and
(Phase 2+) creative assets. This directory is its data layer: Pydantic
schemas, a guarded BigQuery writer, and the smoke test that proves both work
before anything is wired into a conductor.

## What's here

- `config.py` -- env-driven configuration (project, dataset, table prefix,
  DRY_RUN, credentials path). No machine-specific absolute paths.
- `schemas.py` -- one Pydantic model + one lockstep BigQuery schema tuple
  list per table, collected in `REGISTRY` (12 tables) and `PARTITION_FIELDS`.
- `bq_writer.py` -- the guarded writer. Every write goes through
  `write_rows(table_name, rows)`, which (1) checks the destination against
  the allowlist (`REGISTRY`'s 12 tables only -- anything else, including
  `India_Leads` or `Market_Research`, raises `WriteTargetError` before any
  API call), (2) validates every row against its Pydantic model (raises
  `pydantic.ValidationError` on a bad row), then (3) atomically replaces the
  affected partition(s) via a `table$YYYYMMDD` `WRITE_TRUNCATE` load job (or,
  in DRY_RUN, writes a local preview instead).
- `smoke_test.py` -- writes one valid row per table in DRY_RUN and asserts
  the three guarantees above. Run it after any schema or writer change.
- `staging/` -- created on first DRY_RUN write; holds
  `bq_preview_<table>.json` per table (gitignore-able; regenerated freely).

## Env vars

| Var | Default | Meaning |
|---|---|---|
| `BQ_PROJECT` | `ik-marketing-data` | GCP project. |
| `GENERATION_DATASET_MODE` | `interim` | `interim` = `ce_`-prefixed tables in `SIP_pattern_rag`. `future` = the dedicated `Creative_Engine` dataset, no prefix. **The one env var to flip when the dataset is provisioned.** |
| `TARGET_DATASET` | `SIP_pattern_rag` (interim) / `Creative_Engine` (future) | Override either mode's dataset directly if needed. |
| `TABLE_PREFIX` | `ce_` (interim) / `""` (future) | Override the table prefix directly if needed. |
| `FUTURE_DATASET` | `Creative_Engine` | Name of the dedicated dataset once requested from the data team (PLAN.md Phase 0 Step 1). |
| `DRY_RUN` | `true` | `false` (case-insensitive) enables live BigQuery writes. Anything else stays in preview mode. |
| `GENERATION_BQ_SERVICE_ACCOUNT` | `~/.gcp-keys/claude-code-dev-bq.json` | Service-account key for live writes. Only read when `DRY_RUN=false`. |
| `GENERATION_BQ_LOCATION` | `US` | BigQuery job location. |

## DRY_RUN vs live

`DRY_RUN=true` (the default) never touches BigQuery or imports
`google.cloud.bigquery` -- `write_rows()` validates the row with Pydantic,
then writes `staging/bq_preview_<table>.json` describing exactly what would
have been loaded (target table id, partition field, affected partitions,
row count, the rows themselves). This means the whole writer -- and the
smoke test -- work on a machine with only `pydantic` installed.

`DRY_RUN=false` additionally requires `google-cloud-bigquery` and a
readable service-account key (`GENERATION_BQ_SERVICE_ACCOUNT`); it performs
one `WRITE_TRUNCATE` load job per partition against the real allowlisted
table.

## Flipping to the dedicated `Creative_Engine` dataset later

When the data team provisions `Creative_Engine`, set:

```bash
export GENERATION_DATASET_MODE=future
```

That's it -- `TARGET_DATASET` becomes `Creative_Engine` and `TABLE_PREFIX`
becomes empty (tables keep their bare names, e.g. `keyword_candidates`
instead of `ce_keyword_candidates`). No code changes; re-run the DDL
equivalent in the new dataset and re-point downstream readers (dashboard
queries, the generator conductor) at the same env var.

## Dependencies

`pydantic>=2` (schema validation, always required) and
`google-cloud-bigquery` (only required for live writes -- `DRY_RUN=true`
runs fine without it, by design, so this can be developed and tested on a
machine that isn't fully provisioned yet). See `requirements.txt` at the
repo root. `python-dotenv` is included there for convenience if a future
`.env`-driven entrypoint is added; nothing here currently loads one.

## Step 3: kw feed collector (`collect_kw_perf.py`)

Persisted keyword / search-term performance feed (PLAN.md Phase 0 Step 3),
writing to the `kw_search_term_perf` table via `bq_writer.write_rows()`.
Clones the credential/auth pattern from
`/mnt/c/mcp-servers/anomaly-engine/collect_drilldown.py` (itself identical
to `collect_data.py`'s `collect_google_ads()`) verbatim -- same `IK_MCP_DIR`
env var, same `<base>/google-ads/.env` path, same `GOOGLE_ADS_*` keys, same
`GoogleAdsClient.load_from_dict()` shape -- so it finds credentials wherever
the anomaly engine finds them, with zero new configuration. It runs its own
GAQL against `search_term_view` (term_type `search_term`) and `keyword_view`
(term_type `keyword`, includes Quality Score); it does **not** call any
google-ads MCP tool.

Segment classification is INTERIM: it inlines
`build_segment_tables.classify_google_campaign()`'s substring rule (Brand /
Discovery / `_nb_`), falling back to `"Other"` for anything unclassified
(PMax, YouTube, etc. -- kept, not dropped). PLAN.md Phase 0 Step 2
(`campaign_registry`) is the permanent replacement; swap the lookup inside
`_segment_for()` once that table exists.

### Usage

```bash
# Yesterday, single day (default)
python3 generation/collect_kw_perf.py

# A specific day
python3 generation/collect_kw_perf.py --date 2026-07-01

# Backfill window: N days ending on --date
python3 generation/collect_kw_perf.py --date 2026-07-01 --days 7

# Test without credentials or the google-ads package: load rows from a fixture
python3 generation/collect_kw_perf.py --from-fixture generation/fixtures/kw_perf_sample.json
```

`DRY_RUN` (default `true`, from `config.py`) governs the write side exactly
as elsewhere in this department -- set `DRY_RUN=false` for a live BigQuery
load. `--from-fixture` only replaces the *read* side (skips the Google Ads
API call entirely); the write still goes through the same guarded
`write_rows()` path.

### Cron intent

Intended to run daily, shortly after the Performance team's ~10 AM IST
anomaly-engine run (per PLAN.md Step 14's scheduling note), pulling
yesterday's data: `python3 collect_kw_perf.py` with no arguments. Backfill
gaps with `--date`/`--days`.

### Fixture testing

`generation/fixtures/kw_perf_sample.json` is a 9-row JSON array (a plain
list, or a `{"rows": [...]}` object also works) of rows already shaped to
`schemas.KwSearchTermPerfRow` -- a mix of `keyword`/`search_term` rows
across the `NB`, `Brand`, and `Discovery` segments, realistic INR values,
and campaign names in the `IK-IN_<Segment>_EdgeUp_..._INDIA` style. Useful
for exercising the write path (`bq_writer.write_rows`) and the smoke-testing
workflow without any Google Ads credentials or the `google-ads` package
installed.

## Step 7: lead-quality signal (CPQL) (`lead_quality.py`)

Read-only pull-forward of the lead-quality/CPQL signal into Phase 1 (PLAN.md
Phase 1 Step 7), writing to `lead_quality_scores` via
`generation/bq_writer.write_rows()`. **India_Leads stays read-only,
non-negotiable** -- this job only issues `SELECT`s; the only write path,
DRY_RUN or live, is the same guarded `write_rows()` every other job in this
directory uses.

### Usage

```bash
# Trailing 30 days ending yesterday (IST), live BQ read (DRY_RUN write, default)
python3 generation/lead_quality.py

# A shorter trailing window
python3 generation/lead_quality.py --period-days 7

# Pin the run date (the period is computed relative to it: period_end =
# run_date - 1 day, period_start = period_end - (period_days - 1))
python3 generation/lead_quality.py --run-date 2026-07-03

# Test without BigQuery credentials or the google-cloud-bigquery package:
# load raw rows from a JSON fixture (skips the read; the write still goes
# through the same guarded write_rows() path)
python3 generation/lead_quality.py --from-fixture generation/fixtures/lead_quality_sample.json
```

`DRY_RUN` (default `true`, from `config.py`) governs the write side exactly
as elsewhere in this department. `--from-fixture` only replaces the *read*
side.

### Discovered source tables and columns (verified by reading code, not
assumed -- see file paths below)

Traced through `/mnt/c/mcp-servers/anomaly-engine/*.py` and
`/mnt/c/mcp-servers/google-sheets/`:

- **`ik-marketing-data.India_Leads.master_view_leads_india`** -- the
  per-lead base table. Fields referenced by the view below: `formatted_date`,
  `UTM_Campaign`, `channel`, `utm_source`, `webinar_attended_date`,
  `DCD_Flag`, `RTE_Flag`, `lead_status`, `sale_date`, `net_revenue`.
- **`ik-marketing-data.India_Leads.v_campaign_funnel`** (view; DDL in
  `/mnt/c/mcp-servers/google-sheets/v_campaign_funnel.sql`) -- pre-aggregates
  the above to `(date, campaign_name=UTM_Campaign, channel, utm_source)`
  with: `iql` = `COUNT(*)` (total leads), `wa` = webinar-attended count,
  `dcd`/`rte` = flag sums, **`junk_unsuitable` = `COUNTIF(lead_status IN
  ('Junk', 'Unsuitable'))`** (the disposition-label columns this job cares
  about), `sales` = `COUNTIF(sale_date IS NOT NULL)`, `revenue` =
  `SUM(net_revenue)`.
- **The Swati Sheet** (Google Sheets id in
  `/mnt/c/mcp-servers/google-sheets/.env`'s `GOOGLE_SHEETS_SHEET_ID`, tab
  `"swati sheet"`) -- the revenue/sale source of truth (not ad-platform
  conversions), read by `collect_data.py`'s `collect_sheets()`. Relevant
  columns (by position, verified against live headers 2026-06-04): Date of
  Sale (A), Deal amount INR (F), Product (J), **Source (BQ)** (T -- same
  channel vocabulary as `v_campaign_funnel.channel`), **Course/Campaign
  (From UTM)** (V, `"CampaignName__CampaignID"`), Campaign ID (AJ). Net
  revenue = gross x (1 - 18% GST).
- **`ik-marketing-data.SIP_pattern_rag.pmis_full_funnel`** (written by the
  anomaly-engine's own `build_dashboard.py`, allowlisted in its
  `bq_writer.py`) -- **this is what `lead_quality.py` actually reads.** One
  row per `(date, campaign)` already joining Google/LinkedIn Ads spend +
  `v_campaign_funnel`'s `iql`/`junk_unsuitable` (renamed `junk`) + the Swati
  Sheet's `sales`/`revenue` (net GST). Columns used here: `date`, `campaign`,
  `channel`, `platform`, `spend`, `iql`, `junk`.
- **`ik-marketing-data.SIP_pattern_rag.pmis_daily_segment_facts`** -- an
  alternative, segment-only (no `campaign_name`) grain of the same
  aggregation; not used here because `lead_quality_scores.campaign_name` is
  a required field and this table doesn't carry one.

**Why `pmis_full_funnel` and not `India_Leads` directly:** both service-account
keys available in this environment were live-tested (`ads-anomaly-bq.json`,
scoped to `India_Leads` per `SETUP_WINDOWS.md`/`SETUP_MACOS.md`, and
`claude-code-dev-bq.json`, the Generation dept's own default credential) and
**both got `403 Access Denied` on `Tech_ML_Projects.lead_history`**, a table
`v_campaign_funnel`/`master_view_leads_india` depend on underneath -- neither
key can query the view or its base table directly in this environment, even
though `ads-anomaly-bq@` nominally has `dataViewer` on `India_Leads`. A live
`SELECT` against `pmis_full_funnel` with `claude-code-dev-bq.json` **did**
succeed (3,760 rows, 219 distinct campaigns, 2026-05-11..2026-07-03 at probe
time) -- it carries the same India_Leads-derived numbers, already joined, and
is reachable with credentials this job actually has. Read-only either way;
this is a credentials/reachability finding, not a design preference.

### Credentials (read path)

This job's read path is independent of the write side's
`GENERATION_BQ_SERVICE_ACCOUNT`/`config.BQ_SERVICE_ACCOUNT` (though it
defaults to the same key): `GENERATION_BQ_READ_SERVICE_ACCOUNT` env var, else
`config.BQ_SERVICE_ACCOUNT`'s path, else a glob fallback for
`/mnt/c/Users/*/.gcp-keys/<same filename>` (this environment is WSL, where
`Path.home()` resolves to the Linux home -- no `.gcp-keys` there -- while the
real key lives under the mounted Windows profile; same class of fix as
`collect_kw_perf.py`'s `_resolve_mcp_base()` WSL fallback for `IK_MCP_DIR`).
Verified present on disk at probe time:
`/mnt/c/Users/Admin/.gcp-keys/claude-code-dev-bq.json`.

### Segment mapping

Reuses `collect_kw_perf.classify_google_campaign()` verbatim (imported, not
duplicated) for Google campaign names (`Brand`/`Discovery`/`_nb_` substring
rules, `"Other"` fallback). One addition beyond `collect_kw_perf.py`: this
job also sees LinkedIn rows (via `pmis_full_funnel`), which a classifier
named for Google campaign names cannot classify by name -- `_segment_for()`
routes on `channel`/`platform == "LinkedIn"` first (mirroring how
`build_segment_tables.py` itself keys LinkedIn off channel, not a name
substring), then falls through to the Google classifier. PLAN.md Phase 0
Step 2 (`campaign_registry`) is the permanent replacement.

### Derivation

Per `(campaign_name, segment)`, summed over the period: `leads` = sum of
`iql`; `junk_leads` = sum of `junk` (clamped to `<= leads`); `quality_leads`
= `leads - junk_leads` (i.e. **not** Junk/Unsuitable -- see "VERIFY WITH DATA
TEAM" below for what this does and doesn't mean); `quality_ratio` =
`quality_leads / leads` (`None` if `leads == 0`); `cpl_inr` = `spend_inr /
leads` (`None` if `leads == 0`); `cpql_inr` = `spend_inr / quality_leads`
(`None` if `quality_leads == 0` -- **null, never `inf`/an error**).

### VERIFY WITH DATA TEAM -- semantic assumptions this job makes

1. **"Quality" = not Junk/Unsuitable, not "became a Sale."** `quality_leads`
   is defined as `leads - junk_leads` (same-period, same-cohort, immediately
   available from `v_campaign_funnel`/`pmis_full_funnel`). It is **not**
   `sales` (from the Swati Sheet) -- period-level sale *counts* would mix
   cohorts (sales close weeks/months after the lead that produced them was
   captured, so "sales in June" and "leads in June" are largely different
   people), and `schemas.LeadQualityScoreRow` has no revenue field to carry
   a separate Sale-based signal anyway. If the org wants CPQL defined on
   verified paying customers rather than non-junk leads, that needs either a
   cohort-level (lead-capture-date-to-eventual-sale) join, out of scope for
   this read-only pull-forward, or an explicit sign-off that a lagging,
   cross-cohort `sales`-count proxy is acceptable at this campaign x period
   grain.
2. **The exact set of "junk" disposition labels is not independently
   verified.** `v_campaign_funnel`'s DDL hardcodes `lead_status IN ('Junk',
   'Unsuitable')`; this job trusts that definition (via `pmis_full_funnel`'s
   `junk` column) rather than re-deriving it, because **neither available
   service-account key could query `master_view_leads_india`'s `lead_status`
   column directly** (see "Discovered source tables" above -- both hit `403`
   on `Tech_ML_Projects.lead_history`). Confirm with the data team whether
   `Junk`/`Unsuitable` are the only disposition values that should be
   excluded from "quality" (e.g., is there a `Duplicate` or `Invalid Number`
   status not covered here?).
3. **Segment mapping is the same interim substring classifier as
   `collect_kw_perf.py`**, plus a LinkedIn channel/platform shortcut (see
   above) -- both are stopgaps for `campaign_registry` (Step 2, not yet
   backfilled per `PLAN.md`'s status log). Any campaign name that doesn't
   contain `brand`/`discovery`/`_nb_` and isn't tagged LinkedIn lands in
   `"Other"`.
4. **CRITICAL -- confirmed via a live read, not a guess: `pmis_full_funnel`'s
   `campaign` join between ad-spend rows and lead rows is broken for (at
   least) the current data.** A live query over the trailing 30 days
   (2026-06-04..2026-07-03) found **0 of 148** aggregated campaign rows with
   both `spend > 0` and `iql > 0` simultaneously (42 rows have spend with
   zero leads; 106 have leads with zero spend). Example:
   `IK-IN_Google_NB_EdgeUp_AgenticAI_35-54` (Ads-collector-sourced, spend
   ~2.4L over the period, 0 leads) vs.
   `IK-IN_Google_NB_EdgeUp_AgenticAI_35-54__23333103542` (BQ-view-sourced,
   the same real campaign, 64 leads, **0 spend**) -- the trailing
   `__<platform_campaign_id>` present on the UTM/BQ side is absent on the raw
   Ads-API side, so `build_dashboard.py`'s `cell()` keys them as two
   different campaigns and spend/leads never merge into one row. This is
   precisely the attribution-spine brittleness `PLAN.md` already flags
   (Step 7 explicitly depends on Step 2's `campaign_registry`, not yet
   backfilled) -- **`cpl_inr`/`cpql_inr` from the live path will be `0.0` or
   `None` for essentially every real campaign until that join is fixed
   upstream (in `pmis_full_funnel`/`build_dashboard.py`) or `lead_quality.py`
   is re-pointed at `campaign_registry`-resolved identifiers.** The
   aggregation/derivation logic itself is verified correct (see
   `tests_lead_quality.py`, which exercises the case where spend and leads
   *do* share one row, as they will once the join is fixed) -- this is a
   live-data/upstream-join gap, not a bug in this job.
5. **`pmis_daily_segment_facts`/`pmis_full_funnel` are the anomaly-engine's
   own tables, outside this department's write allowlist.** This job only
   reads them (`GENERATION_PMIS_SOURCE_DATASET`/`GENERATION_PMIS_FULL_FUNNEL_
   TABLE` env vars, both defaulting to the real names) -- confirm with the
   data team that Generation reading (never writing) another department's
   `pmis_*` tables is an acceptable interim dependency, pending the
   dedicated `Creative_Engine` dataset and/or `campaign_registry`.
