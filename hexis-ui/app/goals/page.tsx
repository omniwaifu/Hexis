"use client";

import { useCallback, useEffect, useState } from "react";
import { Card } from "../components/ui/card";
import { Badge, GoalPriorityBadge } from "../components/ui/badge";
import { PageHeader } from "../components/ui/page-header";
import { Spinner } from "../components/ui/spinner";

type Goal = {
  id: string;
  title: string;
  description: string | null;
  source: string | null;
  priority: string;
  last_touched: string | null;
  progress_count: number;
  is_blocked: boolean;
  created_at: string | null;
};

const PRIORITIES = ["active", "queued", "backburner"] as const;
const SOURCES = ["user_request", "curiosity", "identity", "derived", "external"] as const;

export default function GoalsPage() {
  const [goals, setGoals] = useState<Goal[]>([]);
  const [loading, setLoading] = useState(true);
  const [showNew, setShowNew] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newPriority, setNewPriority] = useState<string>("queued");
  const [newSource, setNewSource] = useState<string>("user_request");
  const [creating, setCreating] = useState(false);
  const [progressGoalId, setProgressGoalId] = useState<string | null>(null);
  const [progressNote, setProgressNote] = useState("");

  const fetchGoals = useCallback(async () => {
    try {
      const res = await fetch("/api/goals", { cache: "no-store" });
      if (!res.ok) throw new Error("Failed");
      const data = await res.json();
      setGoals(data.goals || []);
    } catch {
      setGoals([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchGoals();
  }, [fetchGoals]);

  const createGoal = async () => {
    if (!newTitle.trim() || creating) return;
    setCreating(true);
    try {
      const res = await fetch("/api/goals", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: newTitle.trim(),
          description: newDesc.trim() || null,
          priority: newPriority,
          source: newSource,
        }),
      });
      if (res.ok) {
        setNewTitle("");
        setNewDesc("");
        setShowNew(false);
        fetchGoals();
      }
    } finally {
      setCreating(false);
    }
  };

  const changePriority = async (id: string, priority: string, reason?: string) => {
    await fetch(`/api/goals/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ priority, reason }),
    });
    fetchGoals();
  };

  const addProgress = async (id: string) => {
    if (!progressNote.trim()) return;
    await fetch(`/api/goals/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ progress_note: progressNote.trim() }),
    });
    setProgressGoalId(null);
    setProgressNote("");
    fetchGoals();
  };

  const grouped = {
    active: goals.filter((g) => g.priority === "active"),
    queued: goals.filter((g) => g.priority === "queued"),
    backburner: goals.filter((g) => g.priority === "backburner"),
  };

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner label="Loading goals..." />
      </div>
    );
  }

  return (
    <div className="app-shell min-h-screen">
      <div className="relative z-10 mx-auto max-w-6xl px-6 py-10">
        <div className="flex items-start justify-between">
          <PageHeader
            title="Goals"
            subtitle={`${goals.length} goals across ${Object.values(grouped).filter((g) => g.length > 0).length} priorities`}
          />
          <button
            onClick={() => setShowNew(!showNew)}
            className="mt-1 rounded-full bg-[var(--foreground)] px-5 py-2.5 text-sm font-medium text-white transition hover:bg-[var(--accent-strong)]"
          >
            {showNew ? "Cancel" : "New Goal"}
          </button>
        </div>

        {/* New goal form */}
        {showNew && (
          <Card className="mt-6 fade-up">
            <h3 className="font-display text-lg">Create Goal</h3>
            <div className="mt-4 space-y-3">
              <input
                type="text"
                className="w-full rounded-2xl border border-[var(--outline)] bg-white px-4 py-2.5 text-sm focus:border-[var(--accent)] focus:outline-none"
                placeholder="Goal title..."
                value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
              />
              <textarea
                className="w-full rounded-2xl border border-[var(--outline)] bg-white px-4 py-2.5 text-sm focus:border-[var(--accent)] focus:outline-none"
                placeholder="Description (optional)..."
                rows={2}
                value={newDesc}
                onChange={(e) => setNewDesc(e.target.value)}
              />
              <div className="flex gap-3">
                <select
                  value={newPriority}
                  onChange={(e) => setNewPriority(e.target.value)}
                  className="rounded-2xl border border-[var(--outline)] bg-white px-4 py-2.5 text-sm"
                >
                  {PRIORITIES.map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </select>
                <select
                  value={newSource}
                  onChange={(e) => setNewSource(e.target.value)}
                  className="rounded-2xl border border-[var(--outline)] bg-white px-4 py-2.5 text-sm"
                >
                  {SOURCES.map((s) => (
                    <option key={s} value={s}>
                      {s.replace("_", " ")}
                    </option>
                  ))}
                </select>
                <button
                  onClick={createGoal}
                  disabled={creating || !newTitle.trim()}
                  className="rounded-2xl bg-[var(--accent-strong)] px-6 py-2.5 text-sm font-medium text-white transition hover:bg-[var(--accent)] disabled:opacity-50"
                >
                  {creating ? "Creating..." : "Create"}
                </button>
              </div>
            </div>
          </Card>
        )}

        {/* Three-column layout */}
        <div className="mt-8 grid gap-6 md:grid-cols-3">
          {PRIORITIES.map((priority) => (
            <div key={priority}>
              <div className="mb-4 flex items-center gap-2">
                <GoalPriorityBadge priority={priority} />
                <span className="text-xs text-[var(--ink-soft)]">
                  {grouped[priority].length}
                </span>
              </div>
              <div className="space-y-3">
                {grouped[priority].length === 0 ? (
                  <Card>
                    <p className="text-sm text-[var(--ink-soft)]">No {priority} goals.</p>
                  </Card>
                ) : (
                  grouped[priority].map((g) => (
                    <Card key={g.id}>
                      <p className="text-sm font-medium">{g.title}</p>
                      {g.description && (
                        <p className="mt-1 text-xs text-[var(--ink-soft)] line-clamp-2">
                          {g.description}
                        </p>
                      )}
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {g.source && (
                          <Badge variant="muted">{g.source.replace("_", " ")}</Badge>
                        )}
                        {g.progress_count > 0 && (
                          <Badge variant="teal">{g.progress_count} updates</Badge>
                        )}
                        {g.is_blocked && <Badge variant="warning">blocked</Badge>}
                      </div>

                      {/* Actions */}
                      <div className="mt-3 flex flex-wrap gap-1.5">
                        {priority !== "active" && (
                          <button
                            onClick={() => changePriority(g.id, "active")}
                            className="rounded-lg border border-[var(--outline)] px-2 py-1 text-xs hover:bg-[var(--surface-strong)]"
                          >
                            Activate
                          </button>
                        )}
                        {priority !== "queued" && priority !== "active" && (
                          <button
                            onClick={() => changePriority(g.id, "queued")}
                            className="rounded-lg border border-[var(--outline)] px-2 py-1 text-xs hover:bg-[var(--surface-strong)]"
                          >
                            Queue
                          </button>
                        )}
                        {priority !== "backburner" && (
                          <button
                            onClick={() => changePriority(g.id, "backburner")}
                            className="rounded-lg border border-[var(--outline)] px-2 py-1 text-xs hover:bg-[var(--surface-strong)]"
                          >
                            Backburner
                          </button>
                        )}
                        <button
                          onClick={() => changePriority(g.id, "completed", "Marked complete via UI")}
                          className="rounded-lg border border-green-200 px-2 py-1 text-xs text-green-700 hover:bg-green-50"
                        >
                          Complete
                        </button>
                        <button
                          onClick={() =>
                            setProgressGoalId(progressGoalId === g.id ? null : g.id)
                          }
                          className="rounded-lg border border-[var(--outline)] px-2 py-1 text-xs hover:bg-[var(--surface-strong)]"
                        >
                          + Progress
                        </button>
                      </div>

                      {/* Progress note input */}
                      {progressGoalId === g.id && (
                        <div className="mt-2 flex gap-2 fade-up">
                          <input
                            type="text"
                            className="flex-1 rounded-lg border border-[var(--outline)] bg-white px-3 py-1.5 text-xs focus:border-[var(--accent)] focus:outline-none"
                            placeholder="Progress note..."
                            value={progressNote}
                            onChange={(e) => setProgressNote(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") addProgress(g.id);
                            }}
                          />
                          <button
                            onClick={() => addProgress(g.id)}
                            className="rounded-lg bg-[var(--teal)] px-3 py-1.5 text-xs text-white"
                          >
                            Save
                          </button>
                        </div>
                      )}

                      {g.last_touched && (
                        <p className="mt-2 text-[10px] text-[var(--ink-soft)]">
                          Last: {new Date(g.last_touched).toLocaleDateString()}
                        </p>
                      )}
                    </Card>
                  ))
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
