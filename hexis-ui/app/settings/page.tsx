"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Card } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { PageHeader } from "../components/ui/page-header";
import { Spinner } from "../components/ui/spinner";

type SettingsData = {
  groups: Record<string, Record<string, any>>;
  llm: Record<string, any>;
  heartbeat: Record<string, any>;
  agent: Record<string, any>;
  tools: Record<string, any>;
};

const TABS = ["models", "heartbeat", "tools", "all"] as const;
type Tab = (typeof TABS)[number];

// Keys that should be redacted in display
const SENSITIVE_KEYS = ["api_key", "password", "secret", "token"];

function isSensitive(key: string): boolean {
  return SENSITIVE_KEYS.some((s) => key.toLowerCase().includes(s));
}

function formatValue(value: any, key: string): string {
  if (value === null || value === undefined) return "null";
  if (isSensitive(key)) return "********";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export default function SettingsPage() {
  const [data, setData] = useState<SettingsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<Tab>("models");

  const fetchSettings = useCallback(async () => {
    try {
      const res = await fetch("/api/settings", { cache: "no-store" });
      if (res.ok) setData(await res.json());
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSettings();
  }, [fetchSettings]);

  const toggleTool = async (toolKey: string, currentValue: any) => {
    const toolName = toolKey.replace("tools.", "").replace(".enabled", "");
    const enabled = currentValue !== true;
    await fetch("/api/settings/tools", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tool_name: toolName, enabled }),
    });
    fetchSettings();
  };

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner label="Loading settings..." />
      </div>
    );
  }

  if (!data) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Card className="max-w-md text-center">
          <p className="text-sm text-[var(--ink-soft)]">Unable to load settings.</p>
        </Card>
      </div>
    );
  }

  return (
    <div className="app-shell min-h-screen">
      <div className="relative z-10 mx-auto max-w-4xl px-6 py-10">
        <PageHeader title="Settings" subtitle="View and manage agent configuration" />

        {/* Tabs */}
        <div className="mt-6 flex gap-1 rounded-2xl bg-[var(--surface-strong)] p-1">
          {TABS.map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`flex-1 rounded-xl px-4 py-2 text-sm font-medium capitalize transition ${
                tab === t
                  ? "bg-white shadow-sm"
                  : "text-[var(--ink-soft)] hover:text-[var(--foreground)]"
              }`}
            >
              {t}
            </button>
          ))}
        </div>

        <div className="mt-6">
          {tab === "models" && (
            <Card>
              <div className="flex items-center justify-between">
                <h3 className="font-display text-lg">LLM Configuration</h3>
                <Link
                  href="/init"
                  className="text-xs text-[var(--accent-strong)] hover:underline"
                >
                  Re-run init
                </Link>
              </div>
              <div className="mt-4 space-y-2">
                {Object.entries(data.llm).length === 0 ? (
                  <p className="text-sm text-[var(--ink-soft)]">
                    No LLM configuration found. Run init to configure.
                  </p>
                ) : (
                  Object.entries(data.llm).map(([key, value]) => (
                    <div
                      key={key}
                      className="flex items-center justify-between rounded-xl border border-[var(--outline)] px-4 py-2.5"
                    >
                      <span className="text-sm font-medium">{key}</span>
                      <span
                        className={`text-sm ${
                          isSensitive(key)
                            ? "text-[var(--ink-soft)] opacity-50"
                            : ""
                        }`}
                      >
                        {formatValue(value, key)}
                      </span>
                    </div>
                  ))
                )}
              </div>
              {Object.entries(data.agent).length > 0 && (
                <>
                  <h4 className="mt-6 font-display text-base">Agent</h4>
                  <div className="mt-2 space-y-2">
                    {Object.entries(data.agent).map(([key, value]) => (
                      <div
                        key={key}
                        className="flex items-center justify-between rounded-xl border border-[var(--outline)] px-4 py-2.5"
                      >
                        <span className="text-sm font-medium">{key}</span>
                        <span className="text-sm">{formatValue(value, key)}</span>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </Card>
          )}

          {tab === "heartbeat" && (
            <Card>
              <h3 className="font-display text-lg">Heartbeat Configuration</h3>
              <p className="mt-1 text-xs text-[var(--ink-soft)]">
                Controls the autonomous cognitive loop timing and energy.
              </p>
              <div className="mt-4 space-y-2">
                {Object.entries(data.heartbeat).length === 0 ? (
                  <p className="text-sm text-[var(--ink-soft)]">
                    No heartbeat configuration found.
                  </p>
                ) : (
                  Object.entries(data.heartbeat).map(([key, value]) => (
                    <div
                      key={key}
                      className="flex items-center justify-between rounded-xl border border-[var(--outline)] px-4 py-2.5"
                    >
                      <span className="text-sm font-medium">{key}</span>
                      <span className="text-sm">{formatValue(value, key)}</span>
                    </div>
                  ))
                )}
              </div>
            </Card>
          )}

          {tab === "tools" && (
            <Card>
              <h3 className="font-display text-lg">Tools</h3>
              <p className="mt-1 text-xs text-[var(--ink-soft)]">
                Enable or disable tools available to the agent.
              </p>
              <div className="mt-4 space-y-2">
                {Object.entries(data.tools).length === 0 ? (
                  <p className="text-sm text-[var(--ink-soft)]">
                    No tool configuration found.
                  </p>
                ) : (
                  Object.entries(data.tools)
                    .sort(([a], [b]) => a.localeCompare(b))
                    .map(([key, value]) => {
                      const isEnabled = key.endsWith(".enabled");
                      return (
                        <div
                          key={key}
                          className="flex items-center justify-between rounded-xl border border-[var(--outline)] px-4 py-2.5"
                        >
                          <span className="text-sm font-medium">{key}</span>
                          {isEnabled ? (
                            <button
                              onClick={() => toggleTool(key, value)}
                              className="flex items-center gap-2"
                            >
                              <Badge variant={value === true ? "teal" : "muted"}>
                                {value === true ? "Enabled" : "Disabled"}
                              </Badge>
                            </button>
                          ) : (
                            <span className="text-sm">{formatValue(value, key)}</span>
                          )}
                        </div>
                      );
                    })
                )}
              </div>
            </Card>
          )}

          {tab === "all" && (
            <div className="space-y-4">
              {Object.entries(data.groups)
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([prefix, entries]) => (
                  <Card key={prefix}>
                    <h3 className="font-display text-lg capitalize">{prefix}</h3>
                    <div className="mt-3 space-y-1.5">
                      {Object.entries(entries).map(([key, value]) => (
                        <div
                          key={key}
                          className="flex items-center justify-between rounded-lg px-3 py-2 text-sm odd:bg-[var(--surface-strong)]"
                        >
                          <span className="font-medium">{key}</span>
                          <span
                            className={`max-w-[50%] truncate text-right ${
                              isSensitive(key) ? "opacity-50" : ""
                            }`}
                          >
                            {formatValue(value, key)}
                          </span>
                        </div>
                      ))}
                    </div>
                  </Card>
                ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
