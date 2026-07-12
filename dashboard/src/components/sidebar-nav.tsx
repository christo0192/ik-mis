"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import * as React from "react";
import {
  Activity,
  BookMarked,
  Gauge,
  Home,
  Radar,
  Sparkles,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/format";

interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  soon?: boolean;
}

const NAV: NavItem[] = [
  { href: "/", label: "Home", icon: Home },
  { href: "/intelligence", label: "Intelligence", icon: Radar },
  { href: "/generation", label: "Generation", icon: Sparkles },
  { href: "/registry", label: "Registry", icon: BookMarked },
  { href: "/runs", label: "Runs", icon: Activity },
  { href: "/performance", label: "Performance", icon: Gauge },
];

export function SidebarNav({ mobile = false }: { mobile?: boolean }) {
  const pathname = usePathname();

  return (
    <nav className={cn(mobile ? "grid grid-cols-6 gap-0.5 p-1.5" : "flex flex-col gap-1.5 px-3 py-5")} aria-label="Primary">
      {NAV.map((item) => {
        const active =
          item.href === "/"
            ? pathname === "/"
            : pathname.startsWith(item.href);
        const ItemIcon = item.icon;
        return (
          <Link
            key={item.href}
            href={item.href}
            prefetch
            aria-current={active ? "page" : undefined}
            className={cn(
              "group relative overflow-hidden rounded-xl font-medium transition-all duration-200",
              mobile ? "flex min-w-0 flex-col items-center gap-1 px-1 py-2 text-[9px]" : "flex items-center gap-3 px-3 py-2.5 text-sm",
              active
                ? "border border-violet-300/15 bg-[linear-gradient(90deg,rgba(130,92,246,.18),rgba(130,92,246,.04))] text-violet-100 shadow-[inset_3px_0_0_#a78bfa]"
                : "border border-transparent text-muted hover:border-white/[.05] hover:bg-white/[.035] hover:text-foreground",
            )}
          >
            <ItemIcon
              aria-hidden="true"
              strokeWidth={active ? 2.2 : 1.8}
              className={cn(
                "h-[18px] w-[18px] shrink-0",
                active ? "text-accent" : "text-muted/70",
              )}
            />
            <span className={cn(mobile && "max-w-full truncate")}>{item.label}</span>
            {item.soon ? (
              <span className="ml-auto rounded-full border border-border px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-muted/70">
                Soon
              </span>
            ) : null}
          </Link>
        );
      })}
    </nav>
  );
}
