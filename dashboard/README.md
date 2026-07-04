# Market Research Dashboard

Next.js (App Router) + TypeScript + Tailwind dashboard for the **AI/ML India
Market Research** dataset in BigQuery (`ik-marketing-data.Market_Research`).

BigQuery access is **server-side only**. The data layer (`src/lib/bq.ts`) is
guarded with `import "server-only"` and must only be used from Route Handlers and
Server Components — never a Client Component.

## Prerequisites

- Node.js 20+ (developed on Node 24)
- Read access to the `ik-marketing-data.Market_Research` BigQuery dataset

## Run locally

```powershell
npm install
npm run dev
```

Then open http://localhost:3000 and hit the health endpoint:

```
http://localhost:3000/api/health
```

Locally, **no environment variable is required**. The data layer automatically
falls back to the local service-account key file at:

```
C:\Users\Admin\.gcp-keys\market-research-bq.json
```

### Prove the BigQuery connection without the dev server

```powershell
node scripts/health-check.mjs
```

Expected output (approximate row counts):

```
job_market_demand: ~442 rows
social_trends: ~42 rows
blog_publications: ~23 rows
competitor_intel: ~5 rows
executive_summary: ~6 rows
```

## Production (Vercel)

There is no key file on Vercel, so credentials come from an env var instead.

1. Base64-encode the service-account JSON (PowerShell):

   ```powershell
   [Convert]::ToBase64String(
     [IO.File]::ReadAllBytes("C:\Users\Admin\.gcp-keys\market-research-bq.json")
   ) | Set-Content -NoNewline sa.b64
   ```

2. In the Vercel project settings, add an environment variable:

   ```
   MARKET_RESEARCH_BQ_SA_B64 = <contents of sa.b64, single line>
   ```

Credential precedence in `src/lib/bq.ts`:

1. `MARKET_RESEARCH_BQ_SA_B64` (base64 → JSON → `credentials`) — used in prod.
2. else local key file via `keyFilename` — used locally.

See `.env.local.example` for documentation.

## Security

- **Never commit** the service-account key or any `.env*` file. `.gitignore`
  excludes `.env*` and SA-key JSON patterns.
- The credential is never exposed to the browser (no `NEXT_PUBLIC_` prefix) and
  the key contents are never logged.

## Project layout

```
src/
  app/
    api/health/route.ts   # GET /api/health -> { ok, tables: [{table_id,row_count}] }
  lib/
    bq.ts                 # server-only BigQuery client + query<T>() + TABLES map
scripts/
  health-check.mjs        # standalone BigQuery connectivity check
```
