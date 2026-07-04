"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/format";

const TABS = [
  { href: "/intelligence", label: "Overview" },
  { href: "/intelligence/jobs", label: "Jobs" },
  { href: "/intelligence/social", label: "Social" },
  { href: "/intelligence/blog", label: "Blog" },
  { href: "/intelligence/competitor", label: "Competitor" },
];

/** Condensed sub-tab strip for the Intelligence section (former standalone pages). */
export function IntelligenceTabs() {
  const pathname = usePathname();

  return (
    <nav
      aria-label="Intelligence sections"
      className="mb-6 flex flex-wrap gap-1 border-b border-border pb-0"
    >
      {TABS.map((tab) => {
        const active =
          tab.href === "/intelligence"
            ? pathname === "/intelligence"
            : pathname.startsWith(tab.href);
        return (
          <Link
            key={tab.href}
            href={tab.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "-mb-px rounded-t-md border-b-2 px-3 py-2 text-sm font-medium transition-colors",
              active
                ? "border-accent text-accent"
                : "border-transparent text-muted hover:text-foreground",
            )}
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
