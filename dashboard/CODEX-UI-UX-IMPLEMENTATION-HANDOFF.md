# Codex UI/UX implementation handover

Branch: `codex/ui-ux-overhaul`  
Draft PR: #5 — https://github.com/christo0192/ik-mis/pull/5

## Status

Implementation is complete in the working tree but intentionally **not committed or pushed**. The review server is running from a WSL-native mirror at `http://localhost:3000`; source of truth remains `dashboard/` in this branch.

## What changed

- Reworked the dashboard into a premium dark AI mission-control theme: graphite/black surfaces, violet/cyan luminance, metallic highlights, glass panels, brighter hierarchy, refined tables/cards/navigation.
- Replaced the expensive continuously repainting d3 force-map presentation with a lightweight interactive robot diagnostic console. Live graph data and node links remain intact.
- Added AI visual layers: breathing robot luminance, scanning beam, orbital energy rings, moving SVG telemetry paths, inference particles, health beacons, reflective hover sweeps, and reduced-motion fallback.
- Replaced overlap-prone percentage-positioned diagnostic nodes with constrained left/right telemetry rails at `xl`, plus a compact scrollable status matrix below `xl`.
- Final review refinement: telemetry rails now begin at `lg`, internal metric scrolling has been removed entirely, and smaller screens use an expanded chamber with a complete non-scrolling status grid above the robot.
- Removed the robot breathing/scan animation after visual review because the clipped scan restarted around the head and read as a rendering bug. Ambient rings, paths, particles, beacons, and core light retain the AI-powered motion language.
- Feather-masked the generated robot image on all four edges so its original dark studio background merges into the CSS chamber instead of appearing as a separate rectangular block.
- Added a responsive six-item mobile navigation dock and sufficient bottom content clearance.
- Added persistent pagination with visible record ranges, page state, and disabled Previous/Next controls. It remains visible for zero records and single-page results.
- Pagination is wired into Generation (keywords/copy/angles/rejects), Intelligence Blog, Jobs, and Social datasets.
- Added route-level loading skeletons and explicit navigation/diagnostic-link prefetching.
- Removed the runtime/build dependency on Google Fonts; the UI uses a high-quality system stack.
- Converted the generated robot asset from a 1.8 MB PNG to a 104 KB WebP (~94% smaller): `public/visuals/mis-diagnostic-robot.webp`.

## Important preserved contracts

- No changes to `src/app/api/generation/review/route.ts`.
- No changes to `src/lib/*` server-only queries/data contracts.
- RSC data fetching remains server-side.
- `safeQuery` unavailable/empty behavior remains honest.
- LinkedIn InMail/Conversation fields (`linkedin_format`, `subject`, `body`) remain rendered.
- Keyword Planner fields (`keyword_source`, volume, competition, bids) remain rendered.
- No production deployment was performed.

## Changed/new files

- `src/app/globals.css`
- `src/app/layout.tsx`
- `src/app/loading.tsx` (new)
- `src/app/page.tsx`
- `src/app/generation/page.tsx`
- `src/app/intelligence/blog/page.tsx`
- `src/app/intelligence/jobs/page.tsx`
- `src/app/intelligence/social/page.tsx`
- `src/components/sidebar-nav.tsx`
- `src/components/system-graph.tsx`
- `src/components/ui/card.tsx`
- `src/components/ui/misc.tsx`
- `src/components/ui/table.tsx`
- `src/components/ui/pagination.tsx` (new)
- `public/visuals/mis-diagnostic-robot.webp` (new)

## Validation completed

- `npm run lint` — passed.
- `npm run build` — passed, including TypeScript and every App Router route.
- `git diff --check` — passed.
- Local HTTP checks: `/` and `/generation?tab=keywords` both return 200.
- Warm local timings during the final check: home ~2.1s, Generation ~1.0s.
- Rendered HTML confirms both persistent pagination and the MIS Neural Core.
- One pre-existing build warning remains for the dynamic export-directory NFT trace (`src/lib/exports-dir.ts`); it was deliberately left untouched.

## Local review/run note

The Windows-mounted `dashboard/node_modules` was incomplete and repeatedly hit WSL rename/ENOTEMPTY locking. Dependencies were installed into `/tmp/mis-dashboard`, and that mirror currently serves `http://localhost:3000`. For a normal local run after dependency cleanup:

```bash
cd dashboard
npm ci
npm run dev
```

## Suggested commit

```text
feat(dashboard): overhaul mission control UI and navigation
```

Before committing, review the working tree, then stage only `dashboard/` UI files listed above. Do not stage temporary directories; `/tmp/mis-dashboard` is outside the repository.
