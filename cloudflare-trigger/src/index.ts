// Cloudflare Worker — fires the ik-ad-generator pipeline daily at 11:00 AM
// IST (05:30 UTC), one hour after Performance's ik-anomaly run. It only
// starts the Fly machine; entrypoint.sh + run_daily.sh do the work and the
// machine auto-stops on exit. Mirrors the ik-anomaly-trigger Worker
// verbatim (anomaly-engine/cloudflare-trigger/src/index.ts), retargeted at
// this app.
//
// 2026-10-27 — also the Intelligence department's monthly scheduler. The
// dedicated ik-intelligence-trigger Worker (ad-content-creator repo) was
// refused its cron: the account is at the Workers Free limit of 5 cron
// triggers (code 10072). So this Worker's single daily cron ALSO starts:
//   UTC day 1 -> ik-market-research-jobs   (JOBS_APP / JOBS_MACHINE_ID / FLY_TOKEN_JOBS)
//   UTC day 2 -> ik-market-research-weekly (WEEKLY_APP / WEEKLY_MACHINE_ID / FLY_TOKEN_WEEKLY)
// The generator start is unchanged and independent: both starts run
// concurrently (Promise.allSettled), each catches its own errors, and a
// missing/failed intelligence start only logs. Helpers ported from the tested
// ad-content-creator cloudflare-trigger/src/index.ts.

export interface Env {
  FLY_TOKEN: string; // Cloudflare secret — Fly Personal Access Token
  FLY_APP: string; // var, "ik-ad-generator"
  FLY_MACHINE_ID: string; // secret/var — set after first fly deploy
  // Intelligence targets (2026-10-27). Optional at the type level because a
  // secret that was never `wrangler secret put` is simply absent at runtime.
  FLY_TOKEN_JOBS?: string; // secret — Fly deploy token scoped to ik-market-research-jobs
  FLY_TOKEN_WEEKLY?: string; // secret — Fly deploy token scoped to ik-market-research-weekly
  JOBS_APP?: string; // var
  JOBS_MACHINE_ID?: string; // var
  WEEKLY_APP?: string; // var
  WEEKLY_MACHINE_ID?: string; // var
}

/** Must equal wrangler.toml [triggers].crons — test/index.test.ts guards it. */
export const CRON = "30 5 * * *";
export const JOBS_DAY = 1;
export const WEEKLY_DAY = 2;

export interface Target {
  label: "generator" | "jobs" | "weekly";
  app: string;
  machineId: string;
  token: string;
}

/** The daily generator start — always, every day, exactly as before. */
export function generatorTarget(env: Env): Target {
  return { label: "generator", app: env.FLY_APP, machineId: env.FLY_MACHINE_ID, token: env.FLY_TOKEN };
}

/** Which Intelligence machine to start on this UTC day of the month (null = none). */
export function intelligenceTargetForDay(dayOfMonthUtc: number, env: Env): Target | null {
  switch (dayOfMonthUtc) {
    case JOBS_DAY:
      return {
        label: "jobs",
        app: env.JOBS_APP ?? "",
        machineId: env.JOBS_MACHINE_ID ?? "",
        token: env.FLY_TOKEN_JOBS ?? "",
      };
    case WEEKLY_DAY:
      return {
        label: "weekly",
        app: env.WEEKLY_APP ?? "",
        machineId: env.WEEKLY_MACHINE_ID ?? "",
        token: env.FLY_TOKEN_WEEKLY ?? "",
      };
    default:
      return null;
  }
}

/** Fly deploy tokens (`fly tokens create deploy`) print as "FlyV1 fm2_…" and are
 *  sent as-is, the way flyctl sends macaroons; anything else (the generator's
 *  Personal Access Token) goes as Bearer — the same header as before. Trimmed
 *  because a token piped into `wrangler secret put` can carry a trailing
 *  newline (fetch strips that from header values anyway). */
export function authHeader(token: string): string {
  const t = (token ?? "").trim();
  return t.startsWith("FlyV1 ") ? t : `Bearer ${t}`;
}

/** For the generator's plain app name + hex machine id this is byte-identical
 *  to the old template-literal URL (trim mirrors the URL parser dropping a
 *  trailing newline from a piped secret). */
export function startUrl(t: Target): string {
  const seg = (s: string) => encodeURIComponent(String(s ?? "").trim());
  return `https://api.machines.dev/v1/apps/${seg(t.app)}/machines/${seg(t.machineId)}/start`;
}

export interface StartResult {
  ok: boolean;
  status: number;
  body: string;
}

/** Per-attempt cap for the Intelligence starts, so a hung Fly API call cannot
 *  eat the whole retry budget. */
export const ATTEMPT_TIMEOUT_MS = 15_000;

/** One POST to the Machines API `start`. timeoutMs = null sends no abort signal
 *  (the generator start, as before). */
export async function startMachine(
  t: Target,
  fetchImpl: typeof fetch = fetch,
  timeoutMs: number | null = ATTEMPT_TIMEOUT_MS,
  authorization: string = authHeader(t.token),
): Promise<StartResult> {
  const init: RequestInit = {
    method: "POST",
    headers: { Authorization: authorization, "Content-Type": "application/json" },
  };
  if (timeoutMs !== null) init.signal = AbortSignal.timeout(timeoutMs);
  const res = await fetchImpl(startUrl(t), init);
  return { ok: res.ok, status: res.status, body: (await res.text()).slice(0, 500) };
}

// Retry (Intelligence only): those machines start on ONE day a month, so one
// transient Fly API error (5xx / 429 / network / timeout) would skip that
// pipeline for the whole month. A retry cannot double-run a job: it targets the
// same single Machine seconds later (a job runs for minutes), and a Machine that
// is still running cannot be started a second time. Any other 4xx (bad token,
// wrong id, non-startable state) is final and never retried. The daily
// generator start keeps its single attempt.
export const START_ATTEMPTS = 3;
/** Pause before retry n (ms): attempt 2 after 2 s, attempt 3 after 5 s more. */
export const RETRY_DELAYS_MS: readonly number[] = [2_000, 5_000];

export function isRetryableStatus(status: number): boolean {
  return status === 429 || status >= 500;
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

export interface RetryOptions {
  fetchImpl?: typeof fetch;
  wait?: (ms: number) => Promise<void>;
  warn?: (msg: string) => void;
}

/** startMachine with bounded retries. Returns the last response (plus how many
 *  attempts it took); rethrows the last error only if every attempt threw. */
export async function startMachineWithRetry(
  t: Target,
  { fetchImpl = fetch, wait = sleep, warn = (m: string) => console.warn(m) }: RetryOptions = {},
): Promise<StartResult & { attempts: number }> {
  for (let attempt = 1; ; attempt++) {
    const last = attempt >= START_ATTEMPTS;
    try {
      const r = await startMachine(t, fetchImpl);
      if (r.ok || !isRetryableStatus(r.status) || last) return { ...r, attempts: attempt };
      warn(`${t.label} start attempt ${attempt}/${START_ATTEMPTS} got ${r.status} — retrying`);
    } catch (err) {
      if (last) throw err;
      warn(`${t.label} start attempt ${attempt}/${START_ATTEMPTS} error: ${String(err)} — retrying`);
    }
    await wait(RETRY_DELAYS_MS[Math.min(attempt, RETRY_DELAYS_MS.length) - 1]);
  }
}

/** Log timestamp; never throws (an out-of-range time must not stop a start). */
export function iso(ms: number): string {
  try {
    return new Date(ms).toISOString();
  } catch {
    return String(ms);
  }
}

/** The generator start: one attempt, no timeout, same URL + header as before.
 *  Never throws. */
export async function startGenerator(env: Env, when: string, fetchImpl: typeof fetch = fetch): Promise<void> {
  try {
    const t = generatorTarget(env);
    console.log(`[${when}] Cron fired; POSTing to ${startUrl(t)}`);
    // Exactly the pre-2026-10-27 header (no trim, no FlyV1 detection): the
    // daily generator start must stay byte-for-byte what has worked since July.
    const r = await startMachine(t, fetchImpl, null, `Bearer ${env.FLY_TOKEN}`);
    if (r.ok) {
      console.log(`[${when}] Fly machine start OK (${r.status}): ${r.body.slice(0, 200)}`);
    } else {
      console.error(`[${when}] Fly start FAILED (${r.status}): ${r.body}`);
    }
  } catch (err) {
    console.error(`[${when}] Fetch error:`, err);
  }
}

/** The Intelligence start for this UTC day, if any. Never throws. */
export async function startIntelligence(
  scheduledTime: number,
  env: Env,
  when: string,
  opts: RetryOptions = {},
): Promise<void> {
  let label = "intelligence";
  try {
    const day = new Date(scheduledTime).getUTCDate();
    const t = intelligenceTargetForDay(day, env);
    if (!t) return; // 28-30 days a month: generator only, nothing to log.
    label = t.label;
    if (!t.app.trim() || !t.machineId.trim() || !t.token.trim()) {
      console.error(`[${when}] ${t.label}: missing app/machine/token config — not started (generator unaffected)`);
      return;
    }
    const r = await startMachineWithRetry(t, { warn: (m) => console.warn(`[${when}] ${m}`), ...opts });
    const tries = r.attempts > 1 ? ` after ${r.attempts} attempts` : "";
    if (r.ok) {
      console.log(`[${when}] ${t.label} start OK (${r.status}) ${t.app}/${t.machineId}${tries}`);
    } else {
      // After a lost/5xx first response the machine may in fact be running
      // (the retry is then refused) — `fly machine status` tells which.
      console.error(`[${when}] ${t.label} start FAILED (${r.status}) ${t.app}/${t.machineId}${tries}: ${r.body}`);
    }
  } catch (err) {
    console.error(`[${when}] ${label} fetch error:`, err);
  }
}

export default {
  async scheduled(event: ScheduledController, env: Env, _ctx: ExecutionContext): Promise<void> {
    // Never throw: Cloudflare retries a scheduled event whose handler throws,
    // which would double-start the machines. Every failure is logged and
    // accepted; tokens are never logged. Both starts begin immediately and run
    // concurrently, so the Intelligence start (with its retries) can neither
    // block nor delay the generator, and vice versa.
    try {
      const when = iso(event.scheduledTime);
      await Promise.allSettled([startGenerator(env, when), startIntelligence(event.scheduledTime, env, when)]);
    } catch (err) {
      console.error("scheduled() unexpected error:", err);
    }
  },

  async fetch(_request: Request, _env: Env): Promise<Response> {
    return new Response(
      "Scheduled-only Worker. Test-fire: wrangler trigger ik-ad-generator-trigger --remote",
      { status: 405 },
    );
  },
} satisfies ExportedHandler<Env>;
