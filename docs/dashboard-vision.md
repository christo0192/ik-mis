# MIS Dashboard — Product Vision (from PM, 2026-07-04)

Governs PLAN.md Step 12. The existing market-research dashboard (`market_research_dashboard/`,
Next.js) is NOT extended as-is — it gets **restructured** into the MIS Mission Control.
Market research currently occupies the whole dashboard; it becomes one section among several.

## 1. Core feel

"Automated AI-level feel" — the dashboard should read as a living, self-running system,
not a static report. Dark, premium, motion-aware. No dashboard-template genericness.

## 2. Homepage ("Home Dashboard")

- **Center: an Obsidian-graph-style animated visualization** — a force-directed node graph,
  gently moving/breathing, as the centerpiece.
  - **Make it functional, not decorative**: nodes = the real system map (3 departments →
    their pipelines → key BigQuery tables/outputs). Edges = actual data flow
    (Intelligence → Generator → Review → Export → Performance → lead-quality → back to Generator:
    the closed loop, drawn as the loop it is).
  - Node state = live health: green/pulsing = ran on schedule + fresh data; amber = stale;
    red = last run failed. Health derives from `generation_runs`, `MAX(run_date)`/`MAX(date)`
    freshness queries per table, and (later) Fly machine run status.
  - Clicking a node navigates to that pipeline's page (see §3) — the graph IS the nav.
- **Around the graph: the vital signs of the whole MIS** (small, calm tiles — not a wall of numbers):
  - Pipeline health summary (N healthy / N stale / N failed)
  - Value ledger headline: waste eliminated ₹, CPQL trend, quality-lead volume (30d)
  - Review queue pressure: items awaiting human review (keywords borderline, drafts proposed)
  - Unregistered-campaigns warning count (warn-first enforcement)
- Every pipeline/department visible on this one page, each with its own zone/area.

## 2b. Simplicity rule + pipeline caretaker bots (PM additions, 2026-07-04)

- **Easy to use beats impressive.** The futuristic feel comes from motion, the graph, and
  restraint — NOT from density. Anyone should understand the homepage in 10 seconds:
  what's healthy, what needs review, what's the value being produced. If a decorative
  element competes with comprehension, the decoration loses.
- **Caretaker bots (homepage only):** each pipeline zone/node gets a small animated AI bot
  mascot that gives the "little agents are taking care of this" feel. Bots are state-driven,
  not random:
  - pipeline healthy → bot idles/patrols contentedly around its pipeline
  - run currently executing → bot "working" (busy animation at the node)
  - data stale / run failed → bot investigating with a wrench-out "fixing it" animation,
    and clicking the bot goes to that pipeline's run detail (the bot IS the alert)
  - review queue piling up → bot carries items toward the review area / nudges
- Bots appear ONLY on the homepage. Subpages stay clean and functional.
- All motion (graph + bots) honors `prefers-reduced-motion` (bots freeze to static icons,
  health still readable by color/badge). Performance budget: animations must never make
  the data slow.

## 3. One page per pipeline (outputs + review live together)

Separate pages, reached from the graph or a slim sidebar:
- **Intelligence** (restructured existing market-research views — condensed, not deleted)
- **Performance** (segment funnel, anomalies surface — reads existing pmis_ tables)
- **Generation** (review queue: keywords w/ verdicts + cited signals, copy drafts, angles;
  approve/reject writes back; rejects/negatives tab + CSV export download)
- **Registry & attribution** (campaign_registry browser, needs_attention queue,
  "Other" bucket drill-down — PM requirement from naming Q4, unregistered warnings)
- **Runs & health** (generation_runs, filter pass rates, schedule status)

## 4. Interaction model: click-through drill-down, never navigate-and-hunt

Everything clickable resolves to its underlying detail, in a flowing hierarchy:

```
segment (NB / Brand / Discovery / LinkedIn)
  └─ click → campaigns in that segment (spend, leads, CPQL when registry lands)
       └─ click → campaign detail: platform ID, registry row, keywords/search terms,
                  ad/creative level (RSA assets, copy drafts tied via asset_id)
                    └─ click → asset detail: text, grounding signal, review status/history
```

Implementation shape: URL-addressable levels (e.g. `/g/nb` → `/g/nb/<campaign_id>` →
`/g/nb/<campaign_id>/<asset_id>`) with breadcrumbs, so "click deeper" is also linkable/shareable.
Same pattern on every page — click a metric tile → the rows behind it; click a run → its outputs;
click a warning count → the offending campaigns.

## 5. Data-reality constraints (so the UI never lies)

- Per-campaign CPQL is display-gated on registry confirmation (today only segment-level CPQL
  is trustworthy; campaign level unlocks when the backfill is confirmed + join re-derived).
  Show "pending registry confirmation" states honestly rather than zeros.
- All reads server-side from BigQuery (existing pattern); approve/reject writes go through
  a server route → guarded writer (status columns only).

## 6. Build sequencing (unchanged)

Step 12 builds this after Step 9 (conductor) + Step 10 (gate) produce real reviewable data.
The restructure of the existing market-research pages happens as part of Step 12, not before.
Design skills to apply at build time: frontend-design / ui-ux-pro-max grade polish; the
graph animation must be performant (canvas/WebGL, respects prefers-reduced-motion).
