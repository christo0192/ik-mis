import { NextResponse } from "next/server";

import { DATASET, PROJECT_ID, query } from "@/lib/bq";

// BigQuery access is server-side only and must not be statically cached.
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

interface TableMeta {
  table_id: string;
  row_count: number;
}

export async function GET() {
  try {
    const rows = await query<{ table_id: string; row_count: number | string }>(
      `SELECT table_id, row_count
       FROM \`${PROJECT_ID}.${DATASET}.__TABLES__\`
       ORDER BY table_id`,
    );

    const tables: TableMeta[] = rows.map((r) => ({
      table_id: r.table_id,
      // BigQuery may return INT64 counts as strings; normalize to number.
      row_count: Number(r.row_count),
    }));

    return NextResponse.json({ ok: true, tables });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ ok: false, error: message }, { status: 500 });
  }
}
