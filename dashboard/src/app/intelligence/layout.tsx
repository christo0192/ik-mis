import { IntelligenceTabs } from "@/components/intelligence-tabs";

/**
 * Intelligence section shell — condenses the former standalone market-research
 * pages (Overview/Jobs/Social/Blog/Competitor) into sub-tabs under one
 * top-level nav item, per docs/dashboard-vision.md §3.
 */
export default function IntelligenceLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div>
      <IntelligenceTabs />
      {children}
    </div>
  );
}
