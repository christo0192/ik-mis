/**
 * Shared health model — client-safe (no server-only import), used by the
 * Home page tiles, the system graph, and the caretaker bots so all three
 * agree on what "healthy / stale / failed / unknown" means from the same
 * MAX(date) freshness numbers (docs/dashboard-vision.md §2, §2b).
 */

export type Health = "healthy" | "stale" | "failed" | "unknown";

/** Weekly pipeline cadence: fresh within 8 days, stale beyond, unknown when never seen. */
export const STALE_AFTER_DAYS = 8;

export function healthOf(latestDate: string | null): Health {
  if (!latestDate) return "unknown";
  const then = new Date(latestDate).getTime();
  if (Number.isNaN(then)) return "unknown";
  const days = (Date.now() - then) / (1000 * 60 * 60 * 24);
  return days <= STALE_AFTER_DAYS ? "healthy" : "stale";
}

/** Severity ranking used when rolling several child healths up into one parent (dept) health. */
const SEVERITY: Record<Health, number> = {
  failed: 3,
  stale: 2,
  unknown: 1,
  healthy: 0,
};

/** Worst-of across a set of child healths. Empty input reads as "unknown" (nothing to roll up). */
export function worstHealth(healths: Health[]): Health {
  if (healths.length === 0) return "unknown";
  return healths.reduce((worst, h) => (SEVERITY[h] > SEVERITY[worst] ? h : worst), "healthy" as Health);
}

export const HEALTH_STYLE: Record<Health, { dot: string; text: string; label: string; hex: string }> = {
  healthy: { dot: "bg-success", text: "text-success", label: "fresh", hex: "#34d399" },
  stale: { dot: "bg-warning", text: "text-warning", label: "stale", hex: "#fbbf24" },
  failed: { dot: "bg-danger", text: "text-danger", label: "failed", hex: "#fb7185" },
  unknown: { dot: "bg-muted/40", text: "text-muted", label: "no data yet", hex: "#8b93a8" },
};
