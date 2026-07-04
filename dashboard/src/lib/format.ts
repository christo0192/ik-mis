/**
 * Client-safe formatting + class utilities. NO server imports here — this
 * module is imported by both server and client components.
 */

/** Tiny classnames joiner (avoids pulling in clsx/tailwind-merge). */
export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

/** Format an INR amount compactly: 1500000 -> "₹15.0L", 2500000 -> "₹25.0L". */
export function formatINR(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  if (value >= 1_00_00_000) return `₹${(value / 1_00_00_000).toFixed(2)}Cr`;
  if (value >= 1_00_000) return `₹${(value / 1_00_000).toFixed(1)}L`;
  if (value >= 1_000) return `₹${(value / 1_000).toFixed(0)}K`;
  return `₹${value.toLocaleString("en-IN")}`;
}

/** Render a salary range, gracefully handling missing min/max. */
export function formatSalaryRange(
  min: number | null | undefined,
  max: number | null | undefined,
): string {
  const hasMin = min !== null && min !== undefined && Number.isFinite(min);
  const hasMax = max !== null && max !== undefined && Number.isFinite(max);
  if (!hasMin && !hasMax) return "—";
  if (hasMin && hasMax) return `${formatINR(min)} – ${formatINR(max)}`;
  return formatINR(hasMin ? min : max);
}

/** "2026-06-03" -> "3 Jun 2026". Returns the raw string on parse failure. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

/** Compact number formatting: 12345 -> "12.3K". */
export function formatCompact(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return value.toLocaleString("en-IN");
}
