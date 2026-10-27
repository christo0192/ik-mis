// vitest (node env) for the ik-ad-generator-trigger Worker — added 2026-10-27
// with the Intelligence piggyback. fetch is always stubbed: nothing here can
// reach the Fly API.
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import worker, {
  ATTEMPT_TIMEOUT_MS,
  CRON,
  JOBS_DAY,
  RETRY_DELAYS_MS,
  START_ATTEMPTS,
  WEEKLY_DAY,
  authHeader,
  generatorTarget,
  intelligenceTargetForDay,
  isRetryableStatus,
  iso,
  startMachine,
  startMachineWithRetry,
  startUrl,
  type Env,
} from "../src/index";

const ENV: Env = {
  FLY_TOKEN: "pat-generator-secret",
  FLY_APP: "ik-ad-generator",
  FLY_MACHINE_ID: "d8d1234abcd567",
  FLY_TOKEN_JOBS: "FlyV1 fm2_jobs-secret",
  FLY_TOKEN_WEEKLY: "FlyV1 fm2_weekly-secret",
  JOBS_APP: "ik-market-research-jobs",
  JOBS_MACHINE_ID: "7845393fe26338",
  WEEKLY_APP: "ik-market-research-weekly",
  WEEKLY_MACHINE_ID: "7812565f9d3378",
};
const SECRETS = ["pat-generator-secret", "fm2_jobs-secret", "fm2_weekly-secret"];
// Byte-identical to the pre-2026-10-27 template literal URL.
const GEN_URL = `https://api.machines.dev/v1/apps/${ENV.FLY_APP}/machines/${ENV.FLY_MACHINE_ID}/start`;
const JOBS_URL = "https://api.machines.dev/v1/apps/ik-market-research-jobs/machines/7845393fe26338/start";
const WEEKLY_URL = "https://api.machines.dev/v1/apps/ik-market-research-weekly/machines/7812565f9d3378/start";

type Event = Parameters<typeof worker.scheduled>[0];
type Ctx = Parameters<typeof worker.scheduled>[2];
const ctx = {} as Ctx;
const at = (isoTime: string): Event => ({ scheduledTime: Date.parse(isoTime), cron: CRON, noRetry() {} }) as Event;
const DAY1 = "2026-11-01T05:30:00Z";
const DAY2 = "2026-11-02T05:30:00Z";

let logs: string[];
let fetchMock: ReturnType<typeof vi.fn>;
const auth = (init: RequestInit) => (init.headers as Record<string, string>).Authorization;
const callsTo = (url: string) => fetchMock.mock.calls.filter((c) => c[0] === url);

beforeEach(() => {
  logs = [];
  for (const level of ["log", "info", "debug", "warn", "error"] as const) {
    vi.spyOn(console, level).mockImplementation((...args: unknown[]) => {
      logs.push(args.map(String).join(" "));
    });
  }
  fetchMock = vi.fn(async () => new Response('{"ok":true}', { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// The Intelligence retry pauses are real setTimeouts; fake timers skip them.
const runScheduled = async (event: Event, env: Env = ENV) => {
  vi.useFakeTimers();
  try {
    const p = worker.scheduled(event, env, ctx);
    await vi.runAllTimersAsync();
    return await p;
  } finally {
    vi.useRealTimers();
  }
};

describe("targets", () => {
  it("the generator target is FLY_APP / FLY_MACHINE_ID / FLY_TOKEN", () => {
    expect(generatorTarget(ENV)).toEqual({
      label: "generator",
      app: "ik-ad-generator",
      machineId: "d8d1234abcd567",
      token: "pat-generator-secret",
    });
  });

  it("maps day 1 to the jobs machine and day 2 to the pipeline machine", () => {
    expect(JOBS_DAY).toBe(1);
    expect(WEEKLY_DAY).toBe(2);
    expect(intelligenceTargetForDay(1, ENV)).toEqual({
      label: "jobs",
      app: "ik-market-research-jobs",
      machineId: "7845393fe26338",
      token: "FlyV1 fm2_jobs-secret",
    });
    expect(intelligenceTargetForDay(2, ENV)).toEqual({
      label: "weekly",
      app: "ik-market-research-weekly",
      machineId: "7812565f9d3378",
      token: "FlyV1 fm2_weekly-secret",
    });
  });

  it("returns null for every other day", () => {
    for (const day of [0, 3, 15, 28, 31, NaN]) expect(intelligenceTargetForDay(day, ENV)).toBeNull();
  });

  it("missing intelligence env becomes empty strings, never undefined", () => {
    expect(intelligenceTargetForDay(1, { FLY_TOKEN: "t", FLY_APP: "a", FLY_MACHINE_ID: "m" })).toEqual({
      label: "jobs",
      app: "",
      machineId: "",
      token: "",
    });
  });
});

describe("authHeader", () => {
  it("sends the generator's PAT as Bearer, exactly as before", () => {
    expect(authHeader("pat-generator-secret")).toBe("Bearer pat-generator-secret");
  });
  it("passes Fly deploy (macaroon) tokens through unchanged", () => {
    expect(authHeader("FlyV1 fm2_x")).toBe("FlyV1 fm2_x");
  });
  it("trims the trailing newline a piped `wrangler secret put` can leave", () => {
    expect(authHeader("FlyV1 fm2_x\r\n")).toBe("FlyV1 fm2_x");
    expect(authHeader("  abc\n")).toBe("Bearer abc");
  });
});

describe("startUrl / startMachine", () => {
  it("the generator URL is byte-identical to the old template literal", () => {
    expect(startUrl(generatorTarget(ENV))).toBe(GEN_URL);
    expect(startUrl({ ...generatorTarget(ENV), machineId: `${ENV.FLY_MACHINE_ID}\n` })).toBe(GEN_URL);
  });

  it("builds the exact Machines API start URL (encoded)", () => {
    expect(startUrl(intelligenceTargetForDay(1, ENV)!)).toBe(JOBS_URL);
    expect(startUrl(intelligenceTargetForDay(2, ENV)!)).toBe(WEEKLY_URL);
    expect(startUrl({ label: "jobs", app: "a/b", machineId: "m 1", token: "t" })).toBe(
      "https://api.machines.dev/v1/apps/a%2Fb/machines/m%201/start",
    );
  });

  it("POSTs once with the auth header, a per-attempt timeout, and truncates the body to 500 chars", async () => {
    const f = vi.fn(async () => new Response("x".repeat(2000), { status: 409 }));
    const timeout = vi.spyOn(AbortSignal, "timeout");
    const r = await startMachine(intelligenceTargetForDay(2, ENV)!, f as unknown as typeof fetch);
    expect(r).toEqual({ ok: false, status: 409, body: "x".repeat(500) });
    expect(f).toHaveBeenCalledTimes(1);
    expect(f).toHaveBeenCalledWith(WEEKLY_URL, {
      method: "POST",
      headers: { Authorization: "FlyV1 fm2_weekly-secret", "Content-Type": "application/json" },
      signal: expect.any(AbortSignal),
    });
    expect(timeout).toHaveBeenCalledWith(ATTEMPT_TIMEOUT_MS);
  });

  it("timeoutMs = null sends exactly the old request: no abort signal", async () => {
    const f = vi.fn(async () => new Response("ok", { status: 200 }));
    const timeout = vi.spyOn(AbortSignal, "timeout");
    await startMachine(generatorTarget(ENV), f as unknown as typeof fetch, null);
    expect(f).toHaveBeenCalledWith(GEN_URL, {
      method: "POST",
      headers: { Authorization: "Bearer pat-generator-secret", "Content-Type": "application/json" },
    });
    expect(timeout).not.toHaveBeenCalled();
  });
});

describe("startMachineWithRetry", () => {
  const jobs = intelligenceTargetForDay(1, ENV)!;
  const resp = (status: number) => async () => new Response(`s${status}`, { status });
  const run = (f: ReturnType<typeof vi.fn>) => {
    const waits: number[] = [];
    const warns: string[] = [];
    const p = startMachineWithRetry(jobs, {
      fetchImpl: f as unknown as typeof fetch,
      wait: async (ms) => {
        waits.push(ms);
      },
      warn: (m) => warns.push(m),
    });
    return { p, waits, warns };
  };

  it("retries only 429 and 5xx", () => {
    for (const s of [429, 500, 502, 503, 504]) expect(isRetryableStatus(s)).toBe(true);
    for (const s of [200, 400, 401, 403, 404, 409, 412, 422]) expect(isRetryableStatus(s)).toBe(false);
  });

  it("a first-try success makes exactly one call and never waits", async () => {
    const f = vi.fn(resp(200));
    const { p, waits } = run(f);
    expect(await p).toMatchObject({ ok: true, status: 200, attempts: 1 });
    expect(f).toHaveBeenCalledTimes(1);
    expect(waits).toEqual([]);
  });

  it("a transient 503 then 200 starts the machine on the second attempt", async () => {
    const f = vi.fn(resp(200)).mockImplementationOnce(resp(503));
    const { p, waits, warns } = run(f);
    expect(await p).toMatchObject({ ok: true, status: 200, attempts: 2 });
    expect(waits).toEqual([RETRY_DELAYS_MS[0]]);
    expect(warns).toEqual([`jobs start attempt 1/${START_ATTEMPTS} got 503 — retrying`]);
  });

  it("a network error / timeout is retried", async () => {
    const f = vi
      .fn(resp(200))
      .mockImplementationOnce(async () => {
        throw new DOMException("The operation was aborted due to timeout", "TimeoutError");
      })
      .mockImplementationOnce(async () => {
        throw new TypeError("network down");
      });
    const { p, waits } = run(f);
    expect(await p).toMatchObject({ ok: true, attempts: 3 });
    expect(waits).toEqual([...RETRY_DELAYS_MS]);
  });

  it("never retries a non-429 4xx (bad token, wrong id, non-startable state)", async () => {
    for (const s of [401, 404, 409, 412]) {
      const f = vi.fn(resp(s));
      const { p, waits } = run(f);
      expect(await p).toMatchObject({ ok: false, status: s, attempts: 1 });
      expect(f).toHaveBeenCalledTimes(1);
      expect(waits).toEqual([]);
    }
  });

  it("gives up after START_ATTEMPTS: returns the last 5xx, or rethrows the last error", async () => {
    expect(START_ATTEMPTS).toBe(3);
    const f500 = vi.fn(resp(500));
    const a = run(f500);
    expect(await a.p).toMatchObject({ ok: false, status: 500, body: "s500", attempts: 3 });
    expect(a.waits).toEqual([2_000, 5_000]);
    const fNet = vi.fn(async () => {
      throw new TypeError("network down");
    });
    const b = run(fNet);
    await expect(b.p).rejects.toThrow("network down");
    expect(fNet).toHaveBeenCalledTimes(3);
  });

  it("the whole retry window stays far below a job's runtime", () => {
    const worstMs = START_ATTEMPTS * ATTEMPT_TIMEOUT_MS + RETRY_DELAYS_MS.reduce((s, x) => s + x, 0);
    expect(worstMs).toBeLessThanOrEqual(60_000);
  });
});

describe("scheduled() dispatch", () => {
  it("day 1 starts the generator + the jobs machine (2 POSTs, exact URLs and auth)", async () => {
    await worker.scheduled(at(DAY1), ENV, ctx);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [gen] = callsTo(GEN_URL);
    const [jobs] = callsTo(JOBS_URL);
    expect(gen[1]).toEqual({
      method: "POST",
      headers: { Authorization: "Bearer pat-generator-secret", "Content-Type": "application/json" },
    });
    expect(jobs[1].method).toBe("POST");
    expect(auth(jobs[1])).toBe("FlyV1 fm2_jobs-secret");
    const all = logs.join("\n");
    expect(all).toContain("Fly machine start OK (200)");
    expect(all).toContain("jobs start OK (200) ik-market-research-jobs/7845393fe26338");
  });

  it("generator header is the exact legacy `Bearer ${FLY_TOKEN}` (no trim, no FlyV1 detection)", async () => {
    for (const tok of ["FlyV1 fm2_generator", "pat-with-newline\n"]) {
      fetchMock.mockClear();
      await worker.scheduled(at("2026-11-03T05:30:00Z"), { ...ENV, FLY_TOKEN: tok }, ctx);
      const [gen] = callsTo(GEN_URL);
      expect(auth(gen[1])).toBe(`Bearer ${tok}`);
    }
  });

  it("day 2 starts the generator + the pipeline machine", async () => {
    await worker.scheduled(at(DAY2), ENV, ctx);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(callsTo(GEN_URL)).toHaveLength(1);
    const [weekly] = callsTo(WEEKLY_URL);
    expect(auth(weekly[1])).toBe("FlyV1 fm2_weekly-secret");
    expect(logs.join("\n")).toContain("weekly start OK (200) ik-market-research-weekly/7812565f9d3378");
  });

  it("every other day starts the generator only", async () => {
    for (const t of ["2026-11-03T05:30:00Z", "2026-11-15T05:30:00Z", "2026-10-31T05:30:00Z", "2026-12-28T05:30:00Z"]) {
      fetchMock.mockClear();
      await worker.scheduled(at(t), ENV, ctx);
      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(fetchMock.mock.calls[0][0]).toBe(GEN_URL);
    }
  });

  it("the generator request is not given an abort timeout (unchanged)", async () => {
    await worker.scheduled(at(DAY1), ENV, ctx);
    expect("signal" in callsTo(GEN_URL)[0][1]).toBe(false);
    expect(callsTo(JOBS_URL)[0][1].signal).toBeInstanceOf(AbortSignal);
  });

  it("missing intelligence token or vars still starts the generator, never throws", async () => {
    const broken: Env[] = [
      { ...ENV, FLY_TOKEN_JOBS: undefined },
      { ...ENV, FLY_TOKEN_JOBS: "" },
      { ...ENV, FLY_TOKEN_JOBS: "  \n" },
      { ...ENV, JOBS_APP: undefined },
      { ...ENV, JOBS_MACHINE_ID: "" },
      { FLY_TOKEN: ENV.FLY_TOKEN, FLY_APP: ENV.FLY_APP, FLY_MACHINE_ID: ENV.FLY_MACHINE_ID },
    ];
    for (const env of broken) {
      fetchMock.mockClear();
      await expect(worker.scheduled(at(DAY1), env, ctx)).resolves.toBeUndefined();
      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(fetchMock.mock.calls[0][0]).toBe(GEN_URL);
    }
    expect(logs.filter((l) => l.includes("jobs: missing app/machine/token config"))).toHaveLength(broken.length);

    fetchMock.mockClear();
    await worker.scheduled(at(DAY2), { ...ENV, FLY_TOKEN_WEEKLY: undefined, WEEKLY_MACHINE_ID: undefined }, ctx);
    expect(fetchMock.mock.calls.map((c) => c[0])).toEqual([GEN_URL]);
    expect(logs.join("\n")).toContain("weekly: missing app/machine/token config");
  });
});

describe("scheduled() independence", () => {
  const byUrl = (handlers: Record<string, () => Promise<Response>>) =>
    fetchMock.mockImplementation(async (url: string) => {
      const h = handlers[url];
      return h ? h() : new Response("ok", { status: 200 });
    });

  it("a generator 500 does not stop the jobs start", async () => {
    byUrl({ [GEN_URL]: async () => new Response("boom", { status: 500 }) });
    await expect(worker.scheduled(at(DAY1), ENV, ctx)).resolves.toBeUndefined();
    expect(callsTo(GEN_URL)).toHaveLength(1); // generator: single attempt, as before
    expect(callsTo(JOBS_URL)).toHaveLength(1);
    const all = logs.join("\n");
    expect(all).toContain("Fly start FAILED (500): boom");
    expect(all).toContain("jobs start OK (200)");
  });

  it("a generator network error does not stop the pipeline start", async () => {
    byUrl({
      [GEN_URL]: async () => {
        throw new TypeError("network down");
      },
    });
    await expect(worker.scheduled(at(DAY2), ENV, ctx)).resolves.toBeUndefined();
    expect(callsTo(WEEKLY_URL)).toHaveLength(1);
    const all = logs.join("\n");
    expect(all).toContain("Fetch error:");
    expect(all).toContain("weekly start OK (200)");
  });

  it("a persistent jobs 500 (retried) does not stop the generator start", async () => {
    byUrl({ [JOBS_URL]: async () => new Response("non-startable", { status: 500 }) });
    await expect(runScheduled(at(DAY1))).resolves.toBeUndefined();
    expect(callsTo(GEN_URL)).toHaveLength(1);
    expect(callsTo(JOBS_URL)).toHaveLength(START_ATTEMPTS);
    const all = logs.join("\n");
    expect(all).toContain("Fly machine start OK (200)");
    expect(all).toContain(`jobs start FAILED (500) ik-market-research-jobs/7845393fe26338 after ${START_ATTEMPTS} attempts`);
  });

  it("a persistent pipeline network error does not stop the generator start", async () => {
    byUrl({
      [WEEKLY_URL]: async () => {
        throw new TypeError("network down");
      },
    });
    await expect(runScheduled(at(DAY2))).resolves.toBeUndefined();
    expect(callsTo(GEN_URL)).toHaveLength(1);
    expect(callsTo(WEEKLY_URL)).toHaveLength(START_ATTEMPTS);
    expect(logs.join("\n")).toContain("weekly fetch error:");
  });

  it("the generator is not delayed by the intelligence start (both POSTs fire before either resolves)", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    fetchMock.mockImplementation(async () => {
      await gate;
      return new Response("ok", { status: 200 });
    });
    const p = worker.scheduled(at(DAY1), ENV, ctx);
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(callsTo(GEN_URL)).toHaveLength(1);
    expect(callsTo(JOBS_URL)).toHaveLength(1);
    release();
    await expect(p).resolves.toBeUndefined();
  });

  it("a jobs retry wait does not hold back the generator's result log", async () => {
    vi.useFakeTimers();
    byUrl({ [JOBS_URL]: async () => new Response("busy", { status: 503 }) });
    const p = worker.scheduled(at(DAY1), ENV, ctx);
    // Let microtasks settle without advancing the 2 s retry pause.
    await vi.advanceTimersByTimeAsync(0);
    expect(logs.join("\n")).toContain("Fly machine start OK (200)");
    expect(callsTo(JOBS_URL)).toHaveLength(1);
    await vi.runAllTimersAsync();
    await p;
  });
});

describe("scheduled() never throws", () => {
  it("on garbage scheduledTime: still starts the generator, no intelligence", async () => {
    for (const t of [NaN, 1e20, undefined as unknown as number]) {
      fetchMock.mockClear();
      await expect(worker.scheduled({ scheduledTime: t, cron: CRON } as Event, ENV, ctx)).resolves.toBeUndefined();
      expect(fetchMock.mock.calls.map((c) => c[0])).toEqual([GEN_URL]);
    }
    expect(iso(NaN)).toBe("NaN");
  });

  it("when both starts fail, and on an empty env", async () => {
    fetchMock.mockImplementation(async () => {
      throw new TypeError("network down");
    });
    await expect(runScheduled(at(DAY1))).resolves.toBeUndefined();
    await expect(runScheduled(at(DAY2), {} as Env)).resolves.toBeUndefined();
  });

  it("when Response.text() itself throws", async () => {
    fetchMock.mockImplementation(async () => {
      const r = new Response("x", { status: 200 });
      vi.spyOn(r, "text").mockRejectedValue(new Error("body stream broke"));
      return r;
    });
    await expect(runScheduled(at(DAY1))).resolves.toBeUndefined();
  });
});

describe("secrets", () => {
  it("tokens never appear in console output, on success or failure", async () => {
    await worker.scheduled(at(DAY1), ENV, ctx);
    await worker.scheduled(at(DAY2), ENV, ctx);
    fetchMock.mockImplementation(async () => new Response("unauthorized", { status: 401 }));
    await worker.scheduled(at(DAY1), ENV, ctx);
    await worker.scheduled(at(DAY2), ENV, ctx);
    fetchMock.mockImplementation(async () => {
      throw new TypeError("network down");
    });
    await runScheduled(at(DAY1));
    await runScheduled(at(DAY2));
    await worker.scheduled(at(DAY1), { ...ENV, JOBS_APP: "" }, ctx);
    expect(logs.length).toBeGreaterThan(0);
    const all = logs.join("\n");
    for (const s of SECRETS) expect(all).not.toContain(s);
  });
});

describe("fetch()", () => {
  it("returns 405 (scheduled-only)", async () => {
    const res = await worker.fetch(new Request("https://example.com/"), ENV);
    expect(res.status).toBe(405);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("wrangler.toml drift guard", () => {
  const toml = readFileSync(fileURLToPath(new URL("../wrangler.toml", import.meta.url)), "utf8");
  const v = (key: string) => toml.match(new RegExp(`^${key}\\s*=\\s*"([^"]*)"`, "m"))?.[1];

  it("keeps exactly one cron, the daily 05:30 UTC one (no new cron slot)", () => {
    expect(CRON).toBe("30 5 * * *");
    const all = [...toml.matchAll(/^crons\s*=\s*\[([^\]]*)\]/gm)];
    expect(all).toHaveLength(1);
    const crons = [...all[0][1].matchAll(/"([^"]*)"/g)].map((x) => x[1]);
    expect(crons).toEqual([CRON]);
  });

  it("keeps the Worker name and the generator app", () => {
    expect(v("name")).toBe("ik-ad-generator-trigger");
    expect(v("FLY_APP")).toBe("ik-ad-generator");
  });

  it("targets the real Intelligence machines", () => {
    expect(v("JOBS_APP")).toBe("ik-market-research-jobs");
    expect(v("JOBS_MACHINE_ID")).toBe("7845393fe26338");
    expect(v("WEEKLY_APP")).toBe("ik-market-research-weekly");
    expect(v("WEEKLY_MACHINE_ID")).toBe("7812565f9d3378");
  });

  it("never commits a token as a var", () => {
    for (const k of ["FLY_TOKEN", "FLY_TOKEN_JOBS", "FLY_TOKEN_WEEKLY", "FLY_MACHINE_ID"]) expect(v(k)).toBeUndefined();
  });
});

describe(".gitignore guard (public repo)", () => {
  // `git check-ignore --no-index` exits 0 when the path is ignored, 1 when not
  // (execFileSync throws on 1). Paths are relative to cloudflare-trigger/.
  const cwd = fileURLToPath(new URL("..", import.meta.url));
  const ignored = (path: string) => {
    try {
      execFileSync("git", ["check-ignore", "--no-index", "-q", path], { cwd, stdio: "ignore" });
      return true;
    } catch {
      return false;
    }
  };

  it("ignores the local Wrangler secret files and build output", () => {
    for (const p of [".dev.vars", ".dev.vars.production", ".env", ".env.local", ".wrangler/tmp/bundle.js"]) {
      expect(ignored(p), p).toBe(true);
    }
  });

  it("still tracks the source, config and lockfile", () => {
    for (const p of ["package-lock.json", "package.json", "wrangler.toml", "src/index.ts", "test/index.test.ts"]) {
      expect(ignored(p), p).toBe(false);
    }
  });
});
