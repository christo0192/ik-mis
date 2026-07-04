import { NextResponse } from "next/server";

import { listExportFiles, resolveExportsDir } from "@/lib/exports-dir";

/**
 * Lists CSV files in the Generation-dept `exports/` directory (e.g.
 * negatives_<date>.csv) so the Generation page's Rejects/Negatives tab can
 * offer a download link. Read-only. Gracefully returns an empty list (never
 * an error) when the directory isn't present — e.g. a hosted deployment that
 * doesn't share a filesystem with the pipeline.
 */
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  const available = resolveExportsDir() !== null;
  const files = listExportFiles();
  return NextResponse.json({ ok: true, available, files });
}
