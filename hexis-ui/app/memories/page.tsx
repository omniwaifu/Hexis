"use client";

import { useCallback, useEffect, useState } from "react";
import { Card } from "../components/ui/card";
import { Badge, MemoryTypeBadge } from "../components/ui/badge";
import { PageHeader } from "../components/ui/page-header";
import { Spinner } from "../components/ui/spinner";

type Memory = {
  id: string;
  type: string;
  content: string;
  importance: number | null;
  trust_level: number | null;
  score: number | null;
  access_count: number | null;
  created_at: string | null;
  last_accessed: string | null;
  metadata: any;
};

type HealthEntry = {
  type: string;
  count: number;
  avg_importance: number | null;
};

const MEMORY_TYPES = ["", "episodic", "semantic", "procedural", "strategic", "worldview", "goal"];
const SORT_OPTIONS = [
  { value: "recent", label: "Most Recent" },
  { value: "importance", label: "Importance" },
  { value: "oldest", label: "Oldest" },
];

export default function MemoriesPage() {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [health, setHealth] = useState<HealthEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [sort, setSort] = useState("recent");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Memory | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailData, setDetailData] = useState<any>(null);
  const limit = 20;

  const fetchMemories = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (query) params.set("q", query);
      if (typeFilter) params.set("type", typeFilter);
      params.set("sort", sort);
      params.set("limit", String(limit));
      params.set("offset", String(offset));

      const res = await fetch(`/api/memories?${params}`, { cache: "no-store" });
      if (!res.ok) throw new Error("Failed to fetch");
      const data = await res.json();
      setMemories(data.memories || []);
      setHealth(data.health || []);
      setTotal(data.total || 0);
    } catch {
      setMemories([]);
    } finally {
      setLoading(false);
    }
  }, [query, typeFilter, sort, offset]);

  useEffect(() => {
    fetchMemories();
  }, [fetchMemories]);

  const handleSearch = () => {
    setOffset(0);
    setQuery(searchInput);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") handleSearch();
  };

  const openDetail = async (mem: Memory) => {
    setSelected(mem);
    setDetailLoading(true);
    try {
      const res = await fetch(`/api/memories/${mem.id}`, { cache: "no-store" });
      if (res.ok) {
        setDetailData(await res.json());
      }
    } catch {
      setDetailData(null);
    } finally {
      setDetailLoading(false);
    }
  };

  const detail = detailData || selected;

  return (
    <div className="app-shell min-h-screen">
      <div className="relative z-10 mx-auto max-w-6xl px-6 py-10">
        <PageHeader title="Memories" subtitle={`${total} memories across all types`} />

        {/* Health bar */}
        <div className="mt-6 flex flex-wrap gap-3">
          {health.map((h) => (
            <button
              key={h.type}
              onClick={() => {
                setTypeFilter(typeFilter === h.type ? "" : h.type);
                setOffset(0);
              }}
              className={`flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs transition ${
                typeFilter === h.type
                  ? "border-[var(--accent)] bg-[var(--accent)] text-white"
                  : "border-[var(--outline)] bg-white hover:border-[var(--accent)]"
              }`}
            >
              <MemoryTypeBadge type={h.type} />
              <span>{h.count}</span>
            </button>
          ))}
        </div>

        {/* Search + filters */}
        <div className="mt-6 flex flex-col gap-3 sm:flex-row">
          <div className="flex flex-1 gap-2">
            <input
              type="text"
              className="flex-1 rounded-2xl border border-[var(--outline)] bg-white px-4 py-2.5 text-sm focus:border-[var(--accent)] focus:outline-none"
              placeholder="Semantic search..."
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              onKeyDown={handleKeyDown}
            />
            <button
              onClick={handleSearch}
              className="rounded-2xl bg-[var(--foreground)] px-5 py-2.5 text-sm font-medium text-white transition hover:bg-[var(--accent-strong)]"
            >
              Search
            </button>
          </div>
          <select
            value={sort}
            onChange={(e) => {
              setSort(e.target.value);
              setOffset(0);
            }}
            className="rounded-2xl border border-[var(--outline)] bg-white px-4 py-2.5 text-sm"
          >
            {SORT_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>

        {/* Content area */}
        <div className="mt-8 grid gap-6 lg:grid-cols-[1fr_380px]">
          {/* Memory list */}
          <div className="space-y-3">
            {loading ? (
              <div className="flex justify-center py-12">
                <Spinner label="Searching memories..." />
              </div>
            ) : memories.length === 0 ? (
              <Card>
                <p className="text-sm text-[var(--ink-soft)]">No memories found.</p>
              </Card>
            ) : (
              memories.map((m) => (
                <button
                  key={m.id}
                  onClick={() => openDetail(m)}
                  className={`block w-full text-left transition ${
                    selected?.id === m.id ? "scale-[1.01]" : ""
                  }`}
                >
                  <Card
                    className={
                      selected?.id === m.id ? "ring-2 ring-[var(--accent)]" : ""
                    }
                  >
                    <div className="flex items-start gap-3">
                      <MemoryTypeBadge type={m.type} />
                      <div className="min-w-0 flex-1">
                        <p className="text-sm leading-relaxed">
                          {(m.content || "").slice(0, 200)}
                          {(m.content || "").length > 200 ? "..." : ""}
                        </p>
                        <div className="mt-2 flex flex-wrap gap-2 text-xs text-[var(--ink-soft)]">
                          {m.importance != null && (
                            <span>importance: {m.importance.toFixed(2)}</span>
                          )}
                          {m.score != null && <span>score: {m.score.toFixed(3)}</span>}
                          {m.created_at && (
                            <span>{new Date(m.created_at).toLocaleDateString()}</span>
                          )}
                        </div>
                      </div>
                    </div>
                  </Card>
                </button>
              ))
            )}

            {/* Pagination */}
            {!loading && memories.length > 0 && (
              <div className="flex items-center justify-between pt-2">
                <button
                  onClick={() => setOffset(Math.max(0, offset - limit))}
                  disabled={offset === 0}
                  className="rounded-xl border border-[var(--outline)] px-4 py-2 text-sm disabled:opacity-40"
                >
                  Previous
                </button>
                <span className="text-xs text-[var(--ink-soft)]">
                  Showing {offset + 1}–{offset + memories.length}
                </span>
                <button
                  onClick={() => setOffset(offset + limit)}
                  disabled={memories.length < limit}
                  className="rounded-xl border border-[var(--outline)] px-4 py-2 text-sm disabled:opacity-40"
                >
                  Next
                </button>
              </div>
            )}
          </div>

          {/* Detail panel */}
          <div className="lg:sticky lg:top-10 lg:self-start">
            {selected ? (
              <Card className="slide-in">
                {detailLoading ? (
                  <Spinner label="Loading..." />
                ) : detail ? (
                  <div className="space-y-4">
                    <div className="flex items-center gap-2">
                      <MemoryTypeBadge type={detail.type} />
                      {detail.status && detail.status !== "active" && (
                        <Badge variant="warning">{detail.status}</Badge>
                      )}
                    </div>
                    <p className="text-sm leading-relaxed whitespace-pre-wrap">
                      {detail.content}
                    </p>
                    <div className="space-y-1 text-xs text-[var(--ink-soft)]">
                      {detail.importance != null && (
                        <p>Importance: {Number(detail.importance).toFixed(2)}</p>
                      )}
                      {detail.trust_level != null && (
                        <p>Trust: {Number(detail.trust_level).toFixed(2)}</p>
                      )}
                      {detail.access_count != null && (
                        <p>Accessed: {detail.access_count} times</p>
                      )}
                      {detail.source && <p>Source: {detail.source}</p>}
                      {detail.created_at && (
                        <p>Created: {new Date(detail.created_at).toLocaleString()}</p>
                      )}
                      {detail.last_accessed && (
                        <p>Last accessed: {new Date(detail.last_accessed).toLocaleString()}</p>
                      )}
                    </div>
                    {detail.metadata && Object.keys(detail.metadata).length > 0 && (
                      <details className="text-xs">
                        <summary className="cursor-pointer text-[var(--ink-soft)]">
                          Metadata
                        </summary>
                        <pre className="mt-2 max-h-48 overflow-auto rounded-xl bg-[var(--surface-strong)] p-3">
                          {JSON.stringify(detail.metadata, null, 2)}
                        </pre>
                      </details>
                    )}
                  </div>
                ) : (
                  <p className="text-sm text-[var(--ink-soft)]">Could not load details.</p>
                )}
              </Card>
            ) : (
              <Card>
                <p className="text-sm text-[var(--ink-soft)]">
                  Select a memory to view details.
                </p>
              </Card>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
