export default function Loading() {
  return (
    <div className="animate-pulse space-y-8" role="status" aria-live="polite" aria-label="Loading dashboard data">
      <div className="space-y-3"><div className="h-6 w-52 rounded-lg bg-white/10" /><div className="h-3 w-[min(80%,560px)] rounded bg-white/[.06]" /></div>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[0, 1, 2, 3].map((item) => <div key={item} className="h-28 rounded-2xl border border-white/[.06] bg-white/[.025]" />)}
      </div>
      <div className="overflow-hidden rounded-2xl border border-white/[.06] bg-white/[.02]">
        <div className="h-12 border-b border-white/[.06] bg-white/[.025]" />
        {[0, 1, 2, 3, 4, 5].map((item) => <div key={item} className="mx-5 h-14 border-b border-white/[.045] last:border-0" />)}
      </div>
      <span className="sr-only">Loading live MIS data…</span>
    </div>
  );
}
