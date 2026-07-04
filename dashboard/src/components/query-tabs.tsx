"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";

import { cn } from "@/lib/format";

/** Underlined tab strip driven by a URL query param (e.g. `?tab=copy`), so every tab is linkable. */
export function QueryTabs({
  options,
  paramName = "tab",
  defaultValue,
}: {
  options: Array<{ value: string; label: string; count?: number }>;
  paramName?: string;
  defaultValue: string;
}) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const current = searchParams.get(paramName) ?? defaultValue;

  function hrefFor(value: string) {
    const params = new URLSearchParams(searchParams.toString());
    if (value === defaultValue) params.delete(paramName);
    else params.set(paramName, value);
    const qs = params.toString();
    return qs ? `${pathname}?${qs}` : pathname;
  }

  return (
    <nav className="flex flex-wrap gap-1 border-b border-border" aria-label="Review queue tabs">
      {options.map((opt) => {
        const active = current === opt.value;
        return (
          <Link
            key={opt.value}
            href={hrefFor(opt.value)}
            aria-current={active ? "page" : undefined}
            className={cn(
              "-mb-px flex items-center gap-1.5 rounded-t-md border-b-2 px-3 py-2 text-sm font-medium transition-colors",
              active
                ? "border-accent text-accent"
                : "border-transparent text-muted hover:text-foreground",
            )}
          >
            {opt.label}
            {opt.count !== undefined ? (
              <span
                className={cn(
                  "rounded-full px-1.5 py-0.5 text-[10px] font-semibold tabular-nums",
                  active ? "bg-accent/20 text-accent" : "bg-white/5 text-muted",
                )}
              >
                {opt.count}
              </span>
            ) : null}
          </Link>
        );
      })}
    </nav>
  );
}
