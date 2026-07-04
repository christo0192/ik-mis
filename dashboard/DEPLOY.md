# Deploy — Vercel (via GitHub)

Repo: **https://github.com/christo0192/ik-market-research-dashboard** (private)
The Next.js app is at the **repo root**, so Vercel auto-detects it.

## 1. Import the repo into Vercel
1. Vercel → **Add New… → Project** → import `christo0192/ik-market-research-dashboard`.
2. Framework preset: **Next.js** (auto). Root directory: **`./`**. Build/Output: defaults
   (`next build`). No overrides needed.

## 2. Set the one required env var (BigQuery auth)
The app authenticates to BigQuery with a base64-encoded service-account JSON in
`MARKET_RESEARCH_BQ_SA_B64` (read by `src/lib/bq.ts`). Generate it locally:

```powershell
# RECOMMENDED: use the read-only dashboard SA once provisioned (see below).
# Interim: the market-research-bq SA key works (it can read the dataset).
[Convert]::ToBase64String([IO.File]::ReadAllBytes("C:\Users\Admin\.gcp-keys\market-research-dash-bq.json"))
```

In Vercel → **Project → Settings → Environment Variables**:
- **Name:** `MARKET_RESEARCH_BQ_SA_B64`
- **Value:** (paste the base64 string)
- **Environments:** Production + Preview (+ Development if you use `vercel dev`)

Then **Deploy** (or push to `main` → auto-deploys).

## 3. Verify
- Visit the deployment URL → Overview should show the 5 KPI cards + Executive Priorities.
- `…/api/health` should return the 5 tables with row counts.

## Notes
- **Use the READ-ONLY SA in production.** Until the dedicated
  `market-research-dash-bq@` SA is provisioned (see `../job_market_agent/IAM_REQUEST.md`),
  you *can* deploy with the `market-research-bq` key — but that key can write to the
  dataset, so swap to the read-only key as soon as it's available (just change the
  `MARKET_RESEARCH_BQ_SA_B64` value, no code change).
- Local dev needs no env var — `src/lib/bq.ts` falls back to the key file at
  `C:\Users\Admin\.gcp-keys\market-research-bq.json`, or a `MARKET_RESEARCH_BQ_KEY_FILE`
  override path (see `.env.local.example`).
- Pages are `force-dynamic` (live BQ per request). If you want caching to cut BQ
  cost, switch pages to `export const revalidate = 3600` (hourly ISR).

## 4. Review write-route auth model (`/api/generation/review`)

This route is the dashboard's only write surface (Approve/Reject buttons). Its
`x-mis-token` header check is **defense-in-depth, not the primary perimeter**.
The primary perimeter for any deployment that exposes this route publicly
**must** be platform-level protection (Vercel Password Protection / SSO,
or an equivalent auth proxy) — the header alone is not sufficient authn for
an internet-facing deployment.

Env vars (both optional; default is fail-closed — writes disabled):
- `MIS_REVIEW_TOKEN` — if set, every write must send a matching `x-mis-token`
  header. Intended for server-to-server / internal automation callers, not
  the browser UI (a client bundle cannot hold this secret without exposing it).
- `MIS_ALLOW_SAME_ORIGIN_WRITES=true` — if set (and `MIS_REVIEW_TOKEN` is
  unset), same-origin requests are allowed without a token, verified via the
  `Origin` header. This is what the dashboard's own Approve/Reject buttons
  rely on locally/internally. **Do not set this on a publicly reachable
  deployment** without platform-level auth in front of it.
- Neither set: the route returns `503` and refuses all writes.
