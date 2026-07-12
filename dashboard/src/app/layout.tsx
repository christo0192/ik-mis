import type { Metadata } from "next";
import Image from "next/image";
import "./globals.css";

import { SidebarNav } from "@/components/sidebar-nav";

export const metadata: Metadata = {
  title: "MIS — Mission Control",
  description:
    "Marketing Intelligence System mission control: pipeline health, generation review queue, campaign registry, and market intelligence — read from BigQuery.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className="h-full antialiased"
    >
      <body className="mc-backdrop min-h-full bg-background text-foreground">
        <div className="flex min-h-screen">
          {/* Sidebar — a collapsed 76px rail (holds layout width so content never
              reflows) whose inner panel is fixed to the viewport and expands to
              248px on hover, overlaying the content. Pure CSS; labels fade in via
              the named `group/sidebar` hover. */}
          <aside className="hidden w-[76px] shrink-0 md:block" aria-label="Sidebar navigation">
            <div className="group/sidebar fixed inset-y-0 left-0 z-50 flex w-[76px] flex-col overflow-hidden border-r border-white/[0.07] bg-black/40 backdrop-blur-2xl transition-[width] duration-200 ease-out hover:w-[248px] hover:shadow-[10px_0_45px_rgba(0,0,0,.55)]">
              <div className="flex h-[76px] items-center gap-3 border-b border-white/[0.07] px-[18px]">
                <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-white p-1.5 shadow-[0_0_22px_rgba(74,144,217,.35)]">
                  <Image
                    src="/ik-logo.png"
                    alt="Interview Kickstart"
                    width={36}
                    height={26}
                    priority
                    className="h-auto w-full object-contain"
                  />
                </span>
                <div className="min-w-0 whitespace-nowrap opacity-0 transition-opacity duration-150 group-hover/sidebar:opacity-100">
                  <p className="text-sm font-semibold text-foreground">Mission Control</p>
                  <p className="text-[10px] uppercase tracking-[.14em] text-muted">Interview Kickstart</p>
                </div>
              </div>
              <SidebarNav />
              <div className="m-3 mt-auto whitespace-nowrap rounded-2xl border border-violet-300/15 bg-violet-400/[.06] p-4 opacity-0 transition-opacity duration-150 group-hover/sidebar:opacity-100">
                <p className="text-[10px] font-semibold uppercase tracking-[.16em] text-violet-200">System status</p>
                <p className="mt-1.5 text-[11px] leading-relaxed text-muted">Live BigQuery telemetry<br />Review-only controls</p>
              </div>
            </div>
          </aside>

          {/* Main column */}
          <div className="flex min-w-0 flex-1 flex-col">
            <header className="sticky top-0 z-40 flex h-[76px] items-center justify-between border-b border-white/[0.07] bg-background/70 px-5 backdrop-blur-2xl md:px-8">
              <div>
                <h1 className="text-base font-semibold tracking-tight text-foreground">
                  Command overview
                </h1>
                <p className="text-xs text-muted">
                  Marketing intelligence system
                </p>
              </div>
              <div className="hidden items-center gap-2 rounded-full border border-success/30 bg-success/10 px-3 py-1 text-xs font-medium text-success sm:flex">
                <span className="relative flex h-1.5 w-1.5">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success opacity-60 motion-reduce:hidden" />
                  <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-success" />
                </span>
                Live from BigQuery
              </div>
            </header>

            <main className="mx-auto w-full max-w-[1680px] flex-1 px-4 py-6 pb-24 sm:px-6 md:px-8 md:py-8">{children}</main>

            <footer className="border-t border-border px-6 py-4 md:px-8">
              <p className="text-xs text-muted">
                MIS Mission Control <span className="text-muted/40">·</span>{" "}
                server-side reads, review-only writes
              </p>
            </footer>
          </div>
          <div className="fixed inset-x-2 bottom-2 z-50 rounded-2xl border border-white/10 bg-[#090910]/90 shadow-[0_18px_55px_rgba(0,0,0,.6)] backdrop-blur-2xl md:hidden">
            <SidebarNav mobile />
          </div>
        </div>
      </body>
    </html>
  );
}
