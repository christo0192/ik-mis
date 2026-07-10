# Feature Implementation Plan — LinkedIn InMail + Conversation Ad copy

**Overall Progress:** `0%` (templates stored verbatim; nothing built)

> Sub-plan of the master `PLAN.md`. A **pre-Phase-3 copy-generation enhancement**. Ships DARK behind a flag (same rollout pattern as the Keyword Planner feature).

## TLDR

Replace the Generation dept's short LinkedIn **Sponsored Content** copy with two proven LinkedIn creative types generated to match the user's exact templates: **Message/InMail Ads** (Subject + body + `•` bullets + CTA + `Best, [Your Name]`) and **Conversation Ads** (branching `Step 1 → Yes/No → Step 2/3/4` flows). The templates are canonical — the brain fills only placeholders, never alters structure.

## Critical Decisions

- **Both formats, replacing Sponsored Content (user decisions):** LinkedIn copy becomes Message + Conversation ads only; the `headline≤70 / primary_text≤150-200` Sponsored Content contract and its length gate are removed for LinkedIn (Google RSA untouched).
- **Templates are verbatim canon:** stored at `generation/brains/linkedin_templates/{message_ads_examples.md,conversation_ads_examples.md}` (byte-identical to the source). The brain must reproduce their structure exactly and fill only `[First Name]`/`[Job Title]`/program/trivia/offer.
- **Row model — one draft = one whole creative:** a Message ad = one row (`subject` + `body` + `cta`); a Conversation ad = one row whose `body` holds the entire branching flow as markdown (exactly how the template is written). No new table, no per-step rows.
- **Schema: add nullable fields, make validation platform-conditional.** New `AdCopyDraftRow` fields: `linkedin_format` (`message`|`conversation`), `subject` (message only), `body` (long text; the full message/flow). `headline` becomes nullable; a model validator enforces: google→`headline` required, linkedin `message`→`subject`+`body` required, linkedin `conversation`→`body` required. Existing Google rows validate exactly as before.
- **Ship DARK behind `CONDUCTOR_ENABLE_LINKEDIN_INMAIL`:** flag off = today's Sponsored Content behaviour (byte-identical); flag on = InMail/Conversation. Flip after a verify run, per the Keyword Planner rollout.
- **BQ coupling (known landmine):** the new `ce_ad_copy_drafts` columns must be bootstrapped on live BigQuery BEFORE the dashboard's ad-copy query ships (the SELECT would otherwise fail the whole ad-copy view) — identical to the Keyword Planner column sequencing.
- **Brain sees the templates:** the copy conductor injects both verbatim template files into the LinkedIn prompt so the model matches them (single source of truth = the stored files, not a paraphrase inside the brain).

## Tasks

- [ ] **Step 1: Extend `AdCopyDraftRow` schema + platform-conditional validation** [Complex]
  - Depends on: Independent
  - Risk: Medium-High — changes a core schema + relaxes a currently-required field (`headline`); must not break existing Google rows.
  - [ ] Add nullable fields to `AdCopyDraftRow` + `AD_COPY_DRAFTS_BQ`: `linkedin_format` (STRING), `subject` (STRING), `body` (STRING).
  - [ ] Make `headline` `Optional`; replace the `headline` non-empty validator with a model-level `@model_validator` enforcing per-platform requirements (google→headline; linkedin message→subject+body; linkedin conversation→body). `asset_id`/`source_signal` stay required for all.
  - [ ] Update `smoke_test.py` SAMPLE_ROWS["ad_copy_drafts"] to keep a valid Google row + add a LinkedIn message and conversation sample; assert all round-trip.
  - Done when: a Google row (headline, no subject/body), a LinkedIn message row (subject+body, no headline), and a LinkedIn conversation row (body only) all validate + round-trip through DRY_RUN preview; a LinkedIn message row missing `subject` raises ValidationError.

- [ ] **Step 2: Bootstrap the new `ce_ad_copy_drafts` columns on live BigQuery** [Simple]
  - Depends on: Step 1
  - Risk: Low — additive NULLABLE `ALTER TABLE ADD COLUMN IF NOT EXISTS`; mirrors the Keyword Planner bootstrap.
  - [ ] `ALTER TABLE ce_ad_copy_drafts ADD COLUMN IF NOT EXISTS linkedin_format STRING, subject STRING, body STRING` via the guarded SA.
  - Done when: `bq` schema shows the 3 new columns; a live write of a LinkedIn row round-trips from BQ.

- [ ] **Step 3: Drop the LinkedIn length gate (keep Google RSA checks)** [Simple]
  - Depends on: Step 1
  - Risk: Low — removes an over-strict check that would reject all InMail/Conversation copy.
  - [ ] In `generate_assets._copy_length_violation`, keep the `platform=="google"` RSA checks; remove the `platform=="linkedin"` `primary_text>200 / headline>70` checks.
  - Done when: a long (>1KB) LinkedIn `body` passes `gate_copy_rows`; an over-limit Google RSA headline is still dropped.

- [ ] **Step 4: Rewrite the LinkedIn output contract in `ad_copy_strategist.md`** [Complex]
  - Depends on: Independent (prompt file; no runtime dep)
  - Risk: Medium — prompt quality determines fidelity to the templates.
  - [ ] Drop the Sponsored Content instructions (headline≤70 / intro≤150 / CTA-button). Add the LinkedIn contract: emit `message` and `conversation` rows following the provided templates EXACTLY — fill only placeholders, preserve every structural element (Subject line, `Hi [First Name],`, `•` bullets, Yes/No option lines, `Step N (…)` headers, `Best, [Your Name]`).
  - [ ] Update the brain's Output-format JSON contract: linkedin rows carry `linkedin_format`, `subject` (message), `body`, `cta`; `headline`/`primary_text`/`description` null. Every row still cites `source_signal` (grounding preserved).
  - Done when: a manual `claude -p` run of the brain on sample inputs returns schema-valid LinkedIn `message` and `conversation` rows whose structure matches the templates (subject/steps/bullets/signature present).

- [ ] **Step 5: Inject templates into the copy prompt + carry fields through the gate** [Medium]
  - Depends on: Step 1, Step 4
  - Risk: Medium — conductor wiring; must stay dark when the flag is off.
  - [ ] Behind `CONDUCTOR_ENABLE_LINKEDIN_INMAIL` (default false): in `run_copy_path`, load the two `linkedin_templates/*.md` files and append them to the copy prompt under a "## LinkedIn creative templates — follow EXACTLY" section; with the flag off, the prompt + Sponsored Content contract are unchanged.
  - [ ] Confirm `gate_copy_rows` constructs `AdCopyDraftRow(**obj)` so the new fields flow through; write `ad_copy_drafts` as today.
  - Done when: with the flag ON, a DRY_RUN copy run emits LinkedIn message+conversation drafts (with subject/body/format) to the `ad_copy_drafts` preview; with the flag OFF, the copy preview is byte-identical to today.

- [ ] **Step 6: Surface LinkedIn InMail/Conversation copy in the dashboard** [Medium]
  - Depends on: Step 2
  - Risk: Low — additive read-only fields on the existing ad-copy view.
  - [ ] Add `linkedin_format, subject, body` to the `getAdCopyDrafts` SELECT + `AdCopyDraftRow` TS interface + mapper in `dashboard/src/lib/ce-queries.ts`.
  - [ ] Render them in the copy tab of `dashboard/src/app/generation/page.tsx` — for LinkedIn rows show format badge + subject + the full body (pre-wrapped/scrollable); Google rows render as today.
  - Done when: the review queue's copy tab shows a LinkedIn message ad (subject+body) and a conversation ad (full flow) legibly; Google RSA rows unchanged.

- [ ] **Step 7: Tests + dark rollout** [Medium]
  - Depends on: Steps 1,3,4,5
  - Risk: Low-Medium — test coverage + first live LinkedIn output.
  - [ ] Conductor test: with the flag on + a mocked brain returning template-shaped LinkedIn rows, assert message+conversation rows validate and write; assert Google copy rows are unaffected; assert flag-off parity.
  - [ ] Deploy dark; one manual verify run with the flag on → confirm LinkedIn drafts match the templates in BQ + dashboard; then enable the flag on the cron.
  - Done when: all generation suites green; a flag-on manual run produces template-faithful LinkedIn drafts live; flag-off output identical to today.

## Rollback Plan

- **What to revert:** `CONDUCTOR_ENABLE_LINKEDIN_INMAIL=false` (instant — reverts to Sponsored Content); the brain rewrite; the conductor injection; the dashboard fields.
- **Data impact:** new columns are additive NULLABLE on `ce_ad_copy_drafts`; no existing row changes; LinkedIn drafts are confined to that table. Nothing to clean up beyond ignoring the columns.
- **Rollback steps:** (1) unset the flag + redeploy → Sponsored Content restored; (2) revert dashboard `queries.ts`/page; (3) leave nullable columns (harmless) or drop them.

## Definition of Done

- [ ] All steps Done with acceptance criteria met.
- [ ] Build/lint/tests pass (generation suites + dashboard typecheck).
- [ ] No debug artifacts/TODOs/hardcoded secrets.
- [ ] Flag-off parity verified: LinkedIn copy output identical to today when the flag is off; Google RSA copy unaffected in all cases.
- [ ] Template fidelity verified: generated Message + Conversation ads reproduce the stored templates' structure exactly (subject/steps/bullets/signature), differing only in filled placeholders.
- [ ] Grounding preserved: every LinkedIn draft cites a `source_signal`.
- [ ] BQ columns bootstrapped before the dashboard ships; dashboard shows the new fields.
- [ ] Documentation updated (README + master PLAN.md note); code reviewed via /code-review.

## Future considerations (out of current scope)

- **Structured conversation flow** (per-step rows / a `conversation_steps` child table) if the dashboard later needs to render/branch the flow interactively rather than as one markdown body.
- **Meta InMail-equivalent / other placements** — deferred with the rest of Meta (Phase 2).
- **Per-role template library growth** — the two examples (remarketing, EM/TPM leaders) can expand into a larger set of role-wise templates the brain selects from.
- **Character/format validators for LinkedIn** (e.g. subject length, bullet count) once LinkedIn's exact limits for these ad types are confirmed.
