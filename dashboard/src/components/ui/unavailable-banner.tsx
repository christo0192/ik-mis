/**
 * Honest "this query didn't run" banner — distinct from EmptyState (which
 * means the query succeeded with zero rows). Used wherever a `ce_*` table
 * query returns a non-null `error` from `safeQuery`
 * (docs/dashboard-vision.md §5: never fake a clean empty state when the
 * underlying read actually failed).
 */
export function UnavailableBanner({
  label,
  error,
}: {
  label: string;
  error: string;
}) {
  return (
    <div className="rounded-xl border border-dashed border-warning/30 bg-warning/5 p-4 text-sm">
      <p className="font-medium text-warning">
        {label} — no data yet, pipeline hasn&apos;t run live
      </p>
      <p className="mt-1 text-xs leading-relaxed text-muted">{error}</p>
    </div>
  );
}
