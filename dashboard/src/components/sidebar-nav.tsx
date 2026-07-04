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

export function SidebarNav() {
  const pathname = usePathname();

  return (
    <nav className="flex flex-col gap-1 px-3 py-3" aria-label="Primary">
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
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
              active
                ? "bg-accent/15 text-accent"
                : "text-muted hover:bg-white/5 hover:text-foreground",
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
            {item.label}
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
