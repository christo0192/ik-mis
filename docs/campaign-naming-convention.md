# Campaign / UTM Naming Convention — Proposal for Perf Team Review

Status: **draft for review** (Phase 0 / Step 2). Nothing here is enforced yet — this
is the spec the `campaign_registry` validation helper (Step 2, Step 1 schemas) will be
built against once the perf team signs off.

## 1. Current state (verified in code)

Segment/program classification is **substring matching on free-text campaign names**,
duplicated across at least four files instead of living in one place:

| Rule | Where (file:line) | Logic |
|---|---|---|
| Segment: Brand | `build_segment_tables.py:74`, `build_dashboard.py:89`, `calibrate_benchmarks.py:64` | `"brand" in name.lower()` |
| Segment: Discovery | `build_segment_tables.py:76`, `build_dashboard.py:91`, `calibrate_benchmarks.py:65` | `"discovery" in name.lower()` |
| Segment: NB | `build_segment_tables.py:78`, `build_dashboard.py:93`, `calibrate_benchmarks.py:66` | `"_nb_" in name.lower()` (order matters — Brand/Discovery checked **before** NB) |
| Program: Flagship / EdgeUp / PWC / SwitchUp / LevelUp | `build_dashboard.py:67-79` | `"flagship" in n`, `"edgeup" in n or "edge_up" in n`, `"pwc" in n`, `"switchup" in n`, `"levelup" in n` → else `"Other"` |
| BQ channel → segment | `build_segment_tables.py:49-54` (`BQ_CHANNEL_TO_SEGMENT`) | exact string match on `v_campaign_funnel.channel`: `"Google NB"→NB`, `"Google Discovery"→Discovery`, `"Google Brand"→Brand`, `"LinkedIn"→LinkedIn` |
| Sheet source → segment | `build_segment_tables.py:85` | same `BQ_CHANNEL_TO_SEGMENT` map applied to Swati sheet's `Source (BQ)` column |
| Google segment universe | `collect_drilldown.py:38` | `GOOGLE_SEGMENTS = {"NB", "Discovery", "Brand"}` |
| Campaign-name prefix strip (readability only) | `collect_drilldown.py:56-70` | strips `IK-IN_`, `IK_IN_`, `IK-IN-`, `IK_` prefixes, then drops boilerplate tokens `{google, nb, discovery, brand, linkedin, edgeup, flagship, pwc, levelup, switchup, paid, india, gdn}` |
| Campaign ID from UTM | `build_dashboard.py:106-111` | `utm_campaign.rsplit("__", 1)[-1]` if the tail is all-digits, e.g. `"Brand_INDIA__16636249119"` → `16636249119` |

A real live campaign name observed in the drilldown collector's docstring:
`IK-IN_Discovery_EdgeUp_InMarket_TechnologyIndustry_INDIA`.

**Why this is brittle:**
- **Triplicated logic** — the same three substring rules are copy-pasted in three files (`build_segment_tables.py`, `build_dashboard.py`, `calibrate_benchmarks.py`); a rename fixed in one place silently drifts from the others.
- **Renames break attribution** — if someone edits a live campaign name (e.g. drops `_nb_`, retypes `Discovery` as `Disco`), it silently falls out of every segment table with no error, no alert.
- **Order-dependent, ambiguous overlaps** — `"brand" in n` will also match a campaign that happens to contain "Brand" as a sub-word of something else; NB is checked last specifically to avoid Brand/Discovery collisions, which means the rule set is fragile to any new keyword.
- **Redundant/inconsistent geo + prefix tokens** — real names carry the country twice (`IK-IN_` prefix *and* trailing `INDIA`), and the prefix itself has at least 4 spellings in the wild (`IK-IN_`, `IK_IN_`, `IK-IN-`, `IK_`).
- **Meta/LinkedIn inconsistency** — LinkedIn spend is bucketed as one giant "LinkedIn" segment because the whole ad account maps 1:1 to the segment (no sub-parsing at all); Meta (deferred but coming in Phase 2) has no naming convention today, so nothing here transfers automatically.
- **Free-text drift** — nothing prevents a new campaign from being created without `_nb_`/`discovery`/`brand` in the name at all, in which case it silently returns `None`/`"Other"` and vanishes from every segment/program rollup with no error surfaced.
- **utm_campaign ≠ campaign name** — the UTM convention observed (`Brand_INDIA__16636249119`) is a *different*, thinner string than the actual Google/LinkedIn campaign name, with its own ad-hoc double-underscore-id suffix — a second free-text surface to keep in sync.

## 2. Proposed convention

### 2.1 Structured name format

```
{platform}_{program}_{segment}_{geo}_{funnel}_{yyyymm}_{freeform}
```

Tokens use the **vocabulary already live in the code**, not invented terms:

| Token | Allowed values | Source |
|---|---|---|
| `platform` | `google`, `linkedin`, (`meta` — reserved, Phase 2) | new — today implicit per-account |
| `program` | `flagship`, `edgeup`, `pwc`, `switchup`, `levelup`, `other` | `build_dashboard.py:detect_program` |
| `segment` | `nb`, `discovery`, `brand`, `linkedin` | `SEGMENTS` in `build_segment_tables.py:46` |
| `geo` | `in` (India) today; open to more later | replaces redundant `IK-IN_` + `INDIA` double-tagging |
| `funnel` | `tofu`, `mofu`, `bofu` (top/mid/bottom of funnel) — maps loosely to today's ad-hoc targeting tags like `InMarket`/`TechnologyIndustry` | new — needed for Phase 1 copy/angle targeting, not present in code today (open question 1) |
| `yyyymm` | 6-digit launch month, e.g. `202607` | new — lets `campaign_registry` and reporting bucket by cohort/vintage without parsing free text |
| `freeform` | lowercase alnum + hyphens, analyst's own descriptor (audience, angle, test id) | replaces today's uncontrolled tail (`InMarket_TechnologyIndustry`, etc.) |

Example new campaign name:
```
google_edgeup_nb_in_mofu_202607_inmarket-tech-professionals
```

### 2.2 Character / case rules
- All lowercase (avoids `Discovery` vs `discovery` vs `DISCOVERY` drift).
- Tokens separated by single underscore `_`; no underscores *inside* a token — use hyphens (`freeform` may contain `-`, e.g. `inmarket-tech-professionals`).
- No spaces, no double underscores (the old `__id` suffix trick is retired — the numeric platform campaign ID is already available natively from the Google Ads / LinkedIn APIs and does not need to be smuggled into the name).
- Max recommended length: platform UI truncation varies (Google ~257 chars, LinkedIn shorter) — keep total under 80 chars, `freeform` under 30.

### 2.3 UTM mapping
- `utm_campaign` = the structured campaign name above, **verbatim** (single source of truth — no separate shorter UTM string to keep in sync).
- `utm_source` = `google` | `linkedin` | `meta` (matches `platform` token exactly).
- `utm_medium` = `cpc` (Google/LinkedIn paid search/social); `paid-social` also acceptable for LinkedIn Sponsored Content if the perf team wants the split — confirm (open question 2).
- `utm_content` = `{asset_id}` — the Generation dept's per-asset identifier (from `creative_assets`/`ad_copy_drafts`), enabling per-creative attribution back to `asset_performance_feedback` per PLAN.md Step 17. Not derived from campaign name at all — this is the new field that today's scheme has no equivalent for.
- `utm_term` = left blank/unused for now (Google fills it automatically for auto-tagged search terms where applicable).

## 3. `campaign_registry` mapping table

Every campaign — old or new — must have exactly one row in `campaign_registry` before any Generation-dept asset can target it (enforced by the Step 2 validation helper; PLAN.md Step 2 "Done when"). Proposed backfill template for the perf team to fill/confirm:

| campaign_name (as-is, live) | campaign_id | platform | program | segment | geo | funnel | structured_name (new, only if created going forward) | notes |
|---|---|---|---|---|---|---|---|---|
| `IK-IN_Discovery_EdgeUp_InMarket_TechnologyIndustry_INDIA` | `16636249119` | google | edgeup | discovery | in | *(perf team to assign)* | — (legacy, not renamed) | example row from `collect_drilldown.py` docstring |
| *(one row per live Google campaign)* | | google | | | in | | | |
| *(one row per live LinkedIn campaign)* | | linkedin | | linkedin | in | | | |

Columns map directly to the `campaign_registry` Pydantic/BQ schema being defined in Step 1 (`campaign_name ↔ platform ↔ segment ↔ UTM ↔ ad ids`). `funnel` is a new column not derivable from any existing rule — the perf team assigns it manually during backfill (see Open Question 1).

**Rule:** any campaign name not present in `campaign_registry` is rejected by the validation helper — no substring-matching fallback. New campaigns must be registered (ideally at creation time, structured-name-compliant) before the generator, negative-keyword export, or dashboard will target them.

## 4. Migration policy

- **Existing live campaigns are NOT renamed.** Renaming a live Google/LinkedIn campaign resets the platform's learning phase (Smart Bidding relearns, LinkedIn's optimization resets) — a real ₹ cost with zero attribution benefit.
- Instead, every existing campaign gets **one explicit registry row** mapping its current free-text name to platform/segment/program/geo/funnel. This is a one-time backfill, done once, confirmed by the perf team (Section 3 table).
- **Only new campaigns, created after this convention is approved, must follow the structured format** in Section 2.1 and get registered at creation.
- The triplicated substring classifiers (`build_segment_tables.py`, `build_dashboard.py`, `calibrate_benchmarks.py`) are left untouched by this doc — a follow-on (out of scope here) could point all three at `campaign_registry` instead of re-deriving segment/program from name text, removing the drift risk described in Section 1. Flagged for a future step, not Phase 0.

## 5. Open questions for the perf team

> **ANSWERS (from PM, 2026-07-04):**
> 1. **Funnel token → audience-style vocabulary** already used in real campaign names (`inmarket`, `technologyindustry`-style), NOT tofu/mofu/bofu. Format becomes `{platform}_{program}_{segment}_{geo}_{audience}_{yyyymm}_{freeform}` with `audience` drawn from a small controlled list built from the terms already live in names (to be enumerated during backfill).
> 2. **utm_medium = `cpc`** for all LinkedIn campaign types. Confirmed.
> 3. **Geo = `in` only** — everything is India. Confirmed; single value for now.
> 4. **`other` program bucket is acceptable permanently**, with one requirement: the dashboard must let the team drill into what "Other" contains (list its campaigns), so it stays reviewable rather than becoming a junk drawer. → carried into Step 12 dashboard scope.
> 5. **Enforcement = WARN-FIRST** (PM decision 2026-07-04): unregistered campaigns are processed but classified "Other/unknown" and flagged loudly on the dashboard ("N unregistered campaigns, attribution degraded"). After 2–4 clean weekly cycles, flip to hard-block via config (`REGISTRY_ENFORCEMENT=block`), no code change.

1. **`funnel` stage** has no equivalent in any existing code — should it be `tofu`/`mofu`/`bofu`, or should we instead reuse the existing audience-targeting vocabulary seen in real names (`InMarket`, `TechnologyIndustry`) as the "funnel-ish" token instead of inventing TOFU/MOFU/BOFU?
2. **`utm_medium` for LinkedIn** — is `cpc` correct for all LinkedIn campaign types the team runs (Sponsored Content, Sponsored InMail, Thought Leader Ads), or does InMail warrant its own medium value (e.g. `paid-inmail`) for cleaner reporting splits?
3. **Geo scope beyond `in`** — is India the only geo today across both platforms, or are there any non-India campaigns live that need a second geo value now rather than later?
4. **Program `other` bucket** — `build_dashboard.py` maps anything not matching the five named programs to `"Other"`. During backfill, should ambiguous/legacy campaigns be forced into a real program value, or is `other` an acceptable permanent registry value?
5. **Enforcement timing** — should the validation helper (Step 2) hard-block any Google/LinkedIn API write for an unregistered name from day one, or start as a warning-only dashboard flag for a trial period before hard enforcement?
