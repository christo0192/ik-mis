#!/usr/bin/env python3
"""
Guarded BigQuery write module for the Generation department.

Cloned from /mnt/c/mcp-servers/anomaly-engine/bq_writer.py (PLAN.md Phase 0
Step 1: "clone the guarded writer whose allowlist = the new tables only").
Same three guarantees, adapted to config.py + schemas.REGISTRY:

1. Dataset + table allowlist -- every write asserts the target is
   `config.BQ_PROJECT.config.TARGET_DATASET` AND a `config.TABLE_PREFIX`-
   prefixed table whose logical (unprefixed) name is one of the 12 tables in
   `schemas.REGISTRY`. Any other target -- India_Leads and Market_Research
   above all -- raises WriteTargetError before any API call. Non-negotiable.
2. DRY_RUN (default "true") -- writes a local staging/bq_preview_<table>.json
   instead of BigQuery.
3. Idempotent + atomic partition replace -- each affected partition (keyed by
   the table's PARTITION_FIELDS column) is loaded via the partition decorator
   (`table$YYYYMMDD`) with WRITE_TRUNCATE. Atomic per partition (no
   DELETE-then-load gap), idempotent by construction, zero query-string SQL.
   Load jobs (not streaming inserts) on purpose: streaming-buffer rows cannot
   be replaced, which would break re-runs.

Schema validation is the fourth guarantee this module adds on top of the
anomaly-engine pattern: write_rows() constructs each row through its table's
Pydantic model (schemas.REGISTRY) before it is ever serialized -- an invalid
row raises pydantic.ValidationError and nothing is written, in DRY_RUN or
live.

`google.cloud.bigquery` is imported lazily, only inside the live-write path
(get_client(), _bq_schema(), the load-job call in write_partitions) -- so
DRY_RUN runs (and the whole test suite) work without the package installed.

Interim host dataset: see config.py's DATASET_MODE / README.md "Flipping to
Creative_Engine" -- re-homing later is one env var, no code change here.
"""

import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import config
import schemas

IST = timezone(timedelta(hours=5, minutes=30), "IST")  # no DST; avoids tzdata dep on Windows

log = logging.getLogger("generation.bq_writer")


class WriteTargetError(RuntimeError):
    """Raised when a write targets anything outside the allowed dataset/table."""


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------
def _parse_target(table: str) -> tuple[str, str, str]:
    """Parse `table` into (project, dataset, logical_name), or raise.

    Accepts a bare logical table name (e.g. "keyword_candidates", the
    schemas.REGISTRY key) or a fully qualified "project.dataset.table"
    reference. The ONLY writable location is
    config.BQ_PROJECT.config.TARGET_DATASET, and the table's logical name
    (after stripping config.TABLE_PREFIX) must be one of the 12 names in
    schemas.REGISTRY. Anything else -- including "India_Leads.leads" or
    "Market_Research.social_trends" -- raises before any API call.

    Shared by validate_target() and write_rows() so both agree on how a
    fully-qualified name maps back to its schemas.REGISTRY key -- previously
    write_rows() indexed REGISTRY with the raw (possibly fully-qualified)
    argument, which raised KeyError for any caller passing the FQN form that
    validate_target() itself accepts.
    """
    parts = table.replace("`", "").split(".")
    if len(parts) == 1:
        project, dataset, name = config.BQ_PROJECT, config.TARGET_DATASET, parts[0]
    elif len(parts) == 3:
        project, dataset, name = parts
    else:
        raise WriteTargetError(f"Unparseable table reference: {table!r}")

    if project != config.BQ_PROJECT or dataset != config.TARGET_DATASET:
        raise WriteTargetError(
            f"REFUSED: write target {project}.{dataset}.{name} is outside the "
            f"allowed dataset {config.BQ_PROJECT}.{config.TARGET_DATASET}")

    prefix = config.TABLE_PREFIX
    logical = name[len(prefix):] if prefix and name.startswith(prefix) else name
    if logical not in schemas.REGISTRY:
        raise WriteTargetError(
            f"REFUSED: unknown table {name!r} (not one of the 12 allowlisted "
            f"Generation tables)")

    return project, dataset, logical


def validate_target(table: str) -> str:
    """Return the fully-qualified allowlisted table id, or raise.

    See _parse_target() for the acceptance rules (bare logical name or
    fully-qualified "project.dataset.table").
    """
    project, dataset, logical = _parse_target(table)
    physical_name = f"{config.TABLE_PREFIX}{logical}"
    return f"{project}.{dataset}.{physical_name}"


# ---------------------------------------------------------------------------
# Client (live-write path only -- lazy google-cloud-bigquery import)
# ---------------------------------------------------------------------------
_client = None


def _credentials_path() -> str:
    path = config.BQ_SERVICE_ACCOUNT
    if not Path(path).exists():
        raise FileNotFoundError(f"Generation BQ writer key not found: {path}")
    return path


def get_client():
    global _client
    if _client is None:
        from google.cloud import bigquery          # lazy: only needed for live writes
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(
            _credentials_path(),
            scopes=["https://www.googleapis.com/auth/cloud-platform"])
        _client = bigquery.Client(project=config.BQ_PROJECT, credentials=creds,
                                  location=config.BQ_LOCATION)
    return _client


def _bq_schema(bq_schema_tuples):
    from google.cloud import bigquery               # lazy: only needed for live writes
    fields = []
    for name, ftype, mode in bq_schema_tuples:
        fields.append(bigquery.SchemaField(name, ftype, mode=mode))
    return fields


# ---------------------------------------------------------------------------
# Pydantic row -> BQ JSON value
# ---------------------------------------------------------------------------
def _model_to_bq(model, bq_schema_tuples) -> dict:
    """Serialize a validated Pydantic row to BQ-load-ready JSON (dates/
    datetimes -> ISO strings; everything else passes through)."""
    dumped = model.model_dump()
    rec = {}
    for name, _ftype, _mode in bq_schema_tuples:
        v = dumped.get(name)
        if isinstance(v, datetime):
            rec[name] = v.isoformat()
        elif isinstance(v, date):
            rec[name] = v.isoformat()
        else:
            rec[name] = v
    return rec


# ---------------------------------------------------------------------------
# Core idempotent write
# ---------------------------------------------------------------------------
def _write_partitions(table_id: str, table_name: str, part_field: str,
                       records: list[dict]) -> int:
    """Idempotently + atomically replace the partition(s) covered by `records`.

    Each partition is loaded via its decorator (`table$YYYYMMDD`) with
    WRITE_TRUNCATE -- atomic per partition, no DML, no SQL string built from
    data. Partition values are validated as real ISO dates (raises
    otherwise). Returns the number of rows written (0 in DRY_RUN, which
    writes a preview file instead).
    """
    dropped = sum(1 for r in records if not r.get(part_field))
    if dropped:
        log.warning("%s: dropping %d row(s) with NULL %s (would fail NOT NULL load)",
                    table_name, dropped, part_field)
        records = [r for r in records if r.get(part_field)]

    # date.fromisoformat raises on anything that isn't a real date -- the only
    # place partition values are used is the decorator, never a SQL string.
    by_date = defaultdict(list)
    for r in records:
        by_date[date.fromisoformat(str(r[part_field])[:10])].append(r)
    dates = sorted(by_date)
    if not dates:
        log.warning("%s: no rows with a %s value -- nothing to write", table_name, part_field)
        return 0

    if config.DRY_RUN:
        config.STAGING_DIR.mkdir(exist_ok=True)
        preview = config.STAGING_DIR / f"bq_preview_{table_name}.json"
        preview.write_text(json.dumps(
            {"table": table_id, "partition_field": part_field,
             "partitions": [str(d) for d in dates],
             "row_count": len(records), "rows": records},
            indent=2, default=str), encoding="utf-8")
        log.info("DRY_RUN: %s -> %s (%d rows, %d partitions)",
                 table_name, preview.name, len(records), len(dates))
        return 0

    from google.cloud import bigquery               # lazy: only needed for live writes
    _, bq_schema_tuples = schemas.REGISTRY[table_name]
    client = get_client()
    job_config = bigquery.LoadJobConfig(
        schema=_bq_schema(bq_schema_tuples),
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE)
    # Submit all partition loads (jobs run concurrently server-side), then
    # await each individually -- a mid-sequence failure must not leave earlier,
    # already-replaced partitions unaccounted for. Collect success/exception
    # per date and only raise once every job has been awaited, so the error
    # enumerates exactly which partitions are now replaced and which failed.
    jobs = {
        d: client.load_table_from_json(
            by_date[d], f"{table_id}${d.strftime('%Y%m%d')}", job_config=job_config)
        for d in dates
    }
    succeeded = []
    failed = []
    for d, job in jobs.items():
        try:
            job.result()
            succeeded.append(d)
        except Exception as e:
            failed.append((d, e))

    if failed:
        succeeded_str = ", ".join(str(d) for d in succeeded) or "none"
        failed_str = "; ".join(f"{d}: {e}" for d, e in failed)
        msg = (f"{table_name}: partition load failed for {len(failed)} of "
               f"{len(dates)} partition(s) -- succeeded: [{succeeded_str}]; "
               f"failed: [{failed_str}]")
        log.error(msg)
        raise RuntimeError(msg)

    log.info("%s: atomically replaced %d partition(s), %d rows",
             table_name, len(dates), len(records))
    return len(records)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def write_rows(table_name: str, rows: list[dict]) -> int:
    """Validate `rows` against the table's Pydantic model, then atomically
    replace the covered partitions.

    This is the single gate for every Generation-department write:
    destination allowlist (validate_target, checked first -- fails fast
    before looking at row content) + schema validation (Pydantic, via
    schemas.REGISTRY) both happen here before any BigQuery API call or
    DRY_RUN preview write.

    `table_name` is either the logical (unprefixed) name, e.g.
    "keyword_candidates", or a fully-qualified "project.dataset.table"
    reference (e.g. "ik-marketing-data.SIP_pattern_rag.ce_keyword_candidates")
    -- both resolve to the same schemas.REGISTRY entry via _parse_target().
    Raises WriteTargetError for an unregistered/out-of-allowlist target, or
    pydantic.ValidationError for a row that fails its model's contract.
    """
    project, dataset, logical = _parse_target(table_name)
    table_id = f"{project}.{dataset}.{config.TABLE_PREFIX}{logical}"
    model_cls, bq_schema_tuples = schemas.REGISTRY[logical]
    part_field = schemas.PARTITION_FIELDS[logical]

    validated = [model_cls(**row) for row in rows]      # raises pydantic.ValidationError
    records = [_model_to_bq(row, bq_schema_tuples) for row in validated]
    return _write_partitions(table_id, logical, part_field, records)


def run_date_today_ist() -> str:
    """IST, not container-local: a manual retry between 00:00-05:30 IST must
    not label the run with the prior date (Fly containers run UTC)."""
    return str(datetime.now(IST).date())
