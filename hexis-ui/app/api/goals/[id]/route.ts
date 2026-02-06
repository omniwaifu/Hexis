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
      `SELECT id, type, content, importance, metadata, created_at, last_accessed
       FROM memories
       WHERE id = $1::uuid AND type = 'goal'`,
      id
    );

    if (rows.length === 0) {
      return NextResponse.json({ error: "Goal not found" }, { status: 404 });
    }

    const g = rows[0];
    const meta = normalizeJsonValue(g.metadata) || {};

    return NextResponse.json({
      id: g.id,
      title: meta.title || g.content,
      description: meta.description,
      source: meta.source,
      priority: meta.priority,
      progress: meta.progress || [],
      last_touched: meta.last_touched,
      created_at: g.created_at,
    });
  } catch (error: any) {
    console.error("Goal detail error:", error);
    return NextResponse.json(
      { error: error?.message || "Failed to fetch goal" },
      { status: 500 }
    );
  }
}

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;
    const body = await req.json();

    if (body.priority) {
      await prisma.$queryRawUnsafe(
        `SELECT change_goal_priority($1::uuid, $2::goal_priority, $3)`,
        id,
        body.priority,
        body.reason || null
      );
    }

    if (body.progress_note) {
      await prisma.$queryRawUnsafe(
        `SELECT add_goal_progress($1::uuid, $2)`,
        id,
        body.progress_note
      );
    }

    return NextResponse.json({ ok: true });
  } catch (error: any) {
    console.error("Update goal error:", error);
    return NextResponse.json(
      { error: error?.message || "Failed to update goal" },
      { status: 500 }
    );
  }
}
