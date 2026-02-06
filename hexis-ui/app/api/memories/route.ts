import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { normalizeJsonValue } from "@/lib/db";

export async function GET(req: NextRequest) {
  try {
    const url = new URL(req.url);
    const q = url.searchParams.get("q") || "";
    const type = url.searchParams.get("type") || "";
    const limit = Math.min(parseInt(url.searchParams.get("limit") || "20", 10), 100);
    const offset = parseInt(url.searchParams.get("offset") || "0", 10);
    const sort = url.searchParams.get("sort") || "recent";

    let memories: any[];

    if (q.trim()) {
      // Semantic search via fast_recall
      const typeFilter = type ? [type] : null;
      if (typeFilter) {
        memories = await prisma.$queryRawUnsafe(
          `SELECT memory_id AS id, content, memory_type AS type, score, source
           FROM recall_memories_filtered($1, $2, $3::memory_type[], 0.0)`,
          q,
          limit + offset,
          `{${type}}`
        );
      } else {
        memories = await prisma.$queryRawUnsafe(
          `SELECT memory_id AS id, content, memory_type AS type, score, source
           FROM fast_recall($1, $2)`,
          q,
          limit + offset
        );
      }
      // Apply offset manually since fast_recall doesn't support it
      memories = memories.slice(offset, offset + limit);
    } else {
      // Filtered listing
      const orderClause =
        sort === "importance"
          ? "ORDER BY importance DESC"
          : sort === "oldest"
            ? "ORDER BY created_at ASC"
            : "ORDER BY created_at DESC";

      const typeClause = type ? `AND type = '${type}'::memory_type` : "";

      memories = await prisma.$queryRawUnsafe(
        `SELECT id, type, content, importance, trust_level, access_count,
                created_at, last_accessed, metadata
         FROM memories
         WHERE status = 'active' ${typeClause}
         ${orderClause}
         LIMIT $1 OFFSET $2`,
        limit,
        offset
      );
    }

    // Get memory health stats
    const healthRows: any[] = await prisma.$queryRawUnsafe("SELECT * FROM memory_health");

    const totalCount = healthRows.reduce(
      (sum: number, h: any) => sum + Number(h.total_memories || 0),
      0
    );

    return NextResponse.json({
      memories: memories.map((m: any) => ({
        id: m.id,
        type: m.type ?? m.memory_type,
        content: m.content,
        importance: m.importance != null ? Number(m.importance) : null,
        trust_level: m.trust_level != null ? Number(m.trust_level) : null,
        score: m.score != null ? Number(m.score) : null,
        access_count: m.access_count != null ? Number(m.access_count) : null,
        created_at: m.created_at ?? null,
        last_accessed: m.last_accessed ?? null,
        metadata: normalizeJsonValue(m.metadata),
      })),
      health: healthRows.map((h: any) => ({
        type: h.type,
        count: Number(h.total_memories || 0),
        avg_importance: h.avg_importance != null ? Number(h.avg_importance) : null,
        avg_relevance: h.avg_relevance != null ? Number(h.avg_relevance) : null,
      })),
      total: totalCount,
      limit,
      offset,
    });
  } catch (error: any) {
    console.error("Memories API error:", error);
    return NextResponse.json(
      { error: error?.message || "Failed to fetch memories" },
      { status: 500 }
    );
  }
}
