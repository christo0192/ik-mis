"""
Environment-driven configuration for the Generation department's guarded
BigQuery writer (`bq_writer.py`) and schemas (`schemas.py`).

Interim home (Phase 0 / Step 1 of PLAN.md, mirrors the anomaly-engine's
`pmis_` decision): the 12 Generation tables live as `ce_`-prefixed tables in
the already-writable `SIP_pattern_rag` dataset, because no service account in
this project may create datasets. When the dedicated `Creative_Engine`
dataset is provisioned by the data team, flip `GENERATION_DATASET_MODE=future`
-- one env var -- and every write re-points with no code changes (table
names lose the `ce_` prefix in the new dataset). See README.md.

No machine-specific absolute paths are hardcoded here: everything is an env
var with a relative/home-relative default, so this runs unmodified on a
laptop, in CI, or on a Fly machine.
"""
import os
from pathlib import Path

# ---- BigQuery target -------------------------------------------------------
BQ_PROJECT = os.environ.get("BQ_PROJECT", "ik-marketing-data")
BQ_LOCATION = os.environ.get("GENERATION_BQ_LOCATION", "US")

# Dedicated dataset the data team will eventually provision (see PLAN.md
# Phase 0 Step 1). Not used unless GENERATION_DATASET_MODE=future.
FUTURE_DATASET = os.environ.get("FUTURE_DATASET", "Creative_Engine")

# "interim" (default) = ce_-prefixed tables in SIP_pattern_rag.
# "future"  = dedicated Creative_Engine dataset, no prefix.
# This is the single env var that flips the whole module once the dedicated
# dataset is approved -- no other code change needed.
DATASET_MODE = os.environ.get("GENERATION_DATASET_MODE", "interim").strip().lower()

if DATASET_MODE == "future":
    TARGET_DATASET = os.environ.get("TARGET_DATASET", FUTURE_DATASET)
    TABLE_PREFIX = os.environ.get("TABLE_PREFIX", "")
elif DATASET_MODE == "interim":
    TARGET_DATASET = os.environ.get("TARGET_DATASET", "SIP_pattern_rag")
    TABLE_PREFIX = os.environ.get("TABLE_PREFIX", "ce_")
else:
    raise ValueError(
        f"GENERATION_DATASET_MODE must be 'interim' or 'future', got {DATASET_MODE!r}")

# ---- Writer behavior --------------------------------------------------------
# Matches the anomaly-engine's DRY_RUN default (opt-in live writes only).
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() != "false"

# Service-account key for live (non-DRY_RUN) writes. No machine-specific
# default path baked in -- resolves relative to the current user's home dir
# at runtime, same pattern as bq_writer.py's PMIS_BQ_SERVICE_ACCOUNT.
BQ_SERVICE_ACCOUNT = os.environ.get(
    "GENERATION_BQ_SERVICE_ACCOUNT",
    str(Path.home() / ".gcp-keys" / "claude-code-dev-bq.json"),
)

# DRY_RUN preview files land here (created on first write).
STAGING_DIR = Path(__file__).parent / "staging"
