import Link from "next/link";
import { ChevronLeft, ChevronRight } from "lucide-react";

export const PAGE_SIZE = 25;

export function pageSlice<T>(items: T[], page: number, size = PAGE_SIZE) {
  const safePage = Math.max(1, Math.min(page, Math.max(1, Math.ceil(items.length / size))));
  return { page: safePage, totalPages: Math.max(1, Math.ceil(items.length / size)), items: items.slice((safePage - 1) * size, safePage * size) };
}

export function Pagination({ page, totalPages, total, params = {} }: { page: number; totalPages: number; total: number; params?: Record<string, string | undefined> }) {
  const start = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1;
  const end = Math.min(page * PAGE_SIZE, total);
  const href = (next: number) => `?${new URLSearchParams(Object.entries({ ...params, page: String(next) }).filter((entry): entry is [string, string] => Boolean(entry[1]))).toString()}`;
  return <nav aria-label="Pagination" className="mt-4 flex flex-col gap-3 rounded-xl border border-violet-300/15 bg-[linear-gradient(90deg,rgba(130,92,246,.07),rgba(255,255,255,.018))] px-3 py-2.5 shadow-[inset_0_1px_0_rgba(255,255,255,.04)] sm:flex-row sm:items-center sm:justify-between">
    <div><p className="text-xs text-foreground/85">Showing <span className="font-semibold text-violet-200">{start}–{end}</span> of {total.toLocaleString("en-IN")} records</p><p className="mt-0.5 text-[10px] uppercase tracking-[.14em] text-muted">Page {page} of {totalPages} · {PAGE_SIZE} per page</p></div>
    <div className="flex gap-2">
      <Link aria-disabled={page === 1} tabIndex={page === 1 ? -1 : 0} href={page === 1 ? "#" : href(page - 1)} className="inline-flex items-center gap-1 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-foreground transition hover:border-accent/40 aria-disabled:pointer-events-none aria-disabled:opacity-35"><ChevronLeft className="h-3.5 w-3.5" /> Previous</Link>
      <Link aria-disabled={page === totalPages} tabIndex={page === totalPages ? -1 : 0} href={page === totalPages ? "#" : href(page + 1)} className="inline-flex items-center gap-1 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-foreground transition hover:border-accent/40 aria-disabled:pointer-events-none aria-disabled:opacity-35">Next <ChevronRight className="h-3.5 w-3.5" /></Link>
    </div>
  </nav>;
}
