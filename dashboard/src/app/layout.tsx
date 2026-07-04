import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";

import { SidebarNav } from "@/components/sidebar-nav";

const inter = Inter({
  variable: "--font-geist-sans",
  subsets: ["latin"],
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
  display: "swap",
});

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
      className={`${inter.variable} ${jetbrainsMono.variable} h-full antialiased`}
    >
      <body className="mc-backdrop min-h-full bg-background text-foreground">
        <div className="flex min-h-screen">
          {/* Sidebar */}
          <aside className="hidden w-56 shrink-0 flex-col border-r border-border bg-surface/60 backdrop-blur md:flex">
            <div className="flex h-16 items-center gap-2.5 border-b border-border px-5">
              <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent-strong text-sm font-bold text-white">
                MIS
              </span>
              <div className="leading-tight">
                <p className="text-sm font-semibold text-foreground">
                  Mission Control
                </p>
                <p className="text-[11px] text-muted">IK · Marketing Intelligence</p>
              </div>
            </div>
            <SidebarNav />
            <div className="mt-auto border-t border-border px-5 py-4">
              <p className="text-[11px] leading-relaxed text-muted">
                Reads live from BigQuery.
                <br />
                Writes limited to review status.
              </p>
            </div>
          </aside>

          {/* Main column */}
          <div className="flex min-w-0 flex-1 flex-col">
            <header className="sticky top-0 z-10 flex h-16 items-center justify-between border-b border-border bg-background/70 px-6 backdrop-blur">
              <div>
                <h1 className="text-base font-semibold tracking-tight text-foreground">
                  MIS — Mission Control
                </h1>
                <p className="text-xs text-muted">
                  Intelligence · Generation · Registry · Runs
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

            <main className="flex-1 px-6 py-6 md:px-8">{children}</main>

            <footer className="border-t border-border px-6 py-4 md:px-8">
              <p className="text-xs text-muted">
                MIS Mission Control <span className="text-muted/40">·</span>{" "}
                server-side reads, review-only writes
              </p>
            </footer>
          </div>
        </div>
      </body>
    </html>
  );
}
