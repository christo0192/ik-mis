# Feature Implementation Plan — Keyword Planner demand-grounding in the keyword path

**Overall Progress:** `~85%` (Steps 1–6 built + verified on branch `feat/keyword-planner-integration`; Step 7 live rollout is the deliberate remaining gate — live BQ column bootstrap, flag-on manual run, then cron/Vercel)

**Build status (2026-07-09):** Steps 1–6 DONE on branch `feat/keyword-planner-integration`. `keyword_planner.py` live-verified (real ideas, dedup, INR, fail-soft); schema + gate passthrough + conductor merge (per-segment seed → enrich + expand + flag) done; dashboard columns done; new `tests_keyword_planner.py` + smoke round-trip green; **all 9 generation suites pass in DRY_RUN; flag-off parity confirmed** (keyword path byte-identical with the flag off). Ships DARK (`CONDUCTOR_ENABLE_KEYWORD_PLANNER=false`). **Remaining = Step 7 (activation), which is coupled + deliberate:** (a) bootstrap the 7 nullable columns on live `ce_keyword_candidates` — the dashboard SELECT references them, so do this BEFORE any production/Vercel deploy; (b) flag-on manual run to verify live enrichment; (c) only then enable the cron flag + merge to main for Vercel.

> Sub-plan of the master `PLAN.md`. Slots in as a **keyword-path enhancement** — a Phase 1 fast-follow / front-half of Phase 3. Fold the finished step into `PLAN.md`'s task list as "Step 10.5 / Step 16-adjacent: Keyword Planner demand grounding" once shipped.

## TLDR

Add Google Keyword Planner (`KeywordPlanIdeaService.GenerateKeywordIdeas`) to the Generation dept's keyword path as **both** a candidate *source* (real-searched terms the AI missed) and an *enrichment* layer (real INR search-volume/competition/bid attached to every candidate). The existing AI generator + relevance gate + CPQL layer stays as the **curator** — because volume ≠ quality lead. Feasibility already proven live (3,960 real ideas from 5 seeds, real INR volumes/bids, India geo).

## Critical Decisions

- **Both source + enrich (user choice):** Planner expands the candidate pool AND attaches real volume to AI candidates. Not a swap — the AI/CPQL layer is what separates quality-lead keywords from high-volume junk (`data structures` = 40,500/mo but student/free-seeker traffic).
- **Flag zero-volume, never drop (user choice):** an AI candidate Planner reports at zero/low volume is tagged `no_planner_volume` and kept — preserves the emerging-demand-ahead-of-volume edge (Lever B). PMM judges intent at approval.
- **Dashboard surfacing is in scope (user choice):** the review queue shows `avg/mo · competition · ₹bid · source` next to every keyword — the payoff the PMM asked for.
- **Per-segment seeding** (resolves segment attribution): `GenerateKeywordIdeas` returns one merged list with no seed→idea mapping. So we call it **once per segment** (seeds = that segment's AI candidate texts + optional catalog seeds); every returned idea inherits that segment. Few segments → few calls → negligible quota. `keyword_candidates` requires `segment`, so this is load-bearing.
- **Reuse, don't rebuild:** same `collect_kw_perf._client_and_customer_id()` `GoogleAdsClient` + `GOOGLE_ADS_*` creds; direct API call (the "direct GAQL, not MCP tool" pattern the collector already uses). One new service call.
- **Graceful degradation is first-class:** this adds a live external API call to the scheduled run. If Planner errors/quota-limits, the run continues **AI-only**, logged as degraded — never crashes the chain (mirrors the conductor's existing `degraded` source pattern).
- **Ship dark behind a flag:** `CONDUCTOR_ENABLE_KEYWORD_PLANNER` (default `false`). Prove it on a manual run before the cron enables it.
- **Additive nullable schema only:** new `keyword_candidates` columns are all NULLABLE — no existing row/reader breaks; enrichment is purely additive.

## Tasks

- [ ] **Step 1: Harden the spike into `keyword_planner.py`** [Medium]
  - Depends on: Independent (spike already proved the call works)
  - Risk: Medium — live external API; quota, geo/lang params, error surface.
  - [ ] New `generation/keyword_planner.py` reusing `collect_kw_perf._client_and_customer_id()`. Function `fetch_ideas(seeds: list[str], *, geo, language, network) -> list[dict]` returning normalized dicts: `text, avg_monthly_searches, competition (LOW/MEDIUM/HIGH/UNKNOWN), competition_index, top_of_page_bid_low_inr, top_of_page_bid_high_inr`.
  - [ ] Env-driven params with India/English/GOOGLE_SEARCH defaults: `KWP_GEO_TARGET` (`geoTargetConstants/2356`), `KWP_LANGUAGE` (`languageConstants/1000`), `KWP_NETWORK`, `KWP_MAX_IDEAS` cap, `KWP_LOW_VOLUME_FLOOR` (for the low/zero tag, not a drop).
  - [ ] Normalization + intra-call dedup (Planner returns close variants like `data structures` / `data structures and`); collapse on a normalized key, keep the highest-volume representative.
  - [ ] Fail-soft: any Google Ads exception → return `([], reason)` so callers degrade, never raise into the chain. Redact credential fragments (reuse `collect_kw_perf._sanitize_error`).
  - Done when: `python -m generation.keyword_planner --seeds "system design interview"` prints real ideas with INR volumes against the live account, and a forced-error path returns `[]` + a reason instead of raising.

- [ ] **Step 2: Extend `keyword_candidates` schema + bootstrap live BQ columns** [Medium]
  - Depends on: Independent
  - Risk: Medium — touches the live BQ table; `bq_writer` can't do DDL, so the new columns must exist before the first enriched write.
  - [ ] Add to `KeywordCandidateRow` + `KEYWORD_CANDIDATES_BQ` (all NULLABLE): `avg_monthly_searches:int`, `competition:str`, `competition_index:int`, `top_of_page_bid_low_inr:float`, `top_of_page_bid_high_inr:float`, `keyword_source:str` (`ai`|`keyword_planner`|`both`), `demand_flag:str` (e.g. `no_planner_volume`).
  - [ ] Bootstrap the live columns once — prefer a one-time explicit `ALTER TABLE ... ADD COLUMN` bootstrap (matches the existing partitioned-table bootstrap workaround) over relying on load-job `ALLOW_FIELD_ADDITION`; document which was used.
  - [ ] Update `smoke_test.py` SAMPLE_ROWS["keyword_candidates"] to include the new fields and assert round-trip.
  - Done when: a DRY_RUN preview and a live write of a `keyword_candidates` row carrying all seven new fields round-trips identically from BQ; existing rows (null in the new columns) still read fine.

- [ ] **Step 3: Wire per-segment seeding + merge/enrich/expand into the conductor** [Complex]
  - Depends on: Step 1, Step 2
  - Risk: Medium-High — core keyword-path logic; merge correctness (volume attach, dedup vs AI candidates, new-source rows, zero-volume tagging).
  - [ ] After the AI keyword synth in `run_keyword_path`, group AI candidates by `segment`; for each segment call `keyword_planner.fetch_ideas(seeds=<that segment's candidate texts + optional catalog seeds>)`.
  - [ ] **Enrich:** match Planner ideas to AI candidates on a normalized key → attach volume/competition/bid; set `keyword_source="both"`. Unmatched AI candidates → `keyword_source="ai"`, `demand_flag="no_planner_volume"` if under the floor (kept, not dropped).
  - [ ] **Expand:** Planner ideas with no AI match become new candidate rows for that segment — `keyword_source="keyword_planner"`, `kind="new"`, synthetic `source_signal="keyword_planner: {avg_monthly_searches}/mo searches (India)"` (satisfies the grounding requirement), `match_type` left for the gate/brain default.
  - [ ] Optional `KWP_CATALOG_SEEDS` file (IK program catalog terms) merged into each segment's seed set.
  - [ ] Guard the whole block behind `CONDUCTOR_ENABLE_KEYWORD_PLANNER` (default off) and record `keyword_planner` as a digest source in the `generation_runs` degraded/notes list when it returns empty or errors.
  - Done when: with the flag on, a DRY_RUN keyword run shows AI candidates carrying volume, at least one Planner-only expansion candidate per non-empty segment with a grounded `source_signal`, and zero-volume AI candidates tagged (not dropped); with the flag off, output is byte-identical to today.

- [ ] **Step 4: Carry the new fields through the gate + write** [Medium]
  - Depends on: Step 3
  - Risk: Low-Medium — the gate already routes arbitrary candidate rows; just ensure the new fields survive to `keyword_candidates`.
  - [ ] Confirm `gate_keywords.py` passes `avg_monthly_searches/competition/.../keyword_source/demand_flag` through unchanged to `keyword_candidates` (they are carried, not gated on). Planner-only terms flow through the same relevance judge — a high-volume off-topic term still gets rejected → becomes a negative-keyword candidate.
  - [ ] Verify negatives/rejects path is unaffected (Planner-sourced rejects join `keyword_rejects` like any other).
  - Done when: a curated mixed run lands enriched `keyword_candidates` rows (verdict + volume both present), and a high-volume off-topic Planner term is correctly routed to `keyword_rejects`, not `keyword_candidates`.

- [ ] **Step 5: Surface volume/competition in the dashboard review queue** [Medium]
  - Depends on: Step 2 (schema)
  - Risk: Low — additive read-only columns; extends the existing review queue.
  - [ ] Add the new columns to the `keyword_candidates` select in `dashboard/src/lib/queries.ts`.
  - [ ] Render `avg/mo · competition · ₹bid range · source badge (AI / Planner / Both) · demand_flag` in the review queue row; sortable by `avg_monthly_searches`. Null volume renders as "—", not 0.
  - Done when: the PMM opens the review queue and sees real search volume + competition + ₹bid + source next to each keyword, sortable by volume, with AI-only/no-volume rows clearly marked.

- [ ] **Step 6: Tests** [Medium]
  - Depends on: Step 3, Step 4
  - Risk: Low — test-only; mock the Google Ads client like the suite mocks `llm.call`.
  - [ ] `tests_keyword_planner.py`: mapping/normalization/dedup + fail-soft (`[]` + reason on error), no live API.
  - [ ] Conductor merge tests: enrich-attach, zero-volume flag-not-drop, Planner-only expansion row shape + grounded `source_signal` + inherited segment, flag-off == identical output.
  - [ ] Schema round-trip for the seven new fields (extends `smoke_test.py`).
  - Done when: all generation suites (existing + new) pass in DRY_RUN via exit code; flag-off parity test green.

- [ ] **Step 7: Dark rollout + verification** [Medium]
  - Depends on: Steps 1–6
  - Risk: Medium — first live Planner call inside the scheduled chain; must not destabilize the daily run.
  - [ ] Deploy with `CONDUCTOR_ENABLE_KEYWORD_PLANNER=false`; confirm a scheduled run is unchanged.
  - [ ] Manually trigger one run with the flag on; verify live BQ `keyword_candidates` carries volume, expansion terms appear, degradation path logs cleanly if Planner hiccups, and the Slack digest + dashboard reflect it.
  - [ ] Flip the flag on in `fly.toml [env]` for the cron only after the manual run is clean.
  - Done when: one unattended scheduled run completes end-to-end with Planner enabled, real volumes in the queue, and no new failure mode vs. the AI-only baseline.

## Follow-up changes (2026-07-10) — topic routing + already-running gate

Two PMM-requested additions on the same branch, each **dark behind its own flag**:

- **Topic routing** (`CONDUCTOR_ENABLE_TOPIC_ROUTING`) — `keyword_routing.route_by_topic`: **AI-topic** keywords (ML/deep-learning + GenAI/LLM/agentic; NLP/CV/data-science deliberately excluded) route to the **EdgeUp** program's Coding-Interview search campaign; everything else → **LevelUp**'s. Campaign auto-resolved from `campaign_registry` (program + `CI` name token, Google platform), **env-pinnable** per program (`KWP_EDGEUP_CI_CAMPAIGN` / `KWP_LEVELUP_CI_CAMPAIGN`) after you verify the auto-picks. campaign_name + segment set together for registry consistency. Applies to all positive candidates (AI-generated + Planner expansion); negatives untouched.
- **Already-running gate** (`CONDUCTOR_ENABLE_RUNNING_KEYWORD_GATE`) — `keyword_routing.exclude_running` over `keyword_planner.fetch_active_keywords` (live `keyword_view`, ENABLED only): positive candidates we already bid on are excluded (count logged to `generation_runs.notes`). **Not** written to `keyword_rejects` — an already-running keyword must never become a negative. Fail-soft: if the pull errors, nothing is excluded (safe degradation).

New tests: `tests_keyword_routing.py` (AI classifier, campaign resolution + env pin, routing, exclusion, fail-soft) — all green; flag-off parity preserved.

## Rollback Plan

- **What to revert:** `CONDUCTOR_ENABLE_KEYWORD_PLANNER=false` (instant kill — reverts to today's AI-only path); the `keyword_planner.py` module; the conductor merge block; the dashboard columns.
- **Data impact:** all changes are additive NULLABLE columns on `keyword_candidates` — no existing data modified; Planner-sourced rows are normal candidates confined to the `ce_` tables. Nothing to clean up beyond optionally ignoring the new columns.
- **Rollback steps:** (1) set the flag off (or unset in `fly.toml`), redeploy — pipeline reverts immediately; (2) revert the dashboard `queries.ts` + review-page columns; (3) leave the nullable columns in place (harmless) or drop them if desired (no downstream dependency).

## Definition of Done

- [ ] All steps Done with acceptance criteria met.
- [ ] Build/lint/tests pass for the new module, conductor changes, and the dashboard.
- [ ] No debug artifacts/TODOs/hardcoded secrets; all Planner params env-driven with India/INR defaults.
- [ ] Graceful degradation verified: a Planner failure leaves the run AI-only and logged, never crashes the chain.
- [ ] Flag-off parity verified: with the flag off, keyword-path output is identical to today.
- [ ] Grounding preserved: every candidate (AI, Planner, or both) still cites a `source_signal`.
- [ ] PMM value visible: real volume/competition/₹bid + source shown in the review queue, sortable by volume.
- [ ] Documentation updated (README + fold the step into master `PLAN.md`); code reviewed via /review.

## Future considerations (out of current scope)

- **Volume-weighted CPQL scoring:** let the relevance/priority score factor real volume × quality-lead ratio (v1 only *carries* volume; it doesn't re-weight scoring).
- **Historical volume trend:** `GenerateKeywordHistoricalMetrics` for seasonality (e.g. hiring-season spikes) beyond the single avg.
- **URL/site seed:** seed Planner from interviewkickstart.com program pages in addition to AI candidates + catalog.
- **Waste-avoidance ledger line:** log "₹ not spent by declining zero-volume/off-topic high-volume terms" into `value_ledger` as an additional proof number.
- **LinkedIn parity:** Keyword Planner is Google-only; LinkedIn keeps the AI-only path (no equivalent demand API).
