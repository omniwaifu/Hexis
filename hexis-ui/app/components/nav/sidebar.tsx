"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { ProgressBar } from "../ui/progress-bar";
import { Badge } from "../ui/badge";

type StatusData = {
  agent_name?: string;
  energy?: number;
  max_energy?: number;
  mood?: string;
  valence?: number;
  heartbeat_active?: boolean;
  heartbeat_paused?: boolean;
  configured?: boolean;
};

const navItems = [
  { href: "/", label: "Dashboard", icon: "\u25a3" },
  { href: "/chat", label: "Chat", icon: "\u25c6" },
  { href: "/memories", label: "Memories", icon: "\u25c9" },
  { href: "/goals", label: "Goals", icon: "\u25b2" },
  { href: "/settings", label: "Settings", icon: "\u2699" },
];

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

export function Sidebar() {
  const pathname = usePathname();
  const [status, setStatus] = useState<StatusData>({});

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch("/api/status", { cache: "no-store" });
        if (res.ok) {
          const data = await res.json();
          setStatus(data);
        }
      } catch {}
    };
    load();
    const interval = setInterval(load, 30000);
    return () => clearInterval(interval);
  }, []);

  return (
    <aside className="fixed left-0 top-0 z-20 flex h-screen w-56 flex-col border-r border-[var(--outline)] bg-[var(--surface)] px-4 py-6">
      {/* Logo / Agent name */}
      <div className="mb-8">
        <p className="text-xs uppercase tracking-[0.3em] text-[var(--teal)]">
          Hexis
        </p>
        <p className="mt-1 font-display text-lg text-[var(--foreground)]">
          {status.agent_name || "Agent"}
        </p>
      </div>

      {/* Navigation */}
      <nav className="flex-1 space-y-1">
        {navItems.map((item) => {
          const isActive =
            item.href === "/"
              ? pathname === "/"
              : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition ${
                isActive
                  ? "bg-[var(--surface-strong)] font-medium text-[var(--foreground)]"
                  : "text-[var(--ink-soft)] hover:bg-[var(--surface-strong)] hover:text-[var(--foreground)]"
              }`}
            >
              <span className="text-base">{item.icon}</span>
              {item.label}
            </Link>
          );
        })}
      </nav>

      {/* Status footer */}
      <div className="space-y-4 border-t border-[var(--outline)] pt-4">
        {/* Energy bar */}
        {status.energy !== undefined && (
          <ProgressBar
            value={status.energy}
            max={status.max_energy || 20}
            label="Energy"
          />
        )}

        {/* Mood */}
        {status.mood && (
          <div className="flex items-center justify-between text-xs">
            <span className="text-[var(--ink-soft)]">Mood</span>
            <Badge variant={moodColors[status.mood] as any || "muted"}>
              {status.mood}
            </Badge>
          </div>
        )}

        {/* Heartbeat indicator */}
        <div className="flex items-center gap-2 text-xs text-[var(--ink-soft)]">
          <span
            className={`inline-block h-2 w-2 rounded-full ${
              status.heartbeat_paused
                ? "bg-amber-400"
                : status.heartbeat_active
                  ? "bg-green-400 animate-pulse"
                  : "bg-[var(--outline)]"
            }`}
          />
          {status.heartbeat_paused
            ? "Heartbeat paused"
            : status.heartbeat_active
              ? "Heartbeat active"
              : "Heartbeat idle"}
        </div>
      </div>
    </aside>
  );
}
