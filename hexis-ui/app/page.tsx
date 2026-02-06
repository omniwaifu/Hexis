"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Card } from "./components/ui/card";
import { Badge, GoalPriorityBadge, MemoryTypeBadge } from "./components/ui/badge";
import { ProgressBar } from "./components/ui/progress-bar";
import { PageHeader } from "./components/ui/page-header";
import { Spinner } from "./components/ui/spinner";

type StatusData = {
  agent_name?: string;
  configured?: boolean;
  energy?: number;
  max_energy?: number;
  mood?: string;
  valence?: number;
  arousal?: number;
  intensity?: number;
  heartbeat_active?: boolean;
  heartbeat_paused?: boolean;
  heartbeat_count?: number;
  last_heartbeat_at?: string;
  next_heartbeat_at?: string;
  drives?: { name: string; urgency: number; hours_since: number }[];
  emotional_trend?: { hour: string; valence: number; arousal: number }[];
  goals?: { id: string; content: string; priority: string; source: string }[];
  recent_heartbeats?: { id: string; narrative: string; emotional_valence: number; created_at: string }[];
  memory_health?: { type: string; count: number; avg_importance: number }[];
};

const moodColors: Record<string, string> = {
  enthusiastic: "accent",
  content: "teal",
  curious: "teal",
  calm: "teal",
  focused: "teal",
  neutral: "muted",
  concerned: "warning",
  subdued: "warning",
  distressed: "error",
  withdrawn: "error",
};

function urgencyColor(urgency: number): "accent" | "teal" | "green" | "amber" | "red" {
  if (urgency > 80) return "red";
  if (urgency > 60) return "amber";
  if (urgency > 40) return "accent";
  return "teal";
}

export default function Dashboard() {
  const router = useRouter();
  const [status, setStatus] = useState<StatusData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch("/api/status", { cache: "no-store" });
        if (!res.ok) throw new Error("Failed to load status");
        const data = await res.json();
        setStatus(data);

        // Redirect to init if not configured
        if (data.configured === false) {
          router.push("/init");
          return;
        }
      } catch {
        // If status API fails, try init status check
        try {
          const initRes = await fetch("/api/init/status", { cache: "no-store" });
          if (initRes.ok) {
            const initData = await initRes.json();
            if (initData?.status?.stage !== "complete") {
              router.push("/init");
              return;
            }
          }
        } catch {}
      } finally {
        setLoading(false);
      }
    };
    load();
    const interval = setInterval(() => {
      fetch("/api/status", { cache: "no-store" })
        .then((r) => r.json())
        .then(setStatus)
        .catch(() => {});
    }, 30000);
    return () => clearInterval(interval);
  }, [router]);

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner label="Loading..." />
      </div>
    );
  }

  if (!status) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Card className="max-w-md text-center">
          <p className="text-sm text-[var(--ink-soft)]">Unable to connect to the database.</p>
          <p className="mt-2 text-xs text-[var(--ink-soft)]">Make sure the Hexis stack is running.</p>
        </Card>
      </div>
    );
  }

  const totalMemories = (status.memory_health || []).reduce((sum, m) => sum + (m.count || 0), 0);

  return (
    <div className="app-shell min-h-screen">
      <div className="relative z-10 mx-auto max-w-6xl px-6 py-10">
        <PageHeader
          title="Dashboard"
          subtitle={`Welcome back. ${status.agent_name || "Hexis"} is ${status.heartbeat_paused ? "paused" : status.heartbeat_active ? "thinking" : "idle"}.`}
        />

        <div className="mt-8 grid gap-6 lg:grid-cols-[1fr_340px]">
          {/* Left column */}
          <div className="space-y-6">
            {/* Identity + Energy card */}
            <Card>
              <div className="flex items-start justify-between">
                <div>
                  <h2 className="font-display text-2xl">{status.agent_name || "Hexis"}</h2>
                  {status.mood && (
                    <Badge variant={(moodColors[status.mood] as any) || "muted"} className="mt-2">
                      {status.mood}
                      {status.valence !== null && status.valence !== undefined
                        ? ` (${status.valence.toFixed(2)})`
                        : ""}
                    </Badge>
                  )}
                </div>
                <div className="flex items-center gap-2 text-xs text-[var(--ink-soft)]">
                  <span
                    className={`inline-block h-2.5 w-2.5 rounded-full ${
                      status.heartbeat_paused
                        ? "bg-amber-400"
                        : status.heartbeat_active
                          ? "bg-green-400 animate-pulse"
                          : "bg-[var(--outline)]"
                    }`}
                  />
                  {status.heartbeat_paused
                    ? "Paused"
                    : status.heartbeat_active
                      ? "Active"
                      : "Idle"}
                </div>
              </div>

              <div className="mt-4">
                <ProgressBar
                  value={status.energy ?? 0}
                  max={status.max_energy ?? 20}
                  label="Energy"
                />
              </div>

              {status.heartbeat_count !== null && status.heartbeat_count !== undefined && (
                <p className="mt-3 text-xs text-[var(--ink-soft)]">
                  {status.heartbeat_count} heartbeats total
                  {status.last_heartbeat_at && (
                    <> &middot; last: {new Date(status.last_heartbeat_at).toLocaleString()}</>
                  )}
                </p>
              )}
            </Card>

            {/* Active goals */}
            <Card>
              <div className="flex items-center justify-between">
                <h3 className="font-display text-lg">Goals</h3>
                <Link href="/goals" className="text-xs text-[var(--accent-strong)] hover:underline">
                  View all
                </Link>
              </div>
              <div className="mt-4 space-y-3">
                {(status.goals || []).length === 0 ? (
                  <p className="text-sm text-[var(--ink-soft)]">No active goals.</p>
                ) : (
                  (status.goals || []).slice(0, 5).map((g) => (
                    <div key={g.id} className="flex items-center gap-3">
                      <GoalPriorityBadge priority={g.priority} />
                      <span className="text-sm">{g.content}</span>
                    </div>
                  ))
                )}
              </div>
            </Card>

            {/* Recent heartbeats */}
            <Card>
              <h3 className="font-display text-lg">Recent Heartbeats</h3>
              <div className="mt-4 space-y-3">
                {(status.recent_heartbeats || []).length === 0 ? (
                  <p className="text-sm text-[var(--ink-soft)]">No heartbeats yet.</p>
                ) : (
                  (status.recent_heartbeats || []).slice(0, 3).map((h) => (
                    <div
                      key={h.id}
                      className="rounded-2xl border border-[var(--outline)] bg-white p-4"
                    >
                      <p className="text-sm leading-relaxed">
                        {(h.narrative || "").slice(0, 200)}
                        {(h.narrative || "").length > 200 ? "..." : ""}
                      </p>
                      <p className="mt-2 text-xs text-[var(--ink-soft)]">
                        {h.created_at ? new Date(h.created_at).toLocaleString() : ""}
                        {h.emotional_valence !== null && h.emotional_valence !== undefined
                          ? ` \u00b7 valence: ${h.emotional_valence.toFixed(2)}`
                          : ""}
                      </p>
                    </div>
                  ))
                )}
              </div>
            </Card>
          </div>

          {/* Right column */}
          <div className="space-y-6">
            {/* Drives */}
            <Card>
              <h3 className="font-display text-lg">Drives</h3>
              <div className="mt-4 space-y-3">
                {(status.drives || []).length === 0 ? (
                  <p className="text-sm text-[var(--ink-soft)]">No drive data.</p>
                ) : (
                  (status.drives || []).map((d) => (
                    <ProgressBar
                      key={d.name}
                      value={d.urgency ?? 0}
                      max={100}
                      label={d.name}
                      color={urgencyColor(d.urgency ?? 0)}
                    />
                  ))
                )}
              </div>
            </Card>

            {/* Emotional trend */}
            <Card>
              <h3 className="font-display text-lg">Emotional Trend</h3>
              <div className="mt-4">
                {(status.emotional_trend || []).length === 0 ? (
                  <p className="text-sm text-[var(--ink-soft)]">No trend data yet.</p>
                ) : (
                  <div className="flex h-24 items-end gap-px">
                    {(status.emotional_trend || [])
                      .slice(0, 24)
                      .reverse()
                      .map((t, i) => {
                        const v = t.valence ?? 0;
                        const h = Math.max(4, Math.abs(v) * 100);
                        const color = v >= 0 ? "bg-[var(--teal)]" : "bg-[var(--accent)]";
                        return (
                          <div
                            key={i}
                            className={`flex-1 rounded-t ${color} transition-all`}
                            style={{ height: `${h}%` }}
                            title={`${t.hour}: valence ${v.toFixed(2)}`}
                          />
                        );
                      })}
                  </div>
                )}
                <p className="mt-2 text-xs text-[var(--ink-soft)]">24h valence history</p>
              </div>
            </Card>

            {/* Memory health */}
            <Card>
              <div className="flex items-center justify-between">
                <h3 className="font-display text-lg">Memory</h3>
                <Link href="/memories" className="text-xs text-[var(--accent-strong)] hover:underline">
                  Browse
                </Link>
              </div>
              <p className="mt-2 text-2xl font-display text-[var(--accent)]">
                {totalMemories}
                <span className="ml-2 text-sm font-normal text-[var(--ink-soft)]">total</span>
              </p>
              <div className="mt-4 flex flex-wrap gap-2">
                {(status.memory_health || []).map((m) => (
                  <div key={m.type} className="flex items-center gap-1.5">
                    <MemoryTypeBadge type={m.type} />
                    <span className="text-xs text-[var(--ink-soft)]">{m.count}</span>
                  </div>
                ))}
              </div>
            </Card>
          </div>
        </div>
      </div>
    </div>
  );
}
