import "server-only";

import { existsSync, readdirSync, statSync } from "node:fs";
import path from "node:path";

/**
 * Resolves the local `generation/exports/` directory (negative-keyword CSVs
 * etc. written by the Generation-dept pipeline, MIS/generation/exports/).
 * Read-only, filesystem-local — this only works where the dashboard runs on
 * the same machine/volume as the pipeline (local dev today). In a hosted
 * deployment (Vercel) none of these paths exist, and every caller of this
 * module must treat that as "no exports available yet", not an error.
 */
const CANDIDATE_DIRS = [
  process.env.GENERATION_EXPORTS_DIR,
  "C:\\Users\\Admin\\Claude projects\\MIS\\generation\\exports",
  "/mnt/c/Users/Admin/Claude projects/MIS/generation/exports",
].filter((p): p is string => Boolean(p && p.trim().length > 0));

export function resolveExportsDir(): string | null {
  for (const dir of CANDIDATE_DIRS) {
    if (existsSync(dir)) return dir;
  }
  return null;
}

export interface ExportFileInfo {
  name: string;
  sizeBytes: number;
  modifiedAt: string;
}

/** Lists CSV files in the resolved exports dir, newest first. Empty array (never throws) when unavailable. */
export function listExportFiles(): ExportFileInfo[] {
  const dir = resolveExportsDir();
  if (!dir) return [];
  try {
    return readdirSync(dir)
      .filter((name) => name.toLowerCase().endsWith(".csv"))
      .map((name) => {
        const stat = statSync(path.join(dir, name));
        return {
          name,
          sizeBytes: stat.size,
          modifiedAt: stat.mtime.toISOString(),
        };
      })
      .sort((a, b) => b.modifiedAt.localeCompare(a.modifiedAt));
  } catch {
    return [];
  }
}
