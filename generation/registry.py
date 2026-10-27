#!/usr/bin/env python3
"""
Campaign registry lookup/validation helper -- PLAN.md Phase 0 / Step 2
(attribution spine).

This is the module the rest of the Generation pipeline imports to answer
"what segment/program/platform does this campaign_name belong to?" instead
of re-deriving it from free-text substring matching (the brittle pattern
documented in docs/campaign-naming-convention.md Section 1). The registry
itself is backfilled by build_campaign_registry.py into the `campaign_registry`
BQ table (schemas.CampaignRegistryRow); this module only *reads* it.

Enforcement (docs/campaign-naming-convention.md Section 5, ANSWERS #5 --
PM decision 2026-07-04, WARN-FIRST):
    REGISTRY_ENFORCEMENT=warn   (default) -- unregistered campaign names are
        processed but degraded to a synthetic "Other/unknown" entry
        (`registered=False`), a warning is logged, and the name is counted
        in the module-level unregistered report (see `unregistered_report()`)
        for the dashboard flag + Slack digest (PLAN.md Step 12).
    REGISTRY_ENFORCEMENT=block -- an unregistered name raises
        `UnregisteredCampaignError` instead. Flip via env var only, no code
        change, once the perf team confirms 2-4 clean weekly cycles.

Name normalization (`normalize_name()`): campaign names surfacing from the
lead-side of the pipeline (e.g. `pmis_full_funnel`'s `campaign` column, per
lead_quality.py's discovery notes and its own fixtures/lead_quality_sample.json,
e.g. "IK-IN_Brand__333333333") carry a trailing `__<digits>` UTM-id suffix
that the live platform campaign name itself does NOT have (see
docs/campaign-naming-convention.md Section 1's "Campaign ID from UTM" row --
`utm_campaign.rsplit("__", 1)[-1]`). `resolve()` strips that suffix before
falling back to a normalized lookup, so a lead-side campaign string still
resolves against the registry row keyed on the clean live campaign name.

Existing campaigns are NOT renamed (migration policy, Section 4) -- every
registry row's `campaign_name` is byte-identical to the live platform name,
so normalization here is deliberately minimal (whitespace + suffix strip
only, no case-folding).
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import pydantic

import bq_writer
import config
import schemas

log = logging.getLogger("generation.registry")

_UTM_SUFFIX_RE = re.compile(r"__\d+$")

# The DRY_RUN preview file bq_writer.write_rows("campaign_registry", ...)
# produces -- also read here as the local-snapshot fallback so
# `build_campaign_registry.py --from-google` (DRY_RUN default) immediately
# makes its output available to `check()`/`resolve()` without a live BQ read.
_LOCAL_SNAPSHOT_PATH = config.STAGING_DIR / "bq_preview_campaign_registry.json"


class UnregisteredCampaignError(RuntimeError):
    """Raised by check() when REGISTRY_ENFORCEMENT=block and the campaign
    name does not resolve against the loaded registry."""


@dataclass(frozen=True)
class RegistryEntry:
    """A resolved (or, when unregistered, degraded) campaign_registry row.

    Mirrors schemas.CampaignRegistryRow's fields EXCEPT `registered_at` and
    `snapshot_date` -- both are write-time/snapshot bookkeeping (when this
    row was backfilled, which daily snapshot it belongs to) that a lookup
    caller has no use for and that would need re-deriving for a degraded
    (unregistered) entry anyway, so they are deliberately not carried
    through here. Plus `registered` (not a BQ column -- purely a lookup-layer
    flag distinguishing a real backfilled row from check()'s warn-mode
    degraded fallback)."""
    campaign_name: str
    platform: Optional[str] = None
    segment: str = "Other"
    program: Optional[str] = None
    geo: Optional[str] = None
    funnel_stage: Optional[str] = None
    utm_campaign: Optional[str] = None
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    platform_campaign_id: Optional[str] = None
    status: Optional[str] = None
    registered: bool = True

    def as_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------
def normalize_name(campaign_name: str) -> str:
    """Strip whitespace and a trailing `__<digits>` UTM-id suffix.

    `"IK-IN_Brand__333333333"` -> `"IK-IN_Brand"`. A name with no such
    suffix passes through with only whitespace stripped."""
    n = (campaign_name or "").strip()
    return _UTM_SUFFIX_RE.sub("", n)


# ---------------------------------------------------------------------------
# Loading (live BQ, explicit fixture, or local DRY_RUN-preview fallback)
# ---------------------------------------------------------------------------
_registry_cache: Optional[dict] = None


# The exact set of fields schemas.CampaignRegistryRow declares -- used to
# strip any read-layer-only column (e.g. something a future live BQ SELECT *
# carries that isn't part of the write-time contract) before validating,
# since _Row (schemas.py) sets extra="forbid" and would otherwise raise on a
# harmless extra key rather than only on a genuinely corrupted row.
_REGISTRY_ROW_FIELDS = set(schemas.CampaignRegistryRow.model_fields)


def _entry_from_dict(d: dict) -> RegistryEntry:
    """Validate `d` through schemas.CampaignRegistryRow before trusting it.

    A campaign_registry snapshot (local JSON file or a live BQ read) is
    downstream of the same guarded writer that validates on the way IN
    (bq_writer.write_rows), but nothing previously re-checked it on the way
    OUT -- a hand-edited fixture, a manually patched BQ row, or a
    schema-drifted write path could all produce a row this lookup layer
    would silently trust (e.g. a bad `platform` value, per fix #4's
    fail-closed policy for _map_status). Raising here fails the whole load
    fast instead of quietly building a RegistryEntry from corrupted data.
    """
    filtered = {k: v for k, v in d.items() if k in _REGISTRY_ROW_FIELDS}
    try:
        schemas.CampaignRegistryRow(**filtered)
    except pydantic.ValidationError as e:
        raise ValueError(
            f"Corrupted campaign_registry row for campaign_name="
            f"{d.get('campaign_name')!r}: fails schemas.CampaignRegistryRow "
            f"validation -- {e}"
        ) from e

    return RegistryEntry(
        campaign_name=d["campaign_name"],
        platform=d.get("platform"),
        segment=d.get("segment") or "Other",
        program=d.get("program"),
        geo=d.get("geo"),
        funnel_stage=d.get("funnel_stage"),
        utm_campaign=d.get("utm_campaign"),
        utm_source=d.get("utm_source"),
        utm_medium=d.get("utm_medium"),
        platform_campaign_id=d.get("platform_campaign_id"),
        status=d.get("status"),
        registered=True,
    )


def _rows_to_registry(rows: list[dict]) -> dict:
    reg = {}
    for row in rows:
        name = row.get("campaign_name")
        if not name:
            continue
        reg[name] = _entry_from_dict(row)
    return reg


def _load_snapshot_file(path: Path) -> dict:
    import json
    obj = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(obj, dict) and "rows" in obj:
        rows = obj["rows"]
    elif isinstance(obj, list):
        rows = obj
    else:
        raise ValueError(f"Registry snapshot {path} must be a JSON list of rows, "
                          f"or an object with a 'rows' list")
    return _rows_to_registry(rows)


def _load_from_bq() -> dict:
    """Live SELECT of the LATEST snapshot only (rows WHERE snapshot_date =
    MAX(snapshot_date)). Raises on any failure -- the caller (load_registry)
    decides whether to fall back to the local snapshot file.

    Changed 2026-10-27 from "latest row per campaign_name across ALL
    snapshots": with weekly refreshes, that kept a renamed campaign's OLD
    name alive forever as a ghost entry frozen at its July status (3 Google
    renames exist today), and it disagreed with the dashboard, which reads
    MAX(snapshot_date) only (ce-queries.ts). Latest-only is safe because
    build_campaign_registry.py only ever writes COMPLETE snapshots live:
    --refresh carries every prior key forward (a shrink guard refusing
    anything smaller), and a live backfill (no --refresh) refuses to write
    once the table has any row unless --force-backfill is passed -- so a
    one-platform backfill can't silently become the latest snapshot
    (tests_registry.py c10). Duplicate campaign_names inside one snapshot
    (3 LinkedIn names have 2 ids each) still collapse last-wins in
    _rows_to_registry, as before."""
    table_id = bq_writer.validate_target("campaign_registry")
    client = bq_writer.get_client()          # lazy bigquery import inside bq_writer
    query = f"""
        SELECT * FROM `{table_id}`
        WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM `{table_id}`)
    """
    result = client.query(query).result(timeout=120)
    rows = [dict(r.items()) for r in result]
    return _rows_to_registry(rows)


def _load_local_fallback() -> dict:
    if _LOCAL_SNAPSHOT_PATH.exists():
        try:
            reg = _load_snapshot_file(_LOCAL_SNAPSHOT_PATH)
            log.info("Loaded %d campaign_registry row(s) from local snapshot %s",
                      len(reg), _LOCAL_SNAPSHOT_PATH)
            return reg
        except Exception as e:
            log.warning("Failed to parse local registry snapshot %s: %s",
                        _LOCAL_SNAPSHOT_PATH, e)
    log.warning("No live registry and no local snapshot at %s -- registry is "
                "empty (every campaign will resolve as unregistered).",
                _LOCAL_SNAPSHOT_PATH)
    return {}


def load_registry(path: str | Path | None = None, force_reload: bool = False) -> dict:
    """Return {campaign_name: RegistryEntry}, caching the result.

    - `path` given: load exactly that JSON snapshot (bare list of row dicts,
      or the bq_writer preview wrapper {"rows": [...]}), unconditionally
      replacing the cache -- used by tests and any explicit-fixture caller.
    - `path` omitted: return the cached registry unless `force_reload`.
      When (re)loading: if config.DRY_RUN is False, try a live BQ read of
      the latest snapshot first (`_load_from_bq`); any failure (missing credentials, package, network,
      table not yet created) logs a warning and falls back to the local
      DRY_RUN-preview snapshot. If config.DRY_RUN is True (the default),
      skip the live read entirely and go straight to the local snapshot --
      this is what lets `check()`/`resolve()` work in tests and local dev
      with zero BigQuery credentials.
    """
    global _registry_cache
    if path is not None:
        _registry_cache = _load_snapshot_file(Path(path))
        return _registry_cache

    if _registry_cache is not None and not force_reload:
        return _registry_cache

    reg = None
    if not config.DRY_RUN:
        try:
            reg = _load_from_bq()
            log.info("Loaded %d campaign_registry row(s) live from BigQuery", len(reg))
        except Exception as e:
            log.warning("Live campaign_registry read failed (%s) -- falling back "
                        "to local snapshot", e)
    if reg is None:
        reg = _load_local_fallback()

    _registry_cache = reg
    return _registry_cache


def clear_cache() -> None:
    """Force the next load_registry() call (with no explicit path) to reload."""
    global _registry_cache
    _registry_cache = None


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------
def resolve(campaign_name: str) -> Optional[RegistryEntry]:
    """Exact match first, then normalized (suffix-stripped) match. None if
    neither is present in the loaded registry."""
    reg = load_registry()
    if not campaign_name:
        return None
    hit = reg.get(campaign_name)
    if hit is not None:
        return hit
    normalized = normalize_name(campaign_name)
    if normalized != campaign_name:
        return reg.get(normalized)
    return None


# ---------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------
# Distinct invalid REGISTRY_ENFORCEMENT values already warned about, so a
# bad value set once at process start doesn't re-log on every single
# check()/resolve() call (enforcement_mode() is read fresh, uncached, on
# every call by design -- see docstring below).
_warned_invalid_enforcement_values: set[str] = set()


def enforcement_mode() -> str:
    """Read REGISTRY_ENFORCEMENT fresh on every call (not cached at import
    time) so tests/ops can flip it via os.environ without reloading this
    module. Defaults to 'warn'; any value other than 'warn'/'block' also
    degrades to 'warn' (fail open, not closed) -- logged once per distinct
    invalid value, not once per call, so a persistently-bad env var doesn't
    spam the log on every campaign check()."""
    mode = os.environ.get("REGISTRY_ENFORCEMENT", "warn").strip().lower()
    if mode not in ("warn", "block"):
        if mode not in _warned_invalid_enforcement_values:
            log.warning("Invalid REGISTRY_ENFORCEMENT=%r (expected 'warn' or 'block') "
                        "-- defaulting to 'warn'", mode)
            _warned_invalid_enforcement_values.add(mode)
        return "warn"
    return mode


_unregistered_counts: dict = {}


def _record_unregistered(campaign_name: str) -> None:
    _unregistered_counts[campaign_name] = _unregistered_counts.get(campaign_name, 0) + 1


def unregistered_report() -> dict:
    """Copy of {campaign_name: times_seen_unregistered} accumulated by
    check() since process start (or the last reset_unregistered_report()).
    Feeds the dashboard "N unregistered campaigns, attribution degraded"
    flag and the Slack digest (PLAN.md Step 12)."""
    return dict(_unregistered_counts)


def reset_unregistered_report() -> None:
    _unregistered_counts.clear()


def check(campaign_name: str) -> RegistryEntry:
    """The gate future generators/exports call before targeting a campaign.

    Returns the resolved RegistryEntry if registered. If not registered:
    REGISTRY_ENFORCEMENT=block raises UnregisteredCampaignError;
    REGISTRY_ENFORCEMENT=warn (default) logs a warning, records the name in
    the unregistered report, and returns a degraded RegistryEntry
    (registered=False, segment="Other", program="other") so callers can keep
    processing (with attribution flagged as degraded) rather than crash.
    """
    entry = resolve(campaign_name)
    if entry is not None:
        return entry

    _record_unregistered(campaign_name)
    mode = enforcement_mode()
    if mode == "block":
        raise UnregisteredCampaignError(
            f"Campaign {campaign_name!r} is not registered in campaign_registry "
            f"(REGISTRY_ENFORCEMENT=block). Register it via "
            f"build_campaign_registry.py before targeting it."
        )

    log.warning(
        "Unregistered campaign %r (REGISTRY_ENFORCEMENT=warn) -- degraded to "
        "Other/unknown; attribution is degraded for this campaign until it is "
        "backfilled into campaign_registry.", campaign_name)
    return RegistryEntry(
        campaign_name=campaign_name,
        platform=None,
        segment="Other",
        program="other",
        geo="in",
        funnel_stage=None,
        utm_campaign=None,
        utm_source=None,
        utm_medium=None,
        platform_campaign_id=None,
        status=None,
        registered=False,
    )
