# Feature Implementation Plan — IK India Closed-Loop Ad-Content Multi-Agent System (v2)

**Overall Progress:** `~15%` (Phase 0 largely built; live-write verifications + user-owned actions pending)

**Status log:**
- 2026-07-04 00:45 IST — Step 1 BUILT+VERIFIED (12 lockstep schemas, guarded writer, smoke test green incl. WriteTargetError on India_Leads/Market_Research; live BQ write still pending → do with first real run). Step 3 BUILT+VERIFIED (collector pulled 10,855 real rows for 2026-07-02 via live Google Ads read; DRY_RUN preview correct; live BQ landing pending). Step 4 scaffold DONE (`spikes/claude-p-fly/`; user: setup-token + fly deploy). Step 5 DONE (`brand-profile.json`; user: review 4 flagged stats). Step 2 convention doc DRAFTED (`docs/campaign-naming-convention.md`; user: perf-team review; registry backfill after). Code review passed (1 blocker fixed: .gitignore; all 7 should-fixes + nits applied; ruff clean). Next: Steps 7 (lead-quality signal) + 8 (brains); then 9-10.

**v2 (2026-07-03):** amended after code-verified review. Changes: Meta deferred out of current scope (Google + LinkedIn focus); universal dashboard is the single review/approve/reject anchor (Slack is notify-only); new attribution spine (campaign/UTM registry) fixes the brittle `campaign_name` join; lead-quality signal (CPQL) pulled forward into Phase 1 as a read-only input; negative-keyword export promoted to first shipped artifact; value ledger instrumentation added; `claude -p`-on-Fly de-risk spike added; Step 2 collector wording corrected (direct GAQL, not MCP tool calls); relevance-gate taxonomy rebuild scoped explicitly.

## TLDR

Connect three "departments" — **Intelligence** (existing market-research pipeline), **Performance** (existing Google/LinkedIn anomaly pipeline), and a **new Generation** department — into a closed loop where market intelligence + ad performance drive generation of new keywords, copy, and creatives; reviewed in a **single universal dashboard**, launched, measured, and fed back. The system is justified by exactly two levers and every step maps to one of them:

- **Lever A — cut wasted ad spend (₹):** negative keywords mined from the relevance gate's rejects; keyword↔copy alignment (Quality Score → lower CPC); fatigue-triggered creative refresh (Phase 2+).
- **Lever B — raise lead quality:** generate keywords/copy from the *intersection* of employer demand (`job_market_demand`) and learner conversation (`social_trends`) — targeting serious career-switchers, not curiosity clickers; optimize **CPQL (cost per quality lead)** from day one, never raw CPL.

**30/60/90 value milestones:** 30 days = search-term waste eliminated (pure ₹ proof, negative-keyword export); 60 days = demand-grounded keyword/copy launches measured on CPQL; 90 days = full review→launch→feedback loop on at least one campaign.

## Architecture at a glance

- **Three departments, one BigQuery bus.** All in GCP project `ik-marketing-data`; the new Generation dept reads/writes a new `Creative_Engine` dataset (interim: `ce_`-prefixed tables in `SIP_pattern_rag`).
- **Attribution spine:** a `campaign_registry` table + enforced campaign/UTM naming convention replaces reliance on free-text `campaign_name` substring matching (verified brittle: segments are classified via `"brand" in name`-style checks today).
- **One net-new coordinator:** a thin scheduled-Python "conductor" on the existing Cloudflare-cron → Fly pattern. **No LangGraph / agent framework.** (hermes-agent evaluated 2026-07-03: rejected — stateful/self-modifying by design, duplicates `claude -p` + cron + subagents; revisit only as a post-Phase-4 conversational ops layer.)
- **The keyword path is generate → filter → pass.** Candidates from `social_trends` + `job_market_demand` (+ `kw_search_term_perf` + `lead_quality_scores`), gated by a tech/edtech relevance filter; only relevant keywords pass (for Google Ads).
- **Review anchor = the universal dashboard** ("Mission Control"): one place for approve/review/reject, rejects/negatives, run health, value ledger, CPQL. Slack posts are notifications with deep links — no Slack-side approval mechanics.
- **Platforms: Google + LinkedIn now. Meta is out of current scope** (ads not yet running); Meta enablement is a deferred Phase-2 spike.
- **Naming caution:** the folder `Ad content generator` is the *market-research* (Intelligence) system; `Google ads and meta ads` is the *performance/anomaly* (Performance) system with runtime at `/mnt/c/mcp-servers/`. The new Generation dept's code home: `/mnt/c/Users/Admin/Claude projects/MIS/`.
- **Loop:** Intelligence + Performance + lead-quality signal → generate assets → dashboard review → launch (export → later write-API) → measure → `asset_performance_feedback` → steers next generation.

## Critical Decisions

- **North-star metric = CPQL + the value ledger.** Generation optimizes cost-per-*quality*-lead (sale-vs-junk ratios joined read-only from India_Leads / Swati Sheet), never raw CPL — otherwise the pipeline "succeeds" by generating cheap junk. Every pipeline action (negative added, keyword launched, copy refreshed) is logged with expected + realized ₹ impact so the org sees an audited P&L line, not a cool system.
- **First milestone = platform-aware ad-asset generator (text only)** — reuses the proven `exec_summary.py` synth pattern (note: its cross-pipeline BQ read is partial — only the jobs tab reads BQ directly; the conductor's multi-dataset reads are net-new code following the same shape).
- **First shipped artifact = the negative-keyword export.** Rejects double as negative-keyword candidates; a Google Ads Editor CSV of negatives plus a waste-baseline query ("₹ spent last month on search terms the gate would reject") is the fastest, most provable ROI.
- **Keyword path = generate → filter (hybrid gate).** Deterministic pre-filter + `claude -p` judge for the ambiguous middle; borderline → human review (in the dashboard), never auto-dropped; drops logged to `keyword_rejects`. **The ads-domain taxonomy is a rebuild, not a reuse:** verified that `job_market_agent/classify.py`/`scoring.py` encode job-title/skills semantics — only the regex-dispatch *pattern* transfers; the INCLUDE/EXCLUDE vocabulary must be authored fresh for ad-keyword/search-intent semantics (seeded from the skills list + real search-term data).
- **Attribution spine before generation.** `campaign_registry` (campaign_name ↔ platform ↔ segment ↔ UTM ↔ ad ids) + a naming convention for all new campaigns, backfilled for live Google/LinkedIn campaigns. `utm_content`/ad-id ↔ `asset_id` mapping is in-scope Phase 1 schema (populated at launch time, Phase 3) — no longer a "future consideration."
- **Universal dashboard is the approval anchor.** Verified there is no Slack interactivity infrastructure anywhere (post-only). Instead of building Slack callbacks, the existing Next.js dashboard grows into Mission Control: review queue with approve/reject writing status back to BigQuery, rejects tab, run health, value ledger + CPQL tiles. Slack digest = notify + link.
- **BigQuery as the shared bus, not a framework** — lowest coupling; reuses the allowlisted guarded writer (verified: allowlist + `WriteTargetError` + `DRY_RUN`-default + atomic partition-replace all real) + Pydantic↔BQ lockstep schema pattern.
- **Google + LinkedIn now; Meta deferred.** Meta ads aren't running yet. The dormant `meta-ads` server (3 read-only tools, `$` currency hardcode, not in any live `.mcp.json`) is untouched until Meta spend begins — then a Phase-2 enablement spike (credentials, INR fix, adset-level tools, registry entries).
- **Creative tooling = Higgsfield-led** (Phase 2; scriptability spike required) with `generate_image.py` (Imagen/Gemini) as headless fallback.
- **Autonomy = assist, then gated auto** — human-approved through Phase 3; Phase 4 auto-executes only low-risk bounded actions behind approval + >80% acceptance gate + `pmis_action_audit`, ≤20% budget-step cap.
- **Mandatory brand/compliance gate** for creatives (Phase 2+); `brand_safe=false` never surfaces.
- **`claude -p` deployment risk is de-risked in Phase 0, not discovered in Phase 1.** The Intelligence dept's own TODO flags `claude -p` on Fly as riskiest/untested; a time-boxed spike (OAuth `setup-token` on a throwaway Fly machine + an `ANTHROPIC_API_KEY` fallback path in the shared LLM wrapper) settles the pattern for both the pending Intelligence deploys and the new `ik-ad-generator` app. Concurrency stays 1 (fine for batch cadence on a dedicated machine).
- **Phases 2–4 are milestone steps, re-planned in detail at their gates.**

## Tasks

### Phase 0 — Foundation, attribution spine, de-risking (Google + LinkedIn)

- [ ] **Step 1: Provision the `Creative_Engine` data layer** [Complex]
  - Depends on: Independent
  - Risk: Medium — dataset creation needs the data team; schema/writer must never be able to touch `India_Leads`.
  - [ ] Request a dedicated `Creative_Engine` dataset from the data team; interim-home tables as `ce_`-prefixed in the already-writable `SIP_pattern_rag` (mirrors the `pmis_` decision).
  - [ ] Clone `/mnt/c/mcp-servers/anomaly-engine/bq_writer.py` into a new guarded writer whose allowlist = the new tables only (keep `DRY_RUN` default + atomic partition-replace + `WriteTargetError`).
  - [ ] Define Pydantic + BQ schemas (lockstep, pattern from `market_research/shared/schemas.py`) for: `campaign_registry`, `keyword_candidates` (incl. `relevance_verdict` relevant|borderline|irrelevant, `relevance_score`, `filter_reason`, `kind` new|negative, `status`), `keyword_rejects`, `ad_copy_drafts`, `audience_angles`, `creative_briefs`, `creative_assets`, `asset_performance_feedback` (incl. `utm_content`/ad-id ↔ `asset_id` fields), `generation_runs`, `kw_search_term_perf`, `lead_quality_scores`, `value_ledger` (action, expected_impact_inr, realized_impact_inr, status). All partitioned on `run_date` (registry: snapshot), all carry `campaign_name`/`segment`.
  - Done when: a test row written in `DRY_RUN` produces a correct `staging/bq_preview_*.json`, then a live write lands in BigQuery and re-queries identically; a write targeting any other dataset raises `WriteTargetError`.

- [ ] **Step 2: Campaign naming convention + `campaign_registry` (attribution spine)** [Medium]
  - Depends on: Step 1
  - Risk: Medium — the whole closed loop's attribution hangs on this; today's segment classification is substring matching on free-text names (verified brittle).
  - [ ] Define the campaign/UTM naming convention (platform, program/segment, geo, funnel stage) with the perf team; document it.
  - [ ] Backfill `campaign_registry` for all live Google + LinkedIn campaigns (map existing free-text names → segments once, explicitly).
  - [ ] Provide a validation helper (used by future generators/exports) that refuses assets targeting unregistered campaign names.
  - Done when: every live Google/LinkedIn campaign resolves through the registry (no substring fallback needed), and an unregistered name is rejected by the helper.

- [ ] **Step 3: Build the persisted keyword/search-term performance feed** [Medium]
  - Depends on: Step 1
  - Risk: Low — read-only pulls; proven pattern.
  - [ ] New collector following the `/mnt/c/mcp-servers/anomaly-engine/collect_drilldown.py` pattern — direct `GoogleAdsClient` GAQL against `search_term_view`, `keyword_view`, `ad_group_ad` (note: `collect_drilldown.py` does *not* call the google-ads MCP tools; it runs its own GAQL — clone that approach).
  - [ ] Land results into `kw_search_term_perf` (campaign_name, date, keyword/search_term, match_type, impressions, clicks, ctr, avg_cpc, quality_score, cost) via the guarded writer.
  - Done when: `SELECT * FROM kw_search_term_perf WHERE date = (SELECT MAX(date) ...)` returns yesterday's rows for live Google campaigns.

- [ ] **Step 4: `claude -p` deployment de-risk spike** [Spike Needed]
  - Depends on: Independent
  - Risk: Medium — flagged "riskiest" by the Intelligence dept's own TODO; untested on Fly; blocks both the pending Intelligence deploys and the future `ik-ad-generator`.
  - [ ] SPIKE (time-boxed): deploy a minimal Fly machine that runs one `claude -p` call via `claude setup-token` OAuth; verify token persistence across machine restarts.
  - [ ] Add an `ANTHROPIC_API_KEY` fallback path to the shared LLM wrapper (`market_research/shared/llm.py` pattern) so either auth mode works; document the winning pattern.
  - Done when: a scheduled Fly run completes an LLM call unattended with the chosen auth mode, and the fallback path is tested locally.

- [ ] **Step 5: Generate the IK brand profile** [Simple]
  - Depends on: Independent
  - Risk: Low — one-time capture via existing `/ads dna` flow.
  - [ ] Run the `/ads dna` flow against interviewkickstart.com → `brand-profile.json` (colors, tone, claims guardrails, do/don't).
  - Done when: `brand-profile.json` exists and is reviewed for accuracy.

- [ ] **Step 6: Confirm Intelligence dept is on a live cadence (freshness prereq)** [Medium]
  - Depends on: Step 4
  - Risk: Medium — three pending deploys (Vercel + 2 Fly), gated on the Step 4 auth pattern (verified: everything is manual-only today; Apify token done, Claude token pending).
  - [ ] Finish the pending Vercel + 2 Fly deploys so `Market_Research.*` refreshes on schedule.
  - Done when: `SELECT MAX(run_date) FROM Market_Research.executive_summary` is within the expected weekly window without a manual run.

### Phase 1 — Ad-Asset Generator (text only, Google + LinkedIn, dashboard-anchored review)

- [ ] **Step 7: Lead-quality signal (CPQL) — read-only pull-forward** [Medium]
  - Depends on: Step 1, Step 2
  - Risk: Medium — India_Leads is **read-only, non-negotiable**; Swati Sheet is the revenue truth; join granularity may be campaign-level only at first.
  - [ ] Read-only job joining lead outcomes (Sale / Junk / Unsuitable from India_Leads + Swati Sheet via google-sheets MCP) to campaigns/segments through `campaign_registry` → write `lead_quality_scores` (campaign, segment, period, leads, quality_leads, quality_ratio, cpql).
  - [ ] Expose it as generator context: which segments/keywords/angles produce quality leads vs junk.
  - Done when: `lead_quality_scores` has rows for live campaigns and CPQL per segment is queryable; zero writes touch any source dataset.

- [ ] **Step 8: Author the generator + filter "brains"** [Medium]
  - Depends on: Step 5
  - Risk: Low — prompt/persona files; no runtime risk.
  - [ ] Clone `/mnt/c/mcp-servers/anomaly-engine/performance_marketing_analyst.md` into `keyword_strategist.md` and `ad_copy_strategist.md` (+ audience-angle guidance), IK-specific, with per-platform output contracts (Google keywords/negatives/RSA; LinkedIn intro/audience). Brains must optimize for CPQL (quality-lead intersection: employer demand × learner conversation), not volume.
  - [ ] Author `keyword_relevance_filter.md` — the critic between generation and output: tech/edtech relevance rubric, verdict relevant|borderline|irrelevant + reason.
  - [ ] Every brain must require its output to cite the source signal (grounding).
  - Done when: a manual `claude -p` run of each brain on sample inputs returns schema-valid JSON; the filter brain correctly labels a mixed tech/non-tech sample.

- [ ] **Step 9: Build the generator conductor (candidate generation)** [Complex]
  - Depends on: Step 1, Step 3, Step 7, Step 8
  - Risk: Medium — cross-dataset reads + LLM synth + validation. Note: `exec_summary.py` is the *shape* template (digest → one `claude -p` synth → per-row Pydantic gate, drop invalid) but its cross-pipeline BQ read is partial (only the jobs tab reads BQ; others are same-run in-memory) — the conductor's multi-dataset BQ reads are net-new code.
  - [ ] **Keyword path** reads `social_trends` + `job_market_demand` + `kw_search_term_perf` + `lead_quality_scores` → candidate Google keywords. **Copy/angle path** reads the broader set (`competitor_intel`, `blog_publications`, `executive_summary`, `pmis_full_funnel`, `pmis_daily_segment_facts`, `pmis_recommendations`).
  - [ ] Digest → one `claude -p` (Sonnet) synth per path via the Step 8 brains → Pydantic gate → write `ad_copy_drafts` + `audience_angles` (status `proposed`) and raw keyword candidates to staging (Step 10 gates them).
  - [ ] Reuse `market_research/shared/{llm.py,config.py}` patterns; scaffold the `governor.py` hook (no paid calls in Phase 1).
  - Done when: `DRY_RUN=true` previews are correct; a live run emits schema-valid copy/angles + grounded raw keyword candidates, each citing a source signal.

- [ ] **Step 10: Tech/edtech relevance filter (the gate agent)** [Complex]
  - Depends on: Step 9
  - Risk: Medium-High — gate precision/recall determines ad relevance; **and the taxonomy is a fresh build**: verified that `job_market_agent/classify.py`/`scoring.py` encode job-title semantics — reuse the regex-dispatch pattern and seed vocabulary from `scoring.py`'s 32 skills terms, but author the ads-domain INCLUDE/EXCLUDE lists new (from real search-term data + IK program catalog).
  - [ ] Build the ads-domain deterministic pre-filter (fast-pass obvious tech/edtech, fast-drop obvious off-topic).
  - [ ] `claude -p` relevance judge (via `keyword_relevance_filter.md`) adjudicates the ambiguous middle → verdict + reason + score.
  - [ ] Route by verdict: `relevant` → `keyword_candidates` (status `proposed`); `borderline` → flagged `needs_review` (surfaced in the dashboard review queue); `irrelevant` → `keyword_rejects` with reason (= negative-keyword candidates).
  - Done when: a curated mixed test set is gated correctly (no clearly non-tech/edtech keyword reaches `keyword_candidates`), every drop is logged with a reason, precision spot-checked.

- [ ] **Step 11: Negative-keyword export — the first shipped artifact** [Medium]
  - Depends on: Step 3, Step 10
  - Risk: Low — read + CSV; nothing writes to platforms.
  - [ ] Waste-baseline query: ₹ spent in the last 30/90 days on search terms the gate rejects → recorded in `value_ledger` as the recoverable-waste baseline (the pipeline's first proof number).
  - [ ] Google Ads Editor-compatible negative-keyword CSV from `keyword_rejects` (+ high-spend zero-quality search terms from `kw_search_term_perf` × `lead_quality_scores`), download surfaced in the dashboard.
  - Done when: the perf team uploads one export cleanly to Google Ads Editor, and the waste baseline (₹) is in `value_ledger`.

- [ ] **Step 12: Universal dashboard — "Mission Control" (the review anchor)** [Complex]
  - Depends on: Step 7, Step 10
  - Risk: Medium — extends the existing Next.js shell (`market_research_dashboard/`) but adds its first *write* path (approve/reject status → BigQuery through the guarded writer); verified no approval infrastructure exists anywhere today.
  - [ ] Add `Creative_Engine` queries to `src/lib/queries.ts` + new pages: **Review queue** (latest-`run_date` keywords with relevance verdict + grounding, copy drafts, angles; approve/reject controls persisting `status`), **Rejects/negatives** tab (+ export download), **Run health** (`generation_runs`), **Value tiles** (waste eliminated ₹, CPQL trend, quality-lead volume, acceptance rate).
  - [ ] One dashboard = the anchor for the whole project across all three departments; approve/reject writes via a server route → guarded writer (status columns only).
  - Done when: the perf team opens one dashboard, reviews the latest candidates with cited signals, approves/rejects (persists to BQ), sees rejects + value tiles.

- [ ] **Step 13: Slack notify digest (notify-only)** [Simple]
  - Depends on: Step 12
  - Risk: Low — post-only via existing `slack_post.py`; **no Slack-side approval mechanics** (dashboard is the anchor; deep link in every digest).
  - [ ] Per-run digest (counts by platform/segment/verdict + top items + dashboard link) posted after each generator run.
  - Done when: a generator run posts a readable digest whose link opens the dashboard review queue.

- [ ] **Step 14: Schedule the generator** [Medium]
  - Depends on: Step 4, Step 10
  - Risk: Medium — new Fly app + cron; uses the Step 4-proven auth pattern.
  - [ ] New `ik-ad-generator` Fly app + Cloudflare-Worker cron (pattern from `anomaly-engine/fly-deploy/` + `cloudflare-trigger/`), scheduled after Performance's 10 AM IST run, aligned to Intelligence's weekly cadence; `restart="never"`.
  - Done when: an unattended scheduled run generates → filters → writes drafts → posts the digest, no duplicates.

- [ ] **Step 15: Instrument the value ledger + acceptance tracking (the auto-exec gate metric)** [Medium]
  - Depends on: Step 11, Step 12
  - Risk: Low — logging/derived metrics only.
  - [ ] Per-run stats in `generation_runs` (incl. filter pass/drop rates); acceptance rate + edit-distance per asset type from review statuses.
  - [ ] `value_ledger` discipline: every pipeline action logged with expected ₹ impact at creation and realized ₹ impact when measured (negatives → spend delta on blocked terms; launched keywords → CPQL delta); weekly rollup feeds the dashboard tiles ("waste eliminated ₹X · CPQL −Y% · quality leads +Z%").
  - [ ] Track the 30/60/90 milestones against the ledger.
  - Done when: acceptance rate, filter pass-rate, and expected-vs-realized ₹ are queryable over time (the >80% Phase-4 gate and the org-facing value story are both measurable).

### Phases 2–4 — Milestone steps (re-plan in detail at each gate)

- [ ] **Step 16: Phase 2 — Higgsfield-led Creative Unit + Meta enablement** [Spike Needed]
  - Depends on: Step 15 + Phase-1 gate (drafts rated useful/adopted over 2–4 weeks)
  - Risk: High — greenfield creative gen; Higgsfield headless surface unverified; brand-safety critical.
  - [ ] SPIKE: Higgsfield scriptable/API vs interactive-only; fallback = human-triggered + `generate_image.py` headless.
  - [ ] **Meta enablement (deferred from Phase 0):** when Meta campaigns begin — Business app + system-user token; extend the dormant `meta-ads` server (currently 3 read-only tools, `$` hardcoded → fix to INR, add adset-level reads); wire into `.mcp.json`; add Meta campaigns to `campaign_registry`; Meta perf collector → BQ.
  - [ ] Then re-plan atomic steps: `creative_strategist` brain → `creative_briefs`; brand-grounded prompts → Higgsfield/`generate_image.py` → GCS + `creative_assets`; `format-adapter` + `claude -p` brand/compliance judge; dashboard `/creative` gallery; creative-specialist approval.
  - Done when: (defined at re-plan) an approved, on-brand, platform-sized asset set exists with brand_safe/format_valid flags and a pilot shows CTR/engagement uplift.

- [ ] **Step 17: Phase 3 — Feedback loop + export packagers** [Complex]
  - Depends on: Step 16
  - Risk: Medium — attribution granularity; export fidelity.
  - [ ] Re-plan atomic steps: `asset_performance_feedback` job (launched assets → Performance funnel + `lead_quality_scores` through `campaign_registry` + `utm_content`/ad-id ↔ `asset_id`); generators consume feedback (fatigue→refresh, winner→scale, junk-source→negative); export packagers (Google Ads Editor CSV / LinkedIn bundles; Meta when live); event trigger from Performance fatigue/negative recommendations.
  - Done when: (defined at re-plan) feedback rows label winner/neutral/loser correctly (on quality, not volume) for known campaigns, and an export uploads cleanly.

- [ ] **Step 18: Phase 4 — Bounded auto-execution** [Complex]
  - Depends on: Step 17 + acceptance rate sustained >80% + clean backtest
  - Risk: High — moves money; write-API scopes; blast radius.
  - [ ] Re-plan atomic steps: dashboard-anchored approve → bounded write-API actions (add negatives, pause fatigued ad, push approved variants) via google/linkedin (later meta) MCPs; every change → `pmis_action_audit` + `value_ledger`; ≤20% budget-step cap; net-new concepts stay human-gated.
  - Done when: (defined at re-plan) an approved bounded action executes in a test account, writes audit + ledger rows, reports outcome next run, and an over-cap action is refused.

## Rollback Plan

- **What to revert:** the new `ik-ad-generator` Fly app + its Cloudflare cron; the new collectors + lead-quality job; the generator conductor + filter agent; the dashboard Mission-Control pages (+ later `/creative`).
- **Data impact:** all new writes are confined to the `Creative_Engine` dataset (or `ce_`-prefixed tables); **no existing table is modified** (guarded writer forbids it; India_Leads/Swati Sheet are read-only). All new tables are additive and partitioned by `run_date`.
- **Rollback steps:** (1) disable the Cloudflare cron / stop the Fly app; (2) revert the dashboard pages + `queries.ts` additions; (3) if desired, drop the `ce_`/`Creative_Engine` tables (no downstream dependency); (4) Phase 4 only: revoke ad-platform write scopes. Existing Intelligence and Performance pipelines are untouched throughout.

## Definition of Done

- [ ] All Phase 0 + Phase 1 steps Done with acceptance criteria met (Phases 2–4 re-planned at their gates).
- [ ] Build/lint/tests pass for all new Python jobs and the dashboard.
- [ ] No debug artifacts, TODOs, or hardcoded secrets; all secrets via env/Fly secrets (no machine-specific path constants in new code — env-driven with sane defaults).
- [ ] Guarded writer verified: no write can reach any dataset outside the allowlist; `DRY_RUN` previews match live writes; India_Leads and all source datasets untouched.
- [ ] Attribution verified: every generated asset resolves through `campaign_registry`; unregistered targets are refused.
- [ ] Relevance filter verified: no clearly non-tech/edtech keyword reaches `keyword_candidates`; every drop logged with a reason.
- [ ] Grounding enforced: every generated asset cites a source signal.
- [ ] Human-in-the-loop verified: nothing launches without dashboard approval; brand/compliance gate withholds unsafe assets (Phase 2+).
- [ ] Value provable: waste baseline (₹) recorded; CPQL per segment queryable; value-ledger tiles live on the dashboard; 30/60/90 milestones tracked.
- [ ] Closed loop demonstrated end-to-end on at least one campaign by end of Phase 3.
- [ ] Documentation updated; code reviewed via /review.

## Future considerations (out of current scope)

- **Meta at parity:** once Meta spend is live and enabled (Step 16), bring it to full parity (perf feed, registry, generation targets, feedback).
- **Filter as a learning loop:** feed launched-keyword performance back to tune the relevance taxonomy (which "borderline" terms actually converted into quality leads).
- **Unified cross-dataset service account** once the loop stabilizes.
- **On-demand HTTP service** (`/run /jobs /health`) for "generate N variants for campaign X now" beyond batch cadence.
- **Conversational ops layer** (post-Phase 4, optional): if a chat interface over the pipeline is ever wanted, re-evaluate hermes-agent then — not before.
