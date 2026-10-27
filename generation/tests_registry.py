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

Weekly carry-forward refresh (--refresh, added 2026-10-27), on
fixtures/registry_prior_snapshot.json + registry_refresh_live_raw.json:
  (r1-r10) merge_refresh(): manual classification kept on an unchanged id,
      rename re-classifies an untouched row / keeps a hand-edited one,
      new id auto-classified, prior-only id archived on a fetched platform
      (and carried unchanged on an unfetched one), same-name LinkedIn ids
      both kept, null-id prior matched by name, every row schema-valid with
      the new snapshot_date, never fewer rows than the prior registry; plus
      a steady-state re-merge and the null-id dedupe.
  (c1-c9) main(['--refresh', ...]) end to end in DRY_RUN: report sections +
      summary line, staleness gate, shrink guard (exit 3), per-platform
      fail-soft, nothing fetched (exit 1), prior unreadable live (exit 2),
      backfill flags unchanged, partial-listing guard, arg validation.
  (l1-l6) LINKEDIN_API_VERSION env override + the bounded 426 auto-fallback,
      against a mocked http.client (no network).
  (q1-q2) the BQ query shapes: registry.py reads ONLY MAX(snapshot_date);
      the refresh's prior read is latest-per-(platform, id) -- both against
      a fake client.
"""
import json
import logging
import os
import re
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import bq_writer
import config
import schemas
import registry
import build_campaign_registry

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
REGISTRY_SAMPLE = str(FIXTURES_DIR / "registry_sample.json")
RAW_CAMPAIGNS_FIXTURE = str(FIXTURES_DIR / "registry_campaigns_raw.json")
SNAPSHOT_DATE = date(2026, 7, 4)
PRIOR_FIXTURE = str(FIXTURES_DIR / "registry_prior_snapshot.json")
LIVE_FIXTURE = str(FIXTURES_DIR / "registry_refresh_live_raw.json")
REFRESH_DATE = date(2026, 11, 2)
REFRESH_NOW = datetime(2026, 11, 2, 6, 0, tzinfo=UTC)
PRIOR_REG_AT = "2026-07-04T00:00:00+00:00"


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

    test_merge_refresh()
    test_refresh_cli()
    test_linkedin_version_fallback()
    test_bq_query_shapes()

    print("\nAll registry assertions passed.")


# ---------------------------------------------------------------------------
# Weekly carry-forward refresh (2026-10-27)
# ---------------------------------------------------------------------------
class _LogCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record.getMessage())

    def has(self, needle):
        return any(needle in r for r in self.records)


def _by_id(rows):
    return {(r["platform"], r["platform_campaign_id"]): r for r in rows}


def test_merge_refresh():
    bcr = build_campaign_registry
    prior_rows, prior_max = bcr.load_prior_rows(PRIOR_FIXTURE)
    check(len(prior_rows) == 16 and prior_max == SNAPSHOT_DATE,
          f"load_prior_rows(fixture) -> 16 rows, max snapshot 2026-07-04 "
          f"(got {len(prior_rows)}, {prior_max})")
    check(all(set(r) <= set(bcr._REGISTRY_ROW_FIELDS) for r in prior_rows),
          "load_prior_rows strips non-model keys (the fixture's stray 'rn' column)")
    live = bcr.load_fixture(LIVE_FIXTURE)

    merged, rep = bcr.merge_refresh(prior_rows, live, {"google", "linkedin"},
                                    REFRESH_DATE, REFRESH_NOW)
    ids = _by_id(merged)
    totals = bcr._refresh_totals(rep)

    # (r1) unchanged id + hand-edited classification -> kept; status follows live
    r1 = ids[("google", "9001")]
    check((r1["segment"], r1["program"], r1["funnel_stage"]) == ("Brand", "flagship", "retarget"),
          f"(r1) same-name id keeps its hand-edited segment/program/funnel_stage (got {r1})")
    check(r1["registered_at"] == "2026-07-03T21:36:09+00:00",
          "(r1) same-name id keeps its original registered_at verbatim")
    check(r1["status"] == "paused" and any(e["platform_campaign_id"] == "9001"
                                           for e in rep["status_changed"]),
          "(r1) ENABLED->PAUSED on the live side maps to status=paused, reported as a status change")

    # (r2) renamed, prior still auto-equal -> follows the new name, re-classified
    r2 = ids[("google", "9002")]
    check(r2["campaign_name"] == "IK-IN_Search_NB_EdgeUp_Higher_Education"
          and r2["utm_campaign"] == r2["campaign_name"],
          f"(r2) renamed id: campaign_name + utm_campaign = the new live name (got {r2})")
    check((r2["segment"], r2["program"]) == ("NB", "edgeup") and r2["registered_at"] == PRIOR_REG_AT,
          f"(r2) untouched classification is re-derived from the new name "
          f"(other -> edgeup), registered_at kept (got {r2})")

    # (r3) renamed + hand-edited -> override kept, counted
    r3 = ids[("google", "9003")]
    check(r3["campaign_name"] == "IK-IN_Search_NB_FDE_35-54" and r3["program"] == "levelup",
          f"(r3) renamed id keeps its hand-edited program as a manual override "
          f"(auto from the new name would be 'other') (got {r3})")
    check(totals["overrides_kept"] == 1 and totals["renamed"] == 2,
          f"(r3) report: overrides_kept=1, renamed=2 (got {totals})")

    # (r4) new id -> auto-classified, registered_at = now
    r4 = ids[("google", "9100")]
    check((r4["segment"], r4["program"], r4["status"]) == ("NB", "edgeup", "active")
          and r4["registered_at"] == REFRESH_NOW,
          f"(r4) new id is auto-classified with registered_at=now (got {r4})")
    check(totals["new"] == 2 and rep["by_platform"]["google"]["new"] == 1
          and rep["by_platform"]["linkedin"]["new"] == 1,
          f"(r4) report: 2 new (1 google, 1 linkedin) (got {totals})")

    # (r5) prior-only id on a fetched platform -> archived; an already-archived
    #      absent id is carried but not re-reported as 'disappeared'
    check(ids[("google", "9004")]["status"] == "archived",
          "(r5) prior-only id on a fetched platform is carried forward as archived")
    check(totals["disappeared"] == 1 and rep["disappeared"][0]["platform_campaign_id"] == "9004",
          f"(r5) exactly one 'disappeared' (the newly missing id) (got {totals['disappeared']})")
    check(ids[("google", "9013")]["status"] == "archived",
          "(r5) an already-archived absent id stays in the snapshot, still archived")

    # (r6) platform not fetched -> its prior rows carried forward unchanged
    google_only = [r for r in live if r["platform"] == "google"]
    m6, rep6 = bcr.merge_refresh(prior_rows, google_only, {"google"}, REFRESH_DATE, REFRESH_NOW)
    i6 = _by_id(m6)
    check([i6[("linkedin", x)]["status"] for x in ("7001", "7002", "7003")]
          == ["active", "paused", "active"],
          "(r6) unfetched LinkedIn rows carried forward with their statuses intact")
    check(bcr._refresh_totals(rep6)["carried_unfetched"] == 3
          and ("linkedin", "7004") not in i6,
          "(r6) report counts 3 carried_unfetched and no LinkedIn 'new' rows")

    # (r7) same name, two ids -> both kept
    pwc = [r for r in merged
           if r["campaign_name"] == "IK-IN_LinkedIn_PWC_AgenticAI_Carousel_Predictive_SWE_T1"]
    check(sorted((r["platform_campaign_id"], r["status"]) for r in pwc)
          == [("7001", "active"), ("7002", "paused")],
          f"(r7) two LinkedIn ids sharing a name are both kept, keyed by id (got {pwc})")

    # (r8) prior row with a null id matches the live row by name
    r8 = ids.get(("google", "9005"))
    check(r8 is not None and r8["registered_at"] == PRIOR_REG_AT
          and not any(r["campaign_name"] == "IK-IN_Brand_Flagship_INDIA"
                      and r["platform_campaign_id"] is None for r in merged),
          f"(r8) null-id prior row matched by name: it gains the live id and "
          f"keeps registered_at, no duplicate left behind (got {r8})")

    # (r9) schema-valid, new snapshot_date, model keys only
    for r in merged:
        schemas.CampaignRegistryRow(**r)
    check(all(r["snapshot_date"] == str(REFRESH_DATE) for r in merged)
          and all(set(r) == set(bcr._REGISTRY_ROW_FIELDS) for r in merged),
          "(r9) every merged row validates as CampaignRegistryRow, has "
          "snapshot_date=2026-11-02 and only model keys")

    # (r10) never smaller than the prior registry
    check(len(merged) == 18 and len(merged) >= len(prior_rows),
          f"(r10) merged count 18 >= prior 16 (got {len(merged)})")

    # steady state: re-merging the new snapshot with the same listing is a no-op
    m2, rep2 = bcr.merge_refresh(merged, live, {"google", "linkedin"},
                                 date(2026, 11, 9), REFRESH_NOW)
    check(len(m2) == 18 and all(v == 0 for v in bcr._refresh_totals(rep2).values()),
          f"steady state: an unchanged listing re-merges with zero changes "
          f"(got {bcr._refresh_totals(rep2)})")

    # null-id dedupe: once an id is known, the stale name-keyed row is dropped
    stale = dict(ids[("google", "9005")], platform_campaign_id=None, snapshot_date="2026-07-04")
    deduped = bcr._dedupe_prior(merged + [stale])
    check(len(deduped) == 18,
          f"_dedupe_prior drops a null-id row superseded by an id'd row of the "
          f"same name (got {len(deduped)})")


def test_refresh_cli():
    bcr = build_campaign_registry
    preview_path = config.STAGING_DIR / "bq_preview_campaign_registry.json"
    md_path = config.STAGING_DIR / "test_campaign_registry_refresh.md"
    tracked_md = bcr.DEFAULT_MD_PATH
    tracked_before = tracked_md.read_bytes() if tracked_md.exists() else None

    def _clean():
        for p in (preview_path, md_path):
            if p.exists():
                p.unlink()

    cap = _LogCapture()
    bcr.log.addHandler(cap)
    try:
        # (c1) full refresh run from fixtures
        _clean()
        rc = bcr.main(["--refresh", "--from-fixture", LIVE_FIXTURE, "--prior-fixture",
                       PRIOR_FIXTURE, "--snapshot-date", str(REFRESH_DATE),
                       "--out-md", str(md_path)])
        check(rc == 0, f"(c1) --refresh from fixtures exits 0 (got {rc})")
        preview = json.loads(preview_path.read_text(encoding="utf-8"))
        check(preview["partitions"] == [str(REFRESH_DATE)] and preview["row_count"] == 18,
              f"(c1) DRY_RUN preview = one 2026-11-02 partition, 18 rows (got "
              f"{preview['partitions']}, {preview['row_count']})")
        md = md_path.read_text(encoding="utf-8")
        for section in ("## New since last snapshot", "## Renamed", "## Status changes",
                        "## Disappeared (archived)", "## Manual overrides kept",
                        "## Campaigns needing attention", "## All campaigns"):
            check(section in md, f"(c1) refresh report has section {section!r}")
        check("`IK-IN_Search_NB_FDE_35-54` | manual override kept |" in md
              and "Google \\| Display \\| Targeted Audiences \\| July 2025" in md,
              "(c1) report lists the kept override and escapes pipes in names")
        summary = [r for r in cap.records if r.startswith("REGISTRY_REFRESH snapshot=")]
        check(len(summary) == 1 and summary[0].startswith(
                  "REGISTRY_REFRESH snapshot=2026-11-02 total=18 google=14 linkedin=4 new=2 "
                  "renamed=2 status_changed=1 disappeared=1 overrides_kept=1 carried_unfetched=0 "
                  "needs_attention=")
              and summary[0].endswith("sources=google:fetched,linkedin:fetched"),
              f"(c1) one REGISTRY_REFRESH summary line with the expected counts (got {summary})")

        # (c1b) default --out-md in refresh mode -> staging, tracked doc untouched
        default_md = config.STAGING_DIR / f"campaign_registry_refresh_{REFRESH_DATE}.md"
        if default_md.exists():
            default_md.unlink()
        rc = bcr.main(["--refresh", "--from-fixture", LIVE_FIXTURE, "--prior-fixture",
                       PRIOR_FIXTURE, "--snapshot-date", str(REFRESH_DATE)])
        check(rc == 0 and default_md.exists(),
              f"(c1b) refresh without --out-md writes {default_md.name} to staging/")
        tracked_after = tracked_md.read_bytes() if tracked_md.exists() else None
        check(tracked_after == tracked_before,
              "(c1b) refresh mode never touches the tracked docs/campaign-registry-backfill.md")
        default_md.unlink()

        # (c2) staleness gate: 3 days old -> skip before any API call; 7/8 days -> runs
        calls = []
        orig_fg = bcr.fetch_google_campaigns
        bcr.fetch_google_campaigns = lambda: calls.append("google") or [
            r for r in bcr.load_fixture(LIVE_FIXTURE) if r["platform"] == "google"]
        try:
            _clean()
            rc = bcr.main(["--refresh", "--from-google", "--prior-fixture", PRIOR_FIXTURE,
                           "--snapshot-date", "2026-07-07", "--only-if-stale-days", "7",
                           "--out-md", str(md_path)])
            check(rc == 0 and not preview_path.exists() and calls == [],
                  f"(c2) prior snapshot 3 days old + --only-if-stale-days 7 -> exit 0, "
                  f"no API call, no write (rc={rc}, calls={calls})")
            check(cap.has("registry fresh (latest=2026-07-04"),
                  "(c2) the skip logs 'registry fresh (latest=...) -- skipping'")
            for snap in ("2026-07-11", "2026-07-12"):
                _clean()
                rc = bcr.main(["--refresh", "--from-google", "--prior-fixture", PRIOR_FIXTURE,
                               "--snapshot-date", snap, "--only-if-stale-days", "7",
                               "--out-md", str(md_path)])
                check(rc == 0 and preview_path.exists(),
                      f"(c2) prior snapshot {(date.fromisoformat(snap) - SNAPSHOT_DATE).days} "
                      f"days old -> the refresh runs and writes (rc={rc})")
            check(calls == ["google", "google"], f"(c2) fetched once per non-skipped run ({calls})")
        finally:
            bcr.fetch_google_campaigns = orig_fg

        # (c3) shrink guard: a merge that loses rows -> exit 3, nothing written
        writes = []
        orig_write = bq_writer.write_rows
        orig_merge = bcr.merge_refresh
        bq_writer.write_rows = lambda table, rows: writes.append((table, rows)) or 0

        def _lossy_merge(*a, **k):
            m, r = orig_merge(*a, **k)
            return m[:5], r          # simulate a merge bug that drops rows

        bcr.merge_refresh = _lossy_merge
        try:
            rc = bcr.main(["--refresh", "--from-fixture", LIVE_FIXTURE, "--prior-fixture",
                           PRIOR_FIXTURE, "--snapshot-date", str(REFRESH_DATE),
                           "--out-md", str(md_path)])
            check(rc == 3 and writes == [],
                  f"(c3) shrink guard -> exit 3 and write_rows never called (rc={rc})")
        finally:
            bcr.merge_refresh = orig_merge
            bq_writer.write_rows = orig_write

        # (c4) per-platform fail-soft: LinkedIn raises, Google works -> LinkedIn carried
        orig_fl = bcr.fetch_linkedin_campaigns
        bcr.fetch_google_campaigns = lambda: [
            r for r in bcr.load_fixture(LIVE_FIXTURE) if r["platform"] == "google"]
        bcr.fetch_linkedin_campaigns = lambda: (_ for _ in ()).throw(
            RuntimeError("LinkedIn API error (426): version not active"))
        try:
            _clean()
            rc = bcr.main(["--refresh", "--from-google", "--from-linkedin", "--prior-fixture",
                           PRIOR_FIXTURE, "--snapshot-date", str(REFRESH_DATE),
                           "--out-md", str(md_path)])
            rows = json.loads(preview_path.read_text(encoding="utf-8"))["rows"]
            li = _by_id([r for r in rows if r["platform"] == "linkedin"])
            check(rc == 0 and sorted(li) == [("linkedin", "7001"), ("linkedin", "7002"),
                                             ("linkedin", "7003")]
                  and li[("linkedin", "7002")]["status"] == "paused",
                  f"(c4) LinkedIn fetch failure -> exit 0, its 3 prior rows carried "
                  f"forward unchanged (rc={rc}, linkedin={sorted(li)})")
            check(_by_id(rows)[("google", "9001")]["status"] == "paused"
                  and cap.has("carrying LinkedIn forward"),
                  "(c4) Google still refreshed; the LinkedIn failure is logged as carried forward")

            # (c5) both platforms fail -> exit 1, nothing written
            bcr.fetch_google_campaigns = lambda: (_ for _ in ()).throw(
                FileNotFoundError("google-ads/.env"))
            writes.clear()
            bq_writer.write_rows = lambda table, rows: writes.append((table, rows)) or 0
            rc = bcr.main(["--refresh", "--from-google", "--from-linkedin", "--prior-fixture",
                           PRIOR_FIXTURE, "--snapshot-date", str(REFRESH_DATE),
                           "--out-md", str(md_path)])
            check(rc == 1 and writes == [],
                  f"(c5) every fetch fails -> exit 1 and write_rows never called (rc={rc})")
        finally:
            bcr.fetch_google_campaigns = orig_fg
            bcr.fetch_linkedin_campaigns = orig_fl
            bq_writer.write_rows = orig_write

        # (c6) live mode + unreadable prior -> exit 2, nothing written
        orig_dry, orig_load = config.DRY_RUN, bcr.load_prior_rows
        orig_client = bq_writer.get_client
        writes.clear()
        bq_writer.write_rows = lambda table, rows: writes.append((table, rows)) or 0
        try:
            config.DRY_RUN = False
            bcr.load_prior_rows = lambda prior_fixture: (_ for _ in ()).throw(
                RuntimeError("BQ unreachable"))
            rc = bcr.main(["--refresh", "--from-fixture", LIVE_FIXTURE,
                           "--snapshot-date", str(REFRESH_DATE), "--out-md", str(md_path)])
            check(rc == 2 and writes == [],
                  f"(c6) DRY_RUN=false + load_prior_rows raising -> exit 2, no write (rc={rc})")
            # ... and the REAL live prior read raises rather than returning [] on failure
            bcr.load_prior_rows = orig_load
            bq_writer.get_client = lambda: (_ for _ in ()).throw(
                FileNotFoundError("Generation BQ writer key not found"))
            rc = bcr.main(["--refresh", "--from-fixture", LIVE_FIXTURE,
                           "--snapshot-date", str(REFRESH_DATE), "--out-md", str(md_path)])
            check(rc == 2 and writes == [],
                  f"(c6) a failing live BQ prior read is never swallowed -> exit 2 (rc={rc})")
        finally:
            config.DRY_RUN = orig_dry
            bcr.load_prior_rows = orig_load
            bq_writer.get_client = orig_client
            bq_writer.write_rows = orig_write

        # (c7) backfill mode is unchanged (block (f) above passed on it); the
        #      refresh-only flags are refused without --refresh
        for extra in (["--prior-fixture", PRIOR_FIXTURE], ["--only-if-stale-days", "7"],
                      ["--min-retain-ratio", "0.5"]):
            rc = bcr.main(["--from-fixture", RAW_CAMPAIGNS_FIXTURE, *extra,
                           "--out-md", str(md_path)])
            check(rc == 1, f"(c7) {extra[0]} without --refresh exits 1 (got {rc})")

        # (c8) partial-listing guard: a Google listing missing most known ids
        #      is carried forward instead of archiving them
        partial = [r for r in bcr.load_fixture(LIVE_FIXTURE)
                   if r["platform"] == "linkedin" or r["campaign_id"] in ("9001", "9002", "9100")]
        partial_path = config.STAGING_DIR / "test_registry_partial_live.json"
        partial_path.write_text(json.dumps(partial), encoding="utf-8")
        try:
            _clean()
            rc = bcr.main(["--refresh", "--from-fixture", str(partial_path), "--prior-fixture",
                           PRIOR_FIXTURE, "--snapshot-date", str(REFRESH_DATE),
                           "--out-md", str(md_path)])
            rows = _by_id(json.loads(preview_path.read_text(encoding="utf-8"))["rows"])
            check(rc == 0 and rows[("google", "9004")]["status"] == "active"
                  and rows[("google", "9001")]["status"] == "active"
                  and ("google", "9100") not in rows and ("linkedin", "7004") in rows
                  and len(rows) == 17,
                  f"(c8) partial Google listing -> Google carried forward unchanged, "
                  f"LinkedIn still refreshed (rc={rc}, rows={len(rows)})")
            check(cap.has("google listing looks PARTIAL"),
                  "(c8) the partial-listing guard logs a loud warning")
        finally:
            partial_path.unlink()

        # (c9) argument validation
        for bad in ("0", "1.5"):
            rc = bcr.main(["--refresh", "--from-fixture", LIVE_FIXTURE, "--prior-fixture",
                           PRIOR_FIXTURE, "--min-retain-ratio", bad, "--out-md", str(md_path)])
            check(rc == 1, f"(c9) --min-retain-ratio {bad} is rejected with exit 1 (got {rc})")
    finally:
        bcr.log.removeHandler(cap)
        if md_path.exists():
            md_path.unlink()


# --- LinkedIn-Version fallback (mocked http.client; no network) -------------
class _FakeResp:
    def __init__(self, status, body):
        self.status = status
        self._body = json.dumps(body).encode()

    def read(self):
        return self._body


class _FakeLinkedIn:
    """Stands in for http.client.HTTPSConnection. `active(version)` decides
    which LinkedIn-Version values are servable; `token_ok(token)` which bearer
    tokens are valid. Records every request."""

    def __init__(self, active, token_ok=lambda t: True):
        self.active, self.token_ok = active, token_ok
        self.gets, self.refreshes = [], 0

    def __call__(self, host, timeout=None):
        fake = self

        class _Conn:
            def request(self, method, path, body=None, headers=None):
                self.args = (host, method, path, headers or {})

            def getresponse(self):
                host_, _method, path, headers = self.args
                if host_ == "www.linkedin.com":
                    fake.refreshes += 1
                    return _FakeResp(200, {"access_token": "tok-new"})
                version = headers.get("LinkedIn-Version")
                token = headers.get("Authorization", "").removeprefix("Bearer ")
                fake.gets.append((version, path))
                if not fake.token_ok(token):
                    return _FakeResp(401, {"status": 401})
                if not fake.active(version):
                    return _FakeResp(426, {"status": 426, "code": "NONEXISTENT_VERSION",
                                           "message": f"Requested version {version} is not active"})
                if "pageToken=p2" in path:
                    return _FakeResp(200, {"elements": [{"id": 3, "name": "C3", "status": "PAUSED"}],
                                           "metadata": {}})
                return _FakeResp(200, {"elements": [{"id": 1, "name": "C1", "status": "ACTIVE"},
                                                    {"id": 2, "name": "C2", "status": "ACTIVE"}],
                                       "metadata": {"nextPageToken": "p2"}})

            def close(self):
                pass

        return _Conn()


def test_linkedin_version_fallback():
    import http.client
    bcr = build_campaign_registry

    # (l1) env override validation
    check(bcr._resolve_linkedin_api_version(None) == "202610"
          and bcr._resolve_linkedin_api_version("") == "202610",
          "(l1) LINKEDIN_API_VERSION unset/blank -> default 202610")
    check(bcr._resolve_linkedin_api_version(" 202608 ") == "202608",
          "(l1) a valid YYYYMM override is used (whitespace-stripped)")
    for bad in ("2026-08", "v202608", "20268", "19990101", "302608"):
        check(bcr._resolve_linkedin_api_version(bad) == "202610",
              f"(l1) malformed LINKEDIN_API_VERSION={bad!r} falls back to the default")

    # (l2) candidate window: current UTC month + 12 back, across a year boundary
    cands = bcr._linkedin_version_candidates(datetime(2027, 1, 15, tzinfo=UTC))
    check(len(cands) == 13 and cands[:3] == ["202701", "202612", "202611"]
          and cands[-1] == "202601",
          f"(l2) 13 candidates newest-first from 202701 back to 202601 (got {cands})")

    env = {"LINKEDIN_ACCESS_TOKEN": "tok-old", "LINKEDIN_AD_ACCOUNT_ID": "123",
           "LINKEDIN_REFRESH_TOKEN": "r", "LINKEDIN_CLIENT_ID": "c",
           "LINKEDIN_CLIENT_SECRET": "s"}
    orig = (http.client.HTTPSConnection, bcr.collect_kw_perf.load_env, bcr._utc_now,
            bcr.LINKEDIN_API_VERSION)
    cap = _LogCapture()
    bcr.log.addHandler(cap)
    try:
        bcr.collect_kw_perf.load_env = lambda path: dict(env)
        bcr._utc_now = lambda: datetime(2026, 10, 27, tzinfo=UTC)

        def servable(v):
            # Live facts 2026-10-27: <=202512 -> 426. Also pretend 202610/202609
            # aren't out yet, so the first 2xx is the 3rd probe.
            return "202601" <= (v or "") <= "202608"

        # (l3) sunset configured version -> fallback, then reused for page 2
        fake = _FakeLinkedIn(servable)
        http.client.HTTPSConnection = fake
        bcr.LINKEDIN_API_VERSION = "202503"
        rows = bcr.fetch_linkedin_campaigns()
        check([r["campaign_id"] for r in rows] == ["1", "2", "3"],
              f"(l3) 426 fallback recovers the full paginated listing (got {rows})")
        check([v for v, _ in fake.gets] == ["202503", "202610", "202609", "202608", "202608"],
              f"(l3) probes newest-first, stops at the first 2xx, reuses it for "
              f"page 2 (got {[v for v, _ in fake.gets]})")
        check(cap.has("bump LINKEDIN_API_VERSION to 202608"),
              "(l3) the fallback logs 'bump LINKEDIN_API_VERSION'")

        # (l4) nothing servable -> bounded (1 + 13 GETs), then the fetch fails
        fake = _FakeLinkedIn(lambda v: False)
        http.client.HTTPSConnection = fake
        raised = None
        try:
            bcr.fetch_linkedin_campaigns()
        except RuntimeError as e:
            raised = str(e)
        check(raised is not None and "426" in raised and len(fake.gets) == 14,
              f"(l4) no active version -> RuntimeError after exactly 1 + 13 GETs "
              f"(got {len(fake.gets)} GETs, error={raised!r})")

        # (l5) expired access token AND sunset version: one refresh, then fallback
        fake = _FakeLinkedIn(servable, token_ok=lambda t: t == "tok-new")
        http.client.HTTPSConnection = fake
        rows = bcr.fetch_linkedin_campaigns()
        check(len(rows) == 3 and fake.refreshes == 1
              and [v for v, _ in fake.gets] == ["202503", "202503", "202610", "202609",
                                                "202608", "202608"],
              f"(l5) 401 -> one in-memory refresh -> 426 -> fallback, all in one fetch "
              f"(refreshes={fake.refreshes}, versions={[v for v, _ in fake.gets]})")

        # (l6) configured version still active -> no fallback, no warning;
        #      and an auth failure mid-fallback stops the probing early
        cap.records.clear()
        fake = _FakeLinkedIn(servable)
        http.client.HTTPSConnection = fake
        bcr.LINKEDIN_API_VERSION = "202608"
        rows = bcr.fetch_linkedin_campaigns()
        check(len(rows) == 3 and [v for v, _ in fake.gets] == ["202608", "202608"]
              and not cap.has("bump LINKEDIN_API_VERSION"),
              "(l6) an active configured version is used as-is (2 GETs, no fallback warning)")
        seq = iter([(426, {}), (403, {"status": 403})])
        probed = []
        v, status, _ = bcr._linkedin_negotiate_version(
            lambda ver: probed.append(ver) or next(seq), "202503",
            now=datetime(2026, 10, 27, tzinfo=UTC))
        check(status == 403 and v == "202503" and probed == ["202610", "202609"],
              f"(l6) a 403 during the fallback stops probing at once (probed={probed})")
    finally:
        (http.client.HTTPSConnection, bcr.collect_kw_perf.load_env, bcr._utc_now,
         bcr.LINKEDIN_API_VERSION) = orig
        bcr.log.removeHandler(cap)


# --- BQ query shapes (fake client; no BigQuery) ------------------------------
class _FakeBQ:
    def __init__(self, rows):
        self.rows, self.queries = rows, []

    def query(self, q):
        self.queries.append(q)
        rows = self.rows

        class _Job:
            def result(self, timeout=None):
                class _Row(dict):
                    pass
                return [_Row(r) for r in rows]

        return _Job()


def test_bq_query_shapes():
    sample = json.loads(Path(REGISTRY_SAMPLE).read_text(encoding="utf-8"))
    orig_client, orig_dry = bq_writer.get_client, config.DRY_RUN
    try:
        config.DRY_RUN = False

        # (q1) registry.py: latest complete snapshot only
        fake = _FakeBQ(sample)
        bq_writer.get_client = lambda: fake
        registry.clear_cache()
        reg = registry.load_registry()
        check(len(fake.queries) == 1 and "MAX(snapshot_date)" in fake.queries[0]
              and "ROW_NUMBER" not in fake.queries[0],
              "(q1) registry._load_from_bq reads only WHERE snapshot_date = MAX(snapshot_date)")
        check(len(reg) == 5 and reg["IK-IN_Brand"].segment == "Brand",
              f"(q1) the live rows load through the fake client (got {len(reg)} entries)")

        # (q2) refresh prior read: latest row per (platform, id-or-name), all snapshots
        prior = json.loads(Path(PRIOR_FIXTURE).read_text(encoding="utf-8"))
        fake = _FakeBQ(prior)
        bq_writer.get_client = lambda: fake
        rows, prior_max = build_campaign_registry.load_prior_rows(None)
        q = fake.queries[0]
        check("PARTITION BY platform" in q
              and "COALESCE(platform_campaign_id, CONCAT('name:', campaign_name))" in q
              and "MAX(snapshot_date)" not in q,
              "(q2) the refresh's live prior read is latest-per-(platform, id) across snapshots")
        check(len(rows) == 16 and prior_max == SNAPSHOT_DATE and "rn" not in rows[0],
              f"(q2) live prior rows come back stripped, with their max snapshot_date "
              f"(got {len(rows)}, {prior_max})")
    finally:
        bq_writer.get_client, config.DRY_RUN = orig_client, orig_dry
        registry.clear_cache()


if __name__ == "__main__":
    main()
