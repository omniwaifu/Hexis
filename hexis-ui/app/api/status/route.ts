import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { normalizeJsonValue } from "@/lib/db";

export async function GET() {
  try {
    // Aggregate status from DB views
    const [
      cogHealthRows,
      heartbeatRows,
      driveRows,
      emotionRows,
      trendRows,
      goalRows,
      recentHbRows,
      memHealthRows,
      configuredRows,
      profileRows,
    ] = await Promise.all([
      prisma.$queryRawUnsafe("SELECT * FROM cognitive_health LIMIT 1"),
      prisma.$queryRawUnsafe("SELECT * FROM heartbeat_state LIMIT 1"),
      prisma.$queryRawUnsafe("SELECT * FROM drive_status"),
      prisma.$queryRawUnsafe("SELECT * FROM current_emotional_state LIMIT 1"),
      prisma.$queryRawUnsafe("SELECT * FROM emotional_trend ORDER BY hour DESC LIMIT 24"),
      prisma.$queryRawUnsafe("SELECT * FROM active_goals"),
      prisma.$queryRawUnsafe("SELECT * FROM recent_heartbeats LIMIT 5"),
      prisma.$queryRawUnsafe("SELECT * FROM memory_health"),
      prisma.$queryRawUnsafe("SELECT is_agent_configured() AS configured"),
      prisma.$queryRawUnsafe("SELECT get_init_profile() AS profile"),
    ]);

    const cogHealth = normalize(cogHealthRows);
    const heartbeat = normalize(heartbeatRows);
    const emotion = normalize(emotionRows);
    const profile = normalizeJsonValue(
      (profileRows as any)?.[0]?.profile
    );

    const agentName =
      (profile as any)?.agent?.name ||
      cogHealth?.identity ||
      "Hexis";

    return NextResponse.json({
      agent_name: agentName,
      configured: (configuredRows as any)?.[0]?.configured ?? false,

      // Energy
      energy: toNum(cogHealth?.current_energy ?? heartbeat?.current_energy),
      max_energy: toNum(cogHealth?.max_energy ?? 20),

      // Heartbeat
      heartbeat_active: !!heartbeat?.active_heartbeat_id,
      heartbeat_paused: heartbeat?.is_paused ?? false,
      heartbeat_count: toNum(heartbeat?.heartbeat_count),
      last_heartbeat_at: heartbeat?.last_heartbeat_at ?? null,
      next_heartbeat_at: heartbeat?.next_heartbeat_at ?? null,

      // Mood
      mood: cogHealth?.primary_emotion ?? emotion?.primary_emotion ?? null,
      valence: toNum(emotion?.valence),
      arousal: toNum(emotion?.arousal),
      dominance: toNum(emotion?.dominance),
      intensity: toNum(emotion?.intensity),

      // Drives
      drives: (driveRows as any[]).map((d: any) => ({
        name: d.drive_name ?? d.name,
        urgency: toNum(d.urgency_percent),
        hours_since: toNum(d.hours_since_satisfied),
      })),

      // Emotional trend (24h hourly)
      emotional_trend: (trendRows as any[]).map((t: any) => ({
        hour: t.hour,
        valence: toNum(t.avg_valence),
        arousal: toNum(t.avg_arousal),
      })),

      // Goals
      goals: (goalRows as any[]).map((g: any) => ({
        id: g.id,
        content: g.content,
        priority: g.priority,
        source: g.source,
        metadata: normalizeJsonValue(g.metadata),
      })),

      // Recent heartbeats
      recent_heartbeats: (recentHbRows as any[]).map((h: any) => ({
        id: h.id,
        narrative: h.content ?? h.narrative,
        emotional_valence: toNum(h.emotional_valence),
        created_at: h.created_at,
      })),

      // Memory health
      memory_health: (memHealthRows as any[]).map((m: any) => ({
        type: m.memory_type ?? m.type,
        count: toNum(m.memory_count ?? m.count),
        avg_importance: toNum(m.avg_importance),
      })),
    });
  } catch (error: any) {
    console.error("Status API error:", error);
    return NextResponse.json(
      { error: error?.message || "Failed to fetch status" },
      { status: 500 }
    );
  }
}

function normalize(rows: unknown): any {
  if (Array.isArray(rows) && rows.length > 0) return rows[0];
  return {};
}

function toNum(v: unknown): number | null {
  if (v === null || v === undefined) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}
