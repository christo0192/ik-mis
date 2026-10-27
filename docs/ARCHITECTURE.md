# IK MIS — Marketing Intelligence System
## Architecture & Story — 2026-07-04

---

# PART I — TECHNICAL REFERENCE

## 1. What this system is

A **closed-loop ad-content system** for Interview Kickstart India, built as three loosely
coupled "departments" that communicate only through BigQuery — no message queues, no agent
framework, no shared runtime:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    GCP project: ik-marketing-data                       │
│                                                                         │
│  INTELLIGENCE            PERFORMANCE                GENERATION          │
│  (market research)       (PMIS / anomaly)           (ad content)        │
│  dataset:                datasets:                  dataset (interim):  │
│  Market_Research         India_Leads (RO),          SIP_pattern_rag.ce_*│
│                          SIP_pattern_rag.pmis_*     → Creative_Engine   │
└──────────┬────────────────────┬──────────────────────────┬─────────────┘
           │  signals            │  spend/leads/anomalies   │  assets
           └──────────► GENERATION conductor ◄──────────────┘
                             │ generate → gate → review → export
                             ▼
                    MISSION CONTROL DASHBOARD (Next.js / Vercel)
                    human approve/reject = the loop's steering wheel
```

**The loop:** market signals + ad performance → generate keywords/copy/angles → relevance
gate → human review (dashboard) → export to ad platforms → measure spend/leads → lead-quality
signal (CPQL) steers the next generation cycle.

**Two levers justify everything** (every module maps to one):
- **Lever A — cut wasted spend (₹):** negative keywords mined from rejected search terms;
  keyword↔copy alignment (Quality Score → lower CPC); (Phase 2+) creative-fatigue refresh.
- **Lever B — raise lead quality:** target the *intersection* of employer demand
  (job-market data) and learner conversation (social data); optimize **CPQL**
  (cost per quality lead), never raw CPL.

## 2. The three departments

### 2.1 Intelligence (repo: `ad-content-creator`, folder `Ad content generator`)
Plain-Python async collectors → Pydantic validation gate → BigQuery `Market_Research`
(5 tables: `social_trends`, `job_market_demand`, `competitor_intel`, `blog_publications`,
`executive_summary`). Sub-agents: Social / Blog / Job-Market / Competitor. Weekly cadence
(currently manual; Fly deploys pending). Budget governor caps paid scraping (Apify).

### 2.2 Performance / PMIS (repo: `google-ads-meta-ads-engine`, runtime `C:\mcp-servers\`)
Deterministic daily collectors (Google Ads GAQL, LinkedIn REST) + segment classification →
`pmis_daily_segment_facts`, `pmis_full_funnel`, `pmis_anomaly_facts` etc. One LLM judge
triages anomalies → Slack, 10:00 IST daily via Cloudflare-cron → Fly (`ik-anomaly`).
Ground truths: `India_Leads` (lead outcomes, READ-ONLY non-negotiable) and the "Swati Sheet"
(revenue; Google Sheets).

### 2.3 Generation (repo: `ik-mis`, folder `MIS`) — built in this project
| Module | Role | LLM? |
|---|---|---|
| `generation/schemas.py` | 12 Pydantic↔BQ lockstep table schemas | no |
| `generation/bq_writer.py` | **guarded writer**: dataset+table allowlist (`ce_*` only), `WriteTargetError` on anything else, `DRY_RUN` default true, atomic day-partition replace | no |
| `generation/collect_kw_perf.py` | Google Ads search-term/keyword feed → `ce_kw_search_term_perf` | no |
| `generation/build_campaign_registry.py` + `registry.py` | **attribution spine**: 294-campaign registry (Google GAQL + LinkedIn REST), name normalization (strips `__<id>` UTM suffix), WARN-first enforcement of unregistered campaigns | no |
| `generation/lead_quality.py` | segment-grain CPQL from `pmis_daily_segment_facts` (the join PMIS itself trusts) → `ce_lead_quality_scores` | no |
| `generation/generate_assets.py` (conductor) | digests 9 live sources → **one `claude -p` call per path** via brain files → Pydantic gate → length re-check → writes proposals | **yes** |
| `generation/taxonomy.py` + `gate_keywords.py` | relevance gate: deterministic fast-pass/fast-drop regex taxonomy; ambiguous middle → **one batched `claude -p` judge call**; routes relevant/borderline/irrelevant | **yes (partial)** |
| `generation/export_negatives.py` | ₹ waste baseline + Google Ads Editor negatives CSV (only taxonomy-"drop" verdicts may auto-negative) | no |
| `generation/instrument.py` | acceptance rates, filter rates, value-ledger rollups, 30/60/90 tracking | no |
| `generation/notify_slack.py` | post-run digest, notify-only, dashboard deep link | no |
| `generation/llm.py` | the `claude -p` subprocess wrapper (STDIN prompt, JSON extraction, retries, `ANTHROPIC_API_KEY` fallback, concurrency 1) | infra |
| `generation/brains/*.md` | persona/prompt contracts (see §4) | prompts |

### 2.4 Mission Control dashboard (`ik-mis/dashboard`, Next.js, Vercel)
Server-side BigQuery reads only; one hardened write surface (`/api/generation/review`:
fixed table/column/status allowlist, parameterized DML, fail-closed token auth).
Homepage = force-directed system graph (nodes = real pipelines/tables, color = live
freshness/health, click = navigate) + caretaker bot mascots (state-driven, homepage only)
+ vital-signs tiles. Pages: Intelligence, Generation (review queue + drill-down
segment→campaign→asset), Registry, Runs, Performance. Honesty rules: "no data yet" and
"pending registry confirmation" states — never fabricated zeros.

## 3. Scheduling & infrastructure

| Thing | Where | Detail |
|---|---|---|
| Daily Generation run | Cloudflare Worker `ik-ad-generator-trigger`, cron `30 5 * * *` UTC (11:00 IST) | starts Fly machine via Machines API |
| Generation runtime | Fly app `ik-ad-generator` (region sin, `restart=never`, 1GB) | `run_daily.sh` fail-fast chain: collect → lead_quality (Mon) → generate → gate → export → instrument → notify |
| Performance daily run | Cloudflare cron 04:30 UTC → Fly `ik-anomaly` (pre-existing) | 10:00 IST, one hour before Generation |
| Dashboard | Vercel (from `ik-mis`, root dir `dashboard/`) | deployment protection = the auth perimeter |
| Secrets | Fly secrets / Vercel env / Cloudflare worker secrets | `CLAUDE_CODE_OAUTH_TOKEN` (proven on Fly, survives restarts), BQ SA b64, Google-Ads env, Slack env |
| Safety rails | guarded writer allowlist; `DRY_RUN` default; India_Leads read-only; human approval gates everything; rollback = stop cron + `DROP TABLE ce_*` | |

## 4. WHERE THE LLMs ARE — the complete `claude -p` map

There is **no agent framework at runtime**. Every LLM use is a single headless
`claude -p` subprocess call (Claude Code CLI, OAuth token auth on Fly, API-key fallback),
fed a **persona file ("brain") + a security-fenced data digest**, returning strict JSON
that is Pydantic-validated (invalid rows dropped, never repaired).

| # | Call site | Brain / prompt | Model | Frequency | Purpose |
|---|---|---|---|---|---|
| 1 | Intelligence: `market_research/shared/llm.py` ← social/blog/competitor agents | inline prompts | Haiku | weekly, several small calls | extract/classify scraped content |
| 2 | Intelligence: `exec_summary.py` | inline synth prompt | Sonnet | weekly, 1 call | cross-tab executive summary |
| 3 | Performance: anomaly engine daily judge | `performance_marketing_analyst.md` | **Opus** | daily, 1 call | anomaly triage narrative → Slack (the one "expensive judgment" call in the system) |
| 4 | Generation conductor, keyword path | `brains/keyword_strategist.md` | Sonnet | daily, 1 call | candidate Google keywords + negatives from signal digests (CPQL-over-volume directive) |
| 5 | Generation conductor, copy/angle path | `brains/ad_copy_strategist.md` | Sonnet (prod stopgap: Haiku via `GENERATION_SYNTH_MODEL` — sonnet exceeded the 600s timeout at live registry scale; fix queued) | daily, 1 call (emits copy + angles together) | RSA headlines/descriptions + LinkedIn copy + audience angles, brand-guardrailed |
| 6 | Relevance gate, ambiguous middle only | `brains/keyword_relevance_filter.md` | Sonnet | daily, 1 batched call | verdict relevant/borderline/irrelevant + score + reason; injection-suspect → irrelevant |

**Total steady-state cost shape:** ~3 LLM calls/day for Generation + 1 Opus/day for
Performance + a weekly Intelligence batch. Everything else — collectors, registry,
CPQL math, waste baseline, exports, instrumentation, notifications, the entire
dashboard — is deterministic Python/TypeScript with zero LLM involvement.

**Prompt-injection defense (matters because live Google search terms — public,
user-typed text — flow into digests):** every brain carries an untrusted-data hard rule;
digests are fenced with a security warning; the filter brain classifies instruction-like
keywords as `injection-suspect`/irrelevant (live-tested: a "ignore all previous
instructions" keyword was quarantined at score 0.05). The filter deliberately returns
only join-keys + verdict fields so the critic *cannot rewrite* candidates, only judge them.

**Dev-time vs run-time:** during *development*, this system was built by an orchestrator
model (Fable) driving Sonnet worker/reviewer subagents — but none of that exists at
runtime. What runs in production is only the table above.

## 5. Data contracts & key decisions (with reasons)

- **BigQuery as the bus, no LangGraph/framework** — lowest coupling; each dept deploys/fails
  independently; the pattern was already proven by the two existing pipelines.
- **North-star metric = CPQL + value ledger** — optimizing raw CPL would reward cheap junk
  leads; "quality" today = survived sales-team triage (not Junk/Unsuitable); Phase 3
  upgrades to actual-sale truth (Swati Sheet). Every pipeline action logs expected +
  realized ₹ impact (`ce_value_ledger`) so the org sees an audited P&L line.
- **`campaign_registry` attribution spine** — free-text campaign-name matching was verified
  broken (0/148 campaigns joined spend↔leads; lead-side names carry a `__<id>` suffix).
  The registry + name normalization is the durable fix; WARN-first enforcement, flip to
  hard-block via one env var after clean weeks.
- **Generate → gate → human** — the strategist proposes, the deterministic taxonomy
  fast-passes/drops the obvious, the LLM judge handles only the ambiguous middle
  (cost control), borderline always goes to a human, drops are logged and become
  negative-keyword candidates (waste → value).
- **Meta deferred; Phase 2 (creatives) parked; Phase 3 closes the loop on text assets
  first** — all user decisions, recorded in PLAN.md.
- **Autonomy ladder:** assist (now) → gated auto (Phase 4: bounded actions, >80% sustained
  acceptance gate, ≤20% budget-step cap, full audit trail). Nothing launches without an
  approval status today.

## 6. Repos, environments, credentials

| Repo (private) | Home | Contents |
|---|---|---|
| `christo0192/ik-mis` | `C:\Users\Admin\Claude projects\MIS` | Generation dept, dashboard, plan, docs, deploy configs |
| `christo0192/ad-content-creator` | `C:\Users\Admin\Claude projects\Ad content generator` | Intelligence dept (dashboard superseded → `legacy-dashboard` branch) |
| `christo0192/google-ads-meta-ads-engine` | `C:\mcp-servers\anomaly-engine` | Performance dept |

Rule: **change code where it deploys**; `ik-mis` is the system of record for anything
cross-cutting. Dev machine is Windows + WSL2; Google-Ads creds at
`/mnt/c/mcp-servers/google-ads/.env` (`IK_MCP_DIR` override; WSL fallback in code);
BQ SA keys under `C:\Users\Admin\.gcp-keys\`.

---

# PART II — THE STORY

## The company with three brains that never spoke

Interview Kickstart India had, without quite planning it, grown three separate nervous
systems. One brain read the market: every week it combed job postings, GitHub trends, and
competitor blogs, learning that "Agentic AI Engineer" postings were doubling and that a
prompt-engineering repo had 164,000 stars. Another brain watched the money: every morning
at ten, it pulled ad spend and lead counts, muttered about anomalies into Slack, and knew
— to the rupee — that a quality lead from LinkedIn cost ₹1,152 while the same lead from a
Google campaign cost three times more. And the third brain didn't exist yet: the one that
would *act* on what the other two knew.

Because here was the absurdity. The market brain knew exactly which skills employers were
desperate for. The money brain knew exactly which campaigns burned budget on junk. And the
ads themselves? Written by hand, on intuition, whenever someone found the time. The
company's smartest signals were landing in dashboards nobody had time to read, while its
ad spend quietly leaked — ₹3,667 of it, in one three-day window, on people searching for
*free interview questions PDF*, who were never going to buy anything.

## The plan: don't build a robot, build a department

The temptation was to bolt on some grand AI framework — agents talking to agents, a
conductor waving at an orchestra of models. The team chose something more boring and much
stronger: a third *department*, staffed mostly by ordinary Python, that reads what the
other two departments publish to a shared BigQuery warehouse, thinks briefly and cheaply
(three focused LLM calls a day, each with one job and one persona), and publishes its
proposals right back — where a human, not a model, holds the only pen that approves.

But before the new department could think, someone had to fix the plumbing — and the
plumbing had opinions. The campaign names that were supposed to link "money spent" to
"leads earned" turned out to link *nothing*: zero of 148 campaigns joined, because one
system wrote `...AgenticAI_35-54` and the other wrote `...AgenticAI_35-54__23333103542`.
The LinkedIn API had been silently hiding 75 of the company's 175 campaigns behind a
pagination quirk for who knows how long. Even the taxonomy that decided "is this keyword
relevant?" nearly shipped with a rule that flagged *"crack the coding interview"* — the
single most buyer-intent phrase in Indian tech English — as piracy. Each of these was
caught, not by luck, but by the project's stubborn habit: verify everything against
reality before trusting it, and let a reviewer try to break every piece before it ships.

## What a day looks like now

At 10:00 IST, the money brain runs, as it always has. At 11:00, a tiny Cloudflare timer
nudges a sleeping machine awake in Singapore. It pulls yesterday's search terms — all ten
thousand of them — and recomputes what a quality lead actually costs, segment by segment.
Once a week (Mondays IST, or on any day the registry's latest snapshot is more than a week
old) it also re-lists every live campaign and writes a fresh, *complete* registry
snapshot: new campaigns auto-classified, renames and status changes picked up, hand-made
classifications carried forward, and any platform it couldn't reach carried over
unchanged rather than dropped. That step is the one that's allowed to fail without
stopping the day — last week's snapshot is still whole, so the day's generation goes on —
but never silently: a failure, or a platform it had to carry over, is flagged in the run
log and in the Slack digest. (Until 2026-10-27
the registry was a one-shot July backfill that never refreshed.) Then it wakes its
strategist: one model
call that reads the week's market signals and the quality-lead math, and proposes keywords
like `"ai engineer interview preparation course"` — each one carrying a citation, like a
scientist forced to show sources: *"62 postings, 16 flagged emerging."* A second call
drafts headlines and LinkedIn copy, hard-forbidden from using a single unverified claim —
no invented salary stats, no "guaranteed success," ever. A gatekeeper — regex first, model
judge only for the genuinely ambiguous — sorts every keyword into relevant, borderline
(a human will decide), or rejected; and the rejects aren't waste, they're *ammunition*,
exported as negative keywords that stop the next rupee from chasing another free-PDF hunter.

Then the machine posts one modest Slack message — "generated, gated, waiting for you" —
and goes back to sleep. Total thinking cost: about three model calls.

The human's day starts at a dark dashboard where the whole system is drawn as a living
constellation — every pipeline a node, every node breathing green or sulking amber, tiny
caretaker bots pottering around their stations, pulling out a wrench when something goes
stale. One click on any node and you fall through the layers: segment, campaign, down to a
single headline and the exact market signal that inspired it. Approve. Reject. That
click — recorded, audited, measured — is the steering wheel of the entire loop.

## Why this design will compound

Most automation projects die one of two deaths: they're clever but unaccountable (nobody
trusts them with money), or they're safe but pointless (they generate things nobody uses).
This system was built against both. Every generated asset must cite its source signal or
it's invalid. Every rupee of claimed value lands in a ledger with "expected" and
"realized" columns — the org doesn't have to believe the pipeline, it can audit it. And
autonomy is earned, not assumed: only when humans have approved more than 80% of proposals
for weeks does the system graduate to executing small, capped, reversible actions itself.

The flywheel is simple to say and hard to fake: *know the market, know the money, generate
only what both justify, let humans steer, measure everything, feed it back.* Three brains,
finally talking. The fourth brain — the one that approves — stays human. That's not a
limitation. That's the design.

---
*Written 2026-07-04 at the completion of Phase 1. Plan: `PLAN.md`. Dashboard vision:
`docs/dashboard-vision.md`. Naming/attribution: `docs/campaign-naming-convention.md`.*
