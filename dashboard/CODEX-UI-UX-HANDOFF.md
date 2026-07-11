# UI/UX overhaul — handoff brief (for Codex)

This branch (`codex/ui-ux-overhaul`) is for **UI/UX work on the MIS dashboard only**.
The backend (generation pipeline, BigQuery writes, Fly cron) is out of scope and
should not be touched. Below is what the app is, what's safe to change, and the
few things that must not break.

## The app

**"Mission Control"** — the review/approval dashboard for IK's closed-loop
ad-content system. It reads generated ad assets from BigQuery and lets the team
review/approve/reject them. **Read-mostly**: the only write is approve/reject.

**Stack:** Next.js (App Router, React Server Components) · Tailwind (v4, tokens in
`src/app/globals.css`) · `recharts` (charts) · `d3-force` (system graph) ·
`lucide-react` (icons) · `@google-cloud/bigquery` (server-only data). Deploys on
Vercel from `main`. App lives in `dashboard/`.

**Run locally:** `cd dashboard && npm install && npm run dev` (npm install is slow
on WSL — give it time). `npm run lint` = eslint.

## Routes (all under `src/app/`)

- `/generation` — the main review queue. Tabs: **keywords · copy · angles · rejects**
  (`components/query-tabs.tsx`, `status-filter-chips.tsx`). Drill-downs:
  `/generation/[segment]` → `/[campaign]` → `/[assetId]` (asset detail).
- `/intelligence` (+ `/blog`, `/competitor`, `/jobs`, `/social`) — market-research views.
- `/performance`, `/registry`, `/runs` — perf metrics, campaign registry, run health.
- Homepage vision: an Obsidian-style live **system graph** (`components/system-graph.tsx`,
  `lib/graph.ts`) — a functional map of pipelines/tables. See `docs/dashboard-vision.md`.

## ✅ Safe to change (this is the UI/UX work)

- Everything visual: layout, spacing, typography, color, theme tokens in
  `globals.css`, `components/ui/*` (badge, card, table, misc, unavailable-banner),
  `sidebar-nav`, `breadcrumbs`, tabs, filter chips, charts styling, the system-graph
  visuals, empty/loading states, responsive behavior, accessibility, micro-interactions.
- Page markup/JSX (the `page.tsx` files) — restructure/polish freely.
- Add components, a design system, dark/light polish, etc.

## ⛔ Do NOT break

1. **The write path** — `src/app/api/generation/review/route.ts` is the ONLY write
   and is security-reviewed: a hard-allowlisted `UPDATE` that sets only
   `status/reviewed_by/reviewed_at`, with `checkAuth()` (same-origin / token). Do
   **not** change its auth, allowlist, or SQL. The `approve-reject-buttons.tsx`
   client component posts to it — keep that flow working.
2. **The server-only data layer** — `src/lib/*` (`bq.ts`, `ce-queries.ts`,
   `queries.ts`, `pmis-queries.ts`, `graph.ts`, `health.ts`). These carry
   `import "server-only"`; the SELECTs/row shapes are a contract with the backend.
   UI work shouldn't need to edit them — components already receive the fields they
   need. Don't add client-side BigQuery access.
3. **Preserve recent data renders** — the copy views render **LinkedIn
   InMail/Conversation** copy off `linkedin_format`/`subject`/`body` (Format badge +
   Subject + full Body); keyword views render Keyword-Planner demand columns
   (`Source`/`avg_monthly_searches`/`competition`/bid). Keep these fields shown
   (restyle freely, but don't drop them).
4. **RSC boundaries** — pages are Server Components that fetch via the data layer;
   keep `"use client"` limited to what needs interactivity (buttons, tabs, filters,
   graph). Don't turn data-fetching pages into client components.
5. **`safeQuery` degradation** — every query returns `{ rows, error }`; the UI must
   keep rendering an honest "no data yet" / `UnavailableBanner` state on error,
   never a 500 or a faked zero.

## Nice-to-haves already noted (optional)

- A **platform filter** (Google / LinkedIn) on the `/generation` copy tab — the
  LinkedIn drafts are a small slice among many Google rows.
- The system-graph homepage could use the most design love (it's the vision piece).

## Where data comes from (context, don't touch)

BigQuery `ik-marketing-data.SIP_pattern_rag.ce_*` tables (generation dept) +
`Market_Research.*` / `pmis_*` (intelligence/performance). All read-only from the
dashboard except the one review route above.
