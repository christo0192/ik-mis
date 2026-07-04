# Deploying the MIS Dashboard to Vercel (from this repo)

The dashboard lives in `dashboard/` (Next.js). Full app-level details: `dashboard/DEPLOY.md`.

## Vercel project settings
1. Import the `christo0192/ik-mis` repo in Vercel.
2. **Root Directory: `dashboard`** (critical — the repo root is the Python pipeline, not the app).
3. Framework preset: Next.js (auto-detected). Build command / output: defaults.

## Environment variables (Project → Settings → Environment Variables)
| Var | Value | Why |
|---|---|---|
| `MARKET_RESEARCH_BQ_SA_B64` | base64 of a service-account JSON with **read** on `Market_Research` + `SIP_pattern_rag` (and DML update on `ce_*` if approve/reject should work in prod) | BigQuery access. Use a narrow SA — NOT the dev key (`claude-code-dev-bq` has broader write grants than prod needs). |
| `MIS_REVIEW_TOKEN` | a long random string | Fail-closed gate on the approve/reject write route. Unset = writes disabled (read-only dashboard). |
| `MIS_ALLOW_SAME_ORIGIN_WRITES` | `true` (only if you want in-browser approve/reject) | Lets same-origin dashboard clicks write without the header token. |

## Non-negotiable before sharing the URL
**Enable Vercel Deployment Protection (password or team SSO).** The app itself has only
defense-in-depth auth on the write route; the read pages assume a trusted audience.
Platform-level protection is the perimeter (security review, 2026-07-04).

## After first deploy
- `/` should show the system graph with live node health (green/amber/gray).
- If tiles show "unavailable": the SA lacks dataset read — grant and redeploy nothing (it's runtime).
