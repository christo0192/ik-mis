#!/usr/bin/env python3
"""
Tests for the campaign registry (attribution spine) -- PLAN.md Phase 0 / Step 2.

Run: `python3 generation/tests_registry.py` (from the MIS repo root, or
anywhere -- imports resolve relative to this file's directory). Must pass
with DRY_RUN=true (the default) and without google-cloud-bigquery/google-ads
importable -- registry.py and build_campaign_registry.py only import those
lazily, inside the live-pull/live-read paths, never at module load.

Asserts:
  (a) normalize_name() strips a trailing `__<digits>` UTM suffix and leaves
      a clean name unchanged (only whitespace-stripped).
  (b) load_registry(path=...) loads fixtures/registry_sample.json; resolve()
      exact-matches a clean registered name and normalized-matches the same
      entry via its `__<digits>`-suffixed lead-side variant; a name absent
      from the registry resolves to None.
  (c) check() in warn mode (REGISTRY_ENFORCEMENT unset/"warn", the default)
      on an unregistered name returns a degraded entry (registered=False,
      segment="Other", program="other") without raising, and records the
      name in unregistered_report().
  (d) check() in block mode (REGISTRY_ENFORCEMENT=block) raises
      UnregisteredCampaignError for the same unregistered name, and does NOT
      raise for a registered name.
  (e) build_campaign_registry.classify_campaign()/build_registry_rows() on
      fixtures/registry_campaigns_raw.json (mixed clean/legacy/unclassifiable
      Google + LinkedIn names) produce the expected segment/program/audience/
      needs_attention per campaign, and every row's "registry_row" validates
      against schemas.CampaignRegistryRow.
  (f) a full `--from-fixture` CLI run (build_campaign_registry.main()) exits
      0, writes the expected DRY_RUN preview + markdown backfill report, and
      the resulting local snapshot is then readable by
      registry.load_registry() (no explicit path) so resolve() finds a
      campaign that was just backfilled.
"""
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

import config
import schemas
import registry
import build_campaign_registry

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
REGISTRY_SAMPLE = str(FIXTURES_DIR / "registry_sample.json")
RAW_CAMPAIGNS_FIXTURE = str(FIXTURES_DIR / "registry_campaigns_raw.json")
SNAPSHOT_DATE = date(2026, 7, 4)


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"PASS: {msg}")


def main():
    check(config.DRY_RUN, "DRY_RUN defaults true")

    # --- (a) normalize_name --------------------------------------------------
    check(registry.normalize_name("IK-IN_Brand__333333333") == "IK-IN_Brand",
          "normalize_name strips trailing __<digits> suffix")
    check(registry.normalize_name("IK-IN_Brand") == "IK-IN_Brand",
          "normalize_name leaves a clean name unchanged")
    check(registry.normalize_name("  IK-IN_Brand  ") == "IK-IN_Brand",
          "normalize_name strips surrounding whitespace")
    check(registry.normalize_name("IK-IN_Weird__notdigits") == "IK-IN_Weird__notdigits",
          "normalize_name does not strip a non-numeric __ suffix")

    # --- (b) load_registry(path=...) + resolve() ------------------------------
    reg = registry.load_registry(path=REGISTRY_SAMPLE)
    check(len(reg) == 5, f"registry_sample.json loads 5 entries (got {len(reg)})")

    exact = registry.resolve("IK-IN_Brand")
    check(exact is not None and exact.registered is True,
          "resolve() exact-matches a clean registered name")
    check(exact.segment == "Brand" and exact.platform == "google",
          f"resolved entry carries segment/platform from the fixture row "
          f"(got segment={exact.segment!r}, platform={exact.platform!r})")

    suffixed = registry.resolve("IK-IN_Brand__333333333")
    check(suffixed is not None and suffixed.registered is True,
          "resolve() normalized-matches a __<digits>-suffixed lead-side variant")
    check(suffixed.campaign_name == exact.campaign_name,
          "suffixed lookup resolves to the same registry row as the clean lookup")

    miss = registry.resolve("IK-IN_Completely_Unknown_Campaign")
    check(miss is None, "resolve() returns None for a name absent from the registry")

    # --- (b2) corrupted snapshot rows fail fast (schema-validate on load) ------
    bad_snapshot = [{
        "campaign_name": "IK-IN_Corrupt_Platform",
        "platform": "myspace",           # not a valid schemas.CampaignRegistryRow Literal
        "segment": "Other",
        "snapshot_date": "2026-07-04",
    }]
    bad_path = FIXTURES_DIR.parent / "staging" / "test_bad_registry_snapshot.json"
    bad_path.parent.mkdir(parents=True, exist_ok=True)
    bad_path.write_text(json.dumps(bad_snapshot), encoding="utf-8")
    try:
        raised_bad_platform = False
        try:
            registry.load_registry(path=str(bad_path))
        except ValueError:
            raised_bad_platform = True
        check(raised_bad_platform,
              "load_registry(path=...) fails fast on a row with an invalid "
              "platform value (schemas.CampaignRegistryRow validation)")

        missing_field_snapshot = [{
            "campaign_name": "IK-IN_Missing_SnapshotDate",
            "platform": "google",
            "segment": "Other",
            # snapshot_date omitted -- required by schemas.CampaignRegistryRow
        }]
        bad_path.write_text(json.dumps(missing_field_snapshot), encoding="utf-8")
        raised_missing_field = False
        try:
            registry.load_registry(path=str(bad_path))
        except ValueError:
            raised_missing_field = True
        check(raised_missing_field,
              "load_registry(path=...) fails fast on a row missing a "
              "required field (snapshot_date)")
    finally:
        if bad_path.exists():
            bad_path.unlink()

    # A clean reload afterwards must still work (cache untouched by the
    # failed loads above, since the raise happens before _registry_cache is
    # reassigned).
    reg_after_bad = registry.load_registry(path=REGISTRY_SAMPLE)
    check(len(reg_after_bad) == 5,
          f"load_registry(path=...) still works normally after a prior "
          f"corrupted-snapshot load raised (got {len(reg_after_bad)})")

    # --- (c) check() warn mode -------------------------------------------------
    os.environ.pop("REGISTRY_ENFORCEMENT", None)
    check(registry.enforcement_mode() == "warn", "enforcement_mode() defaults to 'warn'")
    registry.reset_unregistered_report()

    degraded = registry.check("IK-IN_Totally_New_Unregistered_Campaign")
    check(degraded.registered is False, "warn-mode check() on an unregistered name returns registered=False")
    check(degraded.segment == "Other" and degraded.program == "other",
          f"warn-mode degraded entry is segment=Other/program=other (got "
          f"segment={degraded.segment!r}, program={degraded.program!r})")
    report = registry.unregistered_report()
    check(report.get("IK-IN_Totally_New_Unregistered_Campaign") == 1,
          f"unregistered_report() counts the unregistered name once (got {report})")

    # A registered name (including its suffixed variant) must NOT be counted
    # as unregistered, and must return the real (non-degraded) entry.
    registered_check = registry.check("IK-IN_Brand__333333333")
    check(registered_check.registered is True,
          "check() on a registered (suffixed) name returns registered=True, not degraded")
    check("IK-IN_Brand__333333333" not in registry.unregistered_report(),
          "check() on a registered name does not add to the unregistered report")

    # --- (d) check() block mode -------------------------------------------------
    os.environ["REGISTRY_ENFORCEMENT"] = "block"
    try:
        check(registry.enforcement_mode() == "block", "enforcement_mode() reads 'block' once set")
        raised = False
        try:
            registry.check("IK-IN_Another_Unregistered_Campaign")
        except registry.UnregisteredCampaignError:
            raised = True
        check(raised, "block-mode check() raises UnregisteredCampaignError for an unregistered name")

        not_raised = True
        try:
            entry = registry.check("IK-IN_Brand")
        except registry.UnregisteredCampaignError:
            not_raised = False
        check(not_raised, "block-mode check() does NOT raise for a registered name")
        check(entry.registered is True, "block-mode check() on a registered name returns registered=True")
    finally:
        os.environ.pop("REGISTRY_ENFORCEMENT", None)

    # --- (d2) invalid REGISTRY_ENFORCEMENT value warns once per distinct value --
    import logging as _logging

    class _CountingHandler(_logging.Handler):
        def __init__(self):
            super().__init__()
            self.records = []

        def emit(self, record):
            self.records.append(record.getMessage())

    handler = _CountingHandler()
    registry.log.addHandler(handler)
    try:
        os.environ["REGISTRY_ENFORCEMENT"] = "bogus_value"
        for _ in range(5):
            check(registry.enforcement_mode() == "warn",
                  "an invalid REGISTRY_ENFORCEMENT value still degrades to 'warn'")
        invalid_warnings = [r for r in handler.records if "Invalid REGISTRY_ENFORCEMENT" in r]
        check(len(invalid_warnings) == 1,
              f"invalid REGISTRY_ENFORCEMENT='bogus_value' is only warned once across "
              f"5 calls, not once per call (got {len(invalid_warnings)})")

        # A second, DIFFERENT invalid value warns again exactly once (it's
        # per-distinct-value, not a global one-shot latch).
        os.environ["REGISTRY_ENFORCEMENT"] = "another_bogus_value"
        for _ in range(3):
            registry.enforcement_mode()
        invalid_warnings_2 = [r for r in handler.records if "Invalid REGISTRY_ENFORCEMENT" in r]
        check(len(invalid_warnings_2) == 2,
              f"a second distinct invalid value logs its own single warning "
              f"(got {len(invalid_warnings_2)} total invalid-value warnings)")
    finally:
        registry.log.removeHandler(handler)
        os.environ.pop("REGISTRY_ENFORCEMENT", None)

    # --- (e) build_campaign_registry classification -----------------------------
    raw_campaigns = build_campaign_registry.load_fixture(RAW_CAMPAIGNS_FIXTURE)
    check(len(raw_campaigns) == 7, f"raw campaigns fixture has 7 rows (got {len(raw_campaigns)})")

    classified = build_campaign_registry.build_registry_rows(raw_campaigns, SNAPSHOT_DATE)
    check(len(classified) == 7, f"build_registry_rows returns 7 classified rows (got {len(classified)})")

    by_name = {c["campaign_name"]: c for c in classified}

    disc = by_name["IK-IN_Discovery_EdgeUp_InMarket_TechnologyIndustry_INDIA"]
    check(disc["segment"] == "Discovery" and disc["program"] == "edgeup"
          and disc["audience"] == "inmarket" and disc["needs_attention"] is False,
          f"clean Discovery/EdgeUp/InMarket campaign classifies cleanly (got {disc})")
    check(disc["status"] == "active", f"ENABLED Google campaign maps to status=active (got {disc['status']!r})")

    brand_flagship = by_name["IK-IN_Brand_Flagship_INDIA"]
    check(brand_flagship["segment"] == "Brand" and brand_flagship["program"] == "flagship"
          and brand_flagship["needs_attention"] is False,
          f"Brand/Flagship campaign classifies cleanly (got {brand_flagship})")

    youtube = by_name["IK-IN_YouTube_Awareness_Q3_INDIA"]
    check(youtube["segment"] == "Other" and youtube["program"] == "other"
          and youtube["needs_attention"] is True,
          f"unparseable YouTube campaign flags needs_attention (got {youtube})")

    pmax = by_name["IK-IN_PMax_LevelUp_INDIA"]
    check(pmax["segment"] == "Other" and pmax["program"] == "levelup"
          and pmax["needs_attention"] is True,
          f"PMax campaign (unparseable segment despite known program) flags "
          f"needs_attention (got {pmax})")
    check(pmax["status"] == "paused", f"PAUSED Google campaign maps to status=paused (got {pmax['status']!r})")

    li_clean = by_name["IK-IN_LinkedIn_EdgeUp_Conversation_EM_JF"]
    check(li_clean["segment"] == "LinkedIn" and li_clean["program"] == "edgeup"
          and li_clean["audience"] == "conversation" and li_clean["needs_attention"] is False,
          f"clean LinkedIn campaign classifies cleanly (got {li_clean})")
    check(li_clean["status"] == "active", f"ACTIVE LinkedIn campaign maps to status=active (got {li_clean['status']!r})")

    li_other = by_name["IK-IN_LinkedIn_Outreach_Random_Test"]
    check(li_other["segment"] == "LinkedIn" and li_other["program"] == "other"
          and li_other["needs_attention"] is True,
          f"LinkedIn campaign with no known program flags needs_attention "
          f"even though segment is always LinkedIn (got {li_other})")
    check(li_other["status"] == "paused", f"PAUSED LinkedIn campaign maps to status=paused (got {li_other['status']!r})")

    structured = by_name["google_edgeup_nb_in_inmarket-tech-professionals_202607_test"]
    check(structured["segment"] == "NB" and structured["program"] == "edgeup"
          and structured["audience"] == "inmarket" and structured["needs_attention"] is False,
          f"future structured-name campaign classifies cleanly (got {structured})")

    flagged_count = sum(1 for c in classified if c["needs_attention"])
    check(flagged_count == 3, f"exactly 3 of 7 campaigns flagged needs_attention (got {flagged_count})")

    model_cls, _bq_schema = schemas.REGISTRY["campaign_registry"]
    for c in classified:
        model_cls(**c["registry_row"])   # raises pydantic.ValidationError on any mismatch
    check(True, "every classified row's registry_row validates against schemas.CampaignRegistryRow")

    # --- (e2) _map_status fails closed for an unrecognized platform ------------
    check(build_campaign_registry._map_status("google", "ENABLED") == "active",
          "_map_status still maps a known google status normally")
    check(build_campaign_registry._map_status("google", "SOME_BRAND_NEW_STATUS") == "archived",
          "_map_status still defaults an unrecognized status on a KNOWN platform to "
          "'archived' (unchanged, intentional fail-open behavior for that case)")
    raised_unknown_platform = False
    try:
        build_campaign_registry._map_status("meta", "ACTIVE")
    except ValueError:
        raised_unknown_platform = True
    check(raised_unknown_platform,
          "_map_status raises ValueError for an unrecognized platform (fail closed) "
          "instead of silently defaulting to 'archived'")

    # --- (e3) markdown escaping (BLOCKER fix) -----------------------------------
    check(build_campaign_registry._md_cell(None) == "", "_md_cell(None) renders as an empty cell")
    check(build_campaign_registry._md_cell(r"a|b") == r"a\|b", "_md_cell escapes a bare pipe")
    check(build_campaign_registry._md_cell(r"a`b") == r"a\`b", "_md_cell escapes a bare backtick")
    check(build_campaign_registry._md_cell(r"a\b") == r"a\\b",
          "_md_cell escapes a bare backslash")
    check(build_campaign_registry._md_cell(r"a\|b") == r"a\\\|b",
          "_md_cell escapes backslash before pipe (order matters -- the pipe's own "
          "escaping backslash must not itself get re-escaped)")

    pipe_name = "Google | Display | Targeted Audiences | July 2025"
    synthetic_pipe_row = [{
        "campaign_name": pipe_name,
        "platform_campaign_id": "22705307673",
        "platform": "google",
        "status": "paused",
        "segment": "Other",
        "program": "other",
        "audience": None,
        "needs_attention": True,
    }]
    pipe_md = build_campaign_registry.render_backfill_markdown(synthetic_pipe_row, SNAPSHOT_DATE)
    pipe_row_lines = [ln for ln in pipe_md.splitlines() if "Google" in ln and "Display" in ln]
    check(len(pipe_row_lines) == 2,
          f"the pipe-containing campaign renders in both the flagged and All-campaigns "
          f"sections (got {len(pipe_row_lines)} matching line(s))")
    for ln in pipe_row_lines:
        # The name's 3 internal pipes must each render escaped (\|), not as
        # bare column delimiters.
        escaped_pipe_count = ln.count("\\|")
        check(escaped_pipe_count == 3,
              f"all 3 of the campaign name's internal pipes are escaped in the "
              f"rendered row (got {escaped_pipe_count}; line={ln!r})")
        # Split the way a markdown table parser would: on a `|` that is NOT
        # escaped by a preceding backslash. A naive `ln.split("|")` would
        # still split on the escaped pipes too (Python's str.split has no
        # notion of markdown escaping), which is exactly the bug this fix
        # closes -- so the test must use the same escape-aware rule a real
        # renderer applies, not plain split().
        cols = re.split(r"(?<!\\)\|", ln)
        check(len(cols) == 10,
              f"pipe-containing row still renders as exactly 8 markdown table "
              f"columns once escaped pipes are correctly treated as literal "
              f"(got {len(cols) - 2} columns; line={ln!r})")

    # --- (f) full CLI run + local-snapshot round trip ----------------------------
    preview_path = config.STAGING_DIR / "bq_preview_campaign_registry.json"
    if preview_path.exists():
        preview_path.unlink()
    md_path = config.STAGING_DIR / "test_campaign_registry_backfill.md"
    if md_path.exists():
        md_path.unlink()

    rc = build_campaign_registry.main([
        "--from-fixture", RAW_CAMPAIGNS_FIXTURE,
        "--snapshot-date", str(SNAPSHOT_DATE),
        "--out-md", str(md_path),
    ])
    check(rc == 0, f"build_campaign_registry.main() --from-fixture exits 0 (got {rc})")

    check(preview_path.exists(), f"DRY_RUN preview file created at {preview_path}")
    preview = json.loads(preview_path.read_text(encoding="utf-8"))
    expected_table_id = (
        f"{config.BQ_PROJECT}.{config.TARGET_DATASET}.{config.TABLE_PREFIX}campaign_registry")
    check(preview["table"] == expected_table_id, f"preview['table'] == {expected_table_id!r}")
    check(preview["row_count"] == 7, f"preview row_count == 7 (got {preview['row_count']})")
    check(preview["partition_field"] == "snapshot_date", "preview partition_field == 'snapshot_date'")

    check(md_path.exists(), f"backfill markdown report written to {md_path}")
    md_text = md_path.read_text(encoding="utf-8")
    check("IK-IN_YouTube_Awareness_Q3_INDIA" in md_text,
          "markdown report lists the flagged YouTube campaign")
    check("## Campaigns needing attention" in md_text,
          "markdown report has the literal '## Campaigns needing attention' "
          "header rendered by render_backfill_markdown() (tightened from a "
          "loose case-insensitive substring match)")

    # Now prove the local-snapshot fallback works: clear the in-memory cache
    # and let load_registry() (no explicit path) re-read the DRY_RUN preview
    # file main() just wrote, with DRY_RUN still true.
    registry.clear_cache()
    reloaded = registry.load_registry()
    check(len(reloaded) == 7, f"load_registry() with no path re-reads the local DRY_RUN "
                              f"preview snapshot (got {len(reloaded)} entries)")
    backfilled = registry.resolve("IK-IN_Brand_Flagship_INDIA")
    check(backfilled is not None and backfilled.program == "flagship",
          "resolve() finds a campaign that was just backfilled via the CLI run")

    # Cleanup test-only artifact; leave the real preview file (other tests /
    # manual runs may rely on staging/ reflecting the last run).
    if md_path.exists():
        md_path.unlink()

    print("\nAll registry assertions passed.")


if __name__ == "__main__":
    main()
