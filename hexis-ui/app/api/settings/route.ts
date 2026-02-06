import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { normalizeJsonValue } from "@/lib/db";

export async function GET() {
  try {
    const rows: any[] = await prisma.$queryRawUnsafe(
      `SELECT key, value FROM config ORDER BY key`
    );

    // Group by prefix
    const groups: Record<string, Record<string, any>> = {};
    for (const row of rows) {
      const val = normalizeJsonValue(row.value);
      const prefix = row.key.split(".")[0] || "other";
      if (!groups[prefix]) groups[prefix] = {};
      groups[prefix][row.key] = val;
    }

    // Extract specific sections for easier UI consumption
    const llm = groups["llm"] || {};
    const heartbeat = groups["heartbeat"] || {};
    const agent = groups["agent"] || {};
    const tools = groups["tools"] || {};

    return NextResponse.json({
      groups,
      llm,
      heartbeat,
      agent,
      tools,
    });
  } catch (error: any) {
    console.error("Settings API error:", error);
    return NextResponse.json(
      { error: error?.message || "Failed to fetch settings" },
      { status: 500 }
    );
  }
}
