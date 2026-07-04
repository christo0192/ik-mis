import { NextResponse } from "next/server";
import { createReadStream, existsSync, statSync } from "node:fs";
import path from "node:path";

import { resolveExportsDir } from "@/lib/exports-dir";

/**
 * Streams one CSV file from the Generation-dept `exports/` directory.
 * Filename is strictly sanitized (basename only, `.csv` extension only, no
 * path separators) before it ever touches the filesystem — this only ever
 * serves a file directly inside the resolved exports directory, never an
 * arbitrary path.
 */
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ file: string }> },
) {
  const { file } = await params;
  const safeName = path.basename(file);

  if (
    !safeName ||
    safeName !== file ||
    !safeName.toLowerCase().endsWith(".csv")
  ) {
    return NextResponse.json(
      { ok: false, error: "Invalid export filename." },
      { status: 400 },
    );
  }

  const dir = resolveExportsDir();
  if (!dir) {
    return NextResponse.json(
      { ok: false, error: "Exports directory is not available on this deployment." },
      { status: 404 },
    );
  }

  const fullPath = path.join(dir, safeName);
  // Belt-and-suspenders: confirm the resolved path is still inside `dir`.
  if (!fullPath.startsWith(path.normalize(dir))) {
    return NextResponse.json({ ok: false, error: "Invalid path." }, { status: 400 });
  }
  if (!existsSync(fullPath) || !statSync(fullPath).isFile()) {
    return NextResponse.json({ ok: false, error: "File not found." }, { status: 404 });
  }

  const MAX_EXPORT_BYTES = 50 * 1024 * 1024; // 50MB cap — these are negative-keyword CSVs, never expected to be huge.
  const stat = statSync(fullPath);
  if (stat.size > MAX_EXPORT_BYTES) {
    return NextResponse.json(
      { ok: false, error: "Export file exceeds the 50MB download cap." },
      { status: 413 },
    );
  }

  const stream = createReadStream(fullPath);
  const webStream = new ReadableStream({
    start(controller) {
      stream.on("data", (chunk) => controller.enqueue(chunk));
      stream.on("end", () => controller.close());
      stream.on("error", (err) => controller.error(err));
    },
    cancel() {
      stream.destroy();
    },
  });

  return new Response(webStream, {
    headers: {
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": `attachment; filename="${safeName}"`,
      "Content-Length": String(stat.size),
    },
  });
}
