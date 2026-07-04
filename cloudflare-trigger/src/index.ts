// Cloudflare Worker — fires the ik-ad-generator pipeline daily at 11:00 AM
// IST (05:30 UTC), one hour after Performance's ik-anomaly run. It only
// starts the Fly machine; entrypoint.sh + run_daily.sh do the work and the
// machine auto-stops on exit. Mirrors the ik-anomaly-trigger Worker
// verbatim (anomaly-engine/cloudflare-trigger/src/index.ts), retargeted at
// this app.

interface Env {
  FLY_TOKEN: string; // Cloudflare secret — Fly Personal Access Token
  FLY_APP: string; // var, "ik-ad-generator"
  FLY_MACHINE_ID: string; // secret/var — set after first fly deploy
}

export default {
  async scheduled(event: ScheduledEvent, env: Env, _ctx: ExecutionContext): Promise<void> {
    const startUrl = `https://api.machines.dev/v1/apps/${env.FLY_APP}/machines/${env.FLY_MACHINE_ID}/start`;
    const cronTime = new Date(event.scheduledTime).toISOString();
    console.log(`[${cronTime}] Cron fired; POSTing to ${startUrl}`);

    try {
      const res = await fetch(startUrl, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${env.FLY_TOKEN}`,
          "Content-Type": "application/json",
        },
      });
      const body = await res.text();
      if (res.ok) {
        console.log(`[${cronTime}] Fly machine start OK (${res.status}): ${body.slice(0, 200)}`);
      } else {
        // Don't throw — Cloudflare retries scheduled events on uncaught errors,
        // which would double-start the machine. Log + accept the miss.
        console.error(`[${cronTime}] Fly start FAILED (${res.status}): ${body}`);
      }
    } catch (err) {
      console.error(`[${cronTime}] Fetch error:`, err);
    }
  },

  async fetch(_request: Request, _env: Env): Promise<Response> {
    return new Response(
      "Scheduled-only Worker. Test-fire: wrangler trigger ik-ad-generator-trigger --remote",
      { status: 405 },
    );
  },
};
