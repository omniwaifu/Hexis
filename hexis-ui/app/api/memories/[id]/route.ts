import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { normalizeJsonValue } from "@/lib/db";

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;

    const rows: any[] = await prisma.$queryRawUnsafe(
      `SELECT id, type, content, importance, trust_level, access_count,
              decay_rate, status, source, metadata, created_at, last_accessed
       FROM memories
       WHERE id = $1::uuid`,
      id
    );

    if (rows.length === 0) {
      return NextResponse.json({ error: "Memory not found" }, { status: 404 });
    }

    const m = rows[0];

    // Touch the memory to update access tracking
    await prisma.$queryRawUnsafe(`SELECT touch_memories(ARRAY[$1::uuid])`, id);

    return NextResponse.json({
      id: m.id,
      type: m.type,
      content: m.content,
      importance: m.importance != null ? Number(m.importance) : null,
      trust_level: m.trust_level != null ? Number(m.trust_level) : null,
      access_count: m.access_count != null ? Number(m.access_count) : null,
      decay_rate: m.decay_rate != null ? Number(m.decay_rate) : null,
      status: m.status,
      source: m.source,
      metadata: normalizeJsonValue(m.metadata),
      created_at: m.created_at,
      last_accessed: m.last_accessed,
    });
  } catch (error: any) {
    console.error("Memory detail API error:", error);
    return NextResponse.json(
      { error: error?.message || "Failed to fetch memory" },
      { status: 500 }
    );
  }
}
