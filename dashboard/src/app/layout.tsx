import type { Metadata } from "next";
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
          {/* Sidebar */}
          <aside className="hidden w-[248px] shrink-0 flex-col border-r border-white/[0.07] bg-black/35 backdrop-blur-2xl md:flex">
            <div className="flex h-[76px] items-center gap-3 border-b border-white/[0.07] px-5">
              <span className="relative flex h-10 w-10 items-center justify-center rounded-xl border border-violet-200/25 bg-[linear-gradient(145deg,#b9a8ff,#6e45e8)] text-xs font-black tracking-tight text-white shadow-[0_0_24px_rgba(130,92,246,.35)]">
                IK<span className="absolute -right-1 -top-1 h-2.5 w-2.5 rounded-full border-2 border-[#08080e] bg-success" />
              </span>
              <div className="leading-tight">
                <p className="text-sm font-semibold text-foreground">
                  Mission Control
                </p>
                <p className="text-[10px] uppercase tracking-[.14em] text-muted">Intelligence OS</p>
              </div>
            </div>
            <SidebarNav />
            <div className="m-3 mt-auto rounded-2xl border border-violet-300/15 bg-violet-400/[.06] p-4">
              <p className="text-[10px] font-semibold uppercase tracking-[.16em] text-violet-200">System status</p>
              <p className="mt-1.5 text-[11px] leading-relaxed text-muted">Live BigQuery telemetry<br />Review-only controls</p>
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
