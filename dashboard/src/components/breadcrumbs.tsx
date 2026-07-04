import Link from "next/link";

export interface Crumb {
  label: string;
  href?: string;
}

/** Simple breadcrumb trail for the drill-down chain (segment -> campaign -> asset). */
export function Breadcrumbs({ items }: { items: Crumb[] }) {
  return (
    <nav aria-label="Breadcrumb" className="mb-4 flex flex-wrap items-center gap-1.5 text-xs">
      {items.map((item, i) => (
        <span key={`${item.label}-${i}`} className="flex items-center gap-1.5">
          {i > 0 ? <span className="text-muted/50">/</span> : null}
          {item.href ? (
            <Link href={item.href} className="text-muted transition-colors hover:text-foreground">
              {item.label}
            </Link>
          ) : (
            <span className="font-medium text-foreground">{item.label}</span>
          )}
        </span>
      ))}
    </nav>
  );
}
