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
