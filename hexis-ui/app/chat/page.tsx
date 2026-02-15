"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Card } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { PageHeader } from "../components/ui/page-header";
import { Spinner } from "../components/ui/spinner";

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
};

type LogEvent = {
  id: string;
  kind: "log" | "stream" | "error";
  title: string;
  detail: string;
  streamId?: string;
  ts: number;
};

type SsePayload = Record<string, unknown>;

const promptAddendaOptions = [
  { id: "philosophy", label: "Philosophy Grounding" },
  { id: "letter", label: "Letter From Claude" },
];

const SESSION_KEY = "hexis-chat-messages";

function loadSession(): ChatMessage[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function saveSession(messages: ChatMessage[]) {
  if (typeof window === "undefined") return;
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(messages));
  } catch {
    // ignore quota errors
  }
}

// Simple markdown-ish rendering: bold, italic, code, line breaks
function renderMarkdown(text: string) {
  if (!text) return null;

  const parts: React.ReactNode[] = [];
  const lines = text.split("\n");

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // Code block detection (simple)
    if (line.startsWith("```")) {
      // Find closing fence
      const codeLines: string[] = [];
      let j = i + 1;
      while (j < lines.length && !lines[j].startsWith("```")) {
        codeLines.push(lines[j]);
        j++;
      }
      parts.push(
        <pre
          key={`code-${i}`}
          className="my-2 overflow-x-auto rounded-xl bg-[var(--surface-strong)] p-3 text-xs"
        >
          <code>{codeLines.join("\n")}</code>
        </pre>
      );
      i = j; // skip past closing fence
      continue;
    }

    // Inline formatting
    const formatted = line
      .replace(/`([^`]+)`/g, '<code class="rounded bg-[var(--surface-strong)] px-1.5 py-0.5 text-xs">$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\*([^*]+)\*/g, "<em>$1</em>");

    parts.push(
      <span key={`line-${i}`}>
        <span dangerouslySetInnerHTML={{ __html: formatted }} />
        {i < lines.length - 1 && <br />}
      </span>
    );
  }

  return <>{parts}</>;
}

export default function ChatPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [events, setEvents] = useState<LogEvent[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [ready, setReady] = useState<boolean | null>(null);
  const [promptAddenda, setPromptAddenda] = useState<string[]>([]);
  const [currentPhase, setCurrentPhase] = useState<string | null>(null);
  const [showSearchConfig, setShowSearchConfig] = useState(false);
  const [searchConfigValue, setSearchConfigValue] = useState("");
  const [searchConfigSaving, setSearchConfigSaving] = useState(false);
  const [searchConfigError, setSearchConfigError] = useState<string | null>(null);
  const [searchConfigNotice, setSearchConfigNotice] = useState<string | null>(null);
  const [historyIndex, setHistoryIndex] = useState<number | null>(null);
  const [historyDraft, setHistoryDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const historyPayload = useMemo(
    () =>
      messages
        .filter((msg) => msg.content.trim())
        .map((msg) => ({ role: msg.role, content: msg.content })),
    [messages]
  );

  // Load session on mount
  useEffect(() => {
    const saved = loadSession();
    if (saved.length > 0) setMessages(saved);
  }, []);

  // Save session on message change
  useEffect(() => {
    if (messages.length > 0) saveSession(messages);
  }, [messages]);

  useEffect(() => {
    const load = async () => {
      const res = await fetch("/api/init/status", { cache: "no-store" });
      if (!res.ok) {
        setReady(false);
        return;
      }
      const data = await res.json();
      setReady(data?.status?.stage === "complete");
    };
    load().catch(() => setReady(false));
  }, []);

  useEffect(() => {
    if (!scrollRef.current) return;
    scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages]);

  useEffect(() => {
    if (!logRef.current) return;
    logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [events]);

  useEffect(() => {
    const latestAssistant = [...messages]
      .reverse()
      .find((msg) => msg.role === "assistant" && msg.content);
    if (latestAssistant && isSearchToolMisconfigured(latestAssistant.content)) {
      setShowSearchConfig(true);
    }
  }, [messages]);

  const appendLog = (event: LogEvent) => {
    setEvents((prev) => [...prev, event]);
  };

  const appendStreamToken = (streamId: string, text: string) => {
    setEvents((prev) => {
      const idx = prev.findIndex((evt) => evt.streamId === streamId && evt.kind === "stream");
      if (idx === -1) {
        return [
          ...prev,
          {
            id: crypto.randomUUID(),
            kind: "stream",
            title: streamLabel(streamId),
            detail: text,
            streamId,
            ts: Date.now(),
          },
        ];
      }
      const next = [...prev];
      next[idx] = { ...next[idx], detail: next[idx].detail + text };
      return next;
    });
  };

  const updateAssistantMessage = (assistantId: string, text: string) => {
    setMessages((prev) =>
      prev.map((msg) =>
        msg.id === assistantId ? { ...msg, content: msg.content + text } : msg
      )
    );
  };

  const handleConfigureSearchTool = async () => {
    const value = searchConfigValue.trim();
    if (!value) {
      setSearchConfigError("Enter a Tavily key or env reference (for example: env:TAVILY_API_KEY).");
      return;
    }

    setSearchConfigSaving(true);
    setSearchConfigError(null);
    setSearchConfigNotice(null);
    try {
      const payload = value.startsWith("env:")
        ? { key_ref: value, enable: true }
        : { api_key: value, enable: true };
      const res = await fetch("/api/settings/tools/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data?.error || `Failed with status ${res.status}`);
      }
      appendLog({
        id: crypto.randomUUID(),
        kind: "log",
        title: "Search Tool",
        detail: "Configured web_search. Retry your question to run live search.",
        ts: Date.now(),
      });
      setShowSearchConfig(false);
      setSearchConfigValue("");
      setSearchConfigNotice("Search tool configured. Retry your question.");
    } catch (err: unknown) {
      setSearchConfigError(
        err instanceof Error ? err.message : "Failed to configure search tool."
      );
    } finally {
      setSearchConfigSaving(false);
    }
  };

  const handleSend = async () => {
    if (!input.trim() || sending) return;

    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: input.trim(),
    };
    const assistantMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: "assistant",
      content: "",
    };
    setMessages((prev) => [...prev, userMessage, assistantMessage]);
    setInput("");
    setHistoryIndex(null);
    setHistoryDraft("");
    setSending(true);
    setCurrentPhase(null);
    setSearchConfigNotice(null);

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: userMessage.content,
          history: historyPayload,
          prompt_addenda: promptAddenda,
        }),
      });
      if (!res.ok || !res.body) {
        appendLog({
          id: crypto.randomUUID(),
          kind: "error",
          title: "Chat error",
          detail: `Failed to reach chat endpoint (${res.status}).`,
          ts: Date.now(),
        });
        setSending(false);
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop() || "";
        for (const part of parts) {
          const lines = part.split("\n");
          let eventType = "message";
          let data = "";
          for (const line of lines) {
            if (line.startsWith("event:")) {
              eventType = line.replace("event:", "").trim();
            }
            if (line.startsWith("data:")) {
              data += line.replace("data:", "").trim();
            }
          }
          if (!data) continue;
          let payload: SsePayload = {};
          try {
            const parsed = JSON.parse(data);
            payload =
              parsed && typeof parsed === "object" && !Array.isArray(parsed)
                ? (parsed as SsePayload)
                : { raw: data };
          } catch {
            payload = { raw: data };
          }

          if (eventType === "token") {
            const phase = asString(payload.phase);
            const text = asString(payload.text);
            setCurrentPhase(phase);
            appendStreamToken(phase, text);
            if (phase === "conscious_final" && text) {
              updateAssistantMessage(assistantMessage.id, text);
              if (isSearchToolMisconfigured(text)) {
                setShowSearchConfig(true);
              }
            }
          }

          if (eventType === "phase_start") {
            const phase = asString(payload.phase, "phase");
            setCurrentPhase(phase);
            appendLog({
              id: crypto.randomUUID(),
              kind: "log",
              title: streamLabel(phase),
              detail: "started",
              ts: Date.now(),
            });
          }

          if (eventType === "log") {
            const detail = asString(payload.detail);
            appendLog({
              id: asString(payload.id) || crypto.randomUUID(),
              kind: "log",
              title: asString(payload.title) || asString(payload.kind) || "log",
              detail,
              ts: Date.now(),
            });
            if (isSearchToolMisconfigured(detail)) {
              setShowSearchConfig(true);
            }
          }

          if (eventType === "error") {
            const detail = asString(payload.message, "Unknown error");
            appendLog({
              id: crypto.randomUUID(),
              kind: "error",
              title: "Error",
              detail,
              ts: Date.now(),
            });
            if (isSearchToolMisconfigured(String(detail))) {
              setShowSearchConfig(true);
            }
          }
        }
      }
    } catch (err: unknown) {
      appendLog({
        id: crypto.randomUUID(),
        kind: "error",
        title: "Chat error",
        detail: err instanceof Error ? err.message : "Unknown error",
        ts: Date.now(),
      });
    } finally {
      setSending(false);
      setCurrentPhase(null);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    const userHistory = messages
      .filter((msg) => msg.role === "user" && msg.content.trim())
      .map((msg) => msg.content);

    if (e.key === "ArrowUp" && userHistory.length > 0) {
      e.preventDefault();
      let nextIndex = historyIndex;
      if (nextIndex === null) {
        setHistoryDraft(input);
        nextIndex = userHistory.length - 1;
      } else {
        nextIndex = Math.max(0, nextIndex - 1);
      }
      setHistoryIndex(nextIndex);
      setInput(userHistory[nextIndex] ?? "");
      requestAnimationFrame(() => {
        const el = textareaRef.current;
        if (el) {
          const pos = el.value.length;
          el.setSelectionRange(pos, pos);
        }
      });
      return;
    }

    if (e.key === "ArrowDown" && historyIndex !== null) {
      e.preventDefault();
      if (historyIndex < userHistory.length - 1) {
        const nextIndex = historyIndex + 1;
        setHistoryIndex(nextIndex);
        setInput(userHistory[nextIndex] ?? "");
      } else {
        setHistoryIndex(null);
        setInput(historyDraft);
      }
      requestAnimationFrame(() => {
        const el = textareaRef.current;
        if (el) {
          const pos = el.value.length;
          el.setSelectionRange(pos, pos);
        }
      });
      return;
    }

    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  if (ready === false) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Card className="max-w-md text-center">
          <h1 className="font-display text-2xl">Initialization Required</h1>
          <p className="mt-3 text-sm text-[var(--ink-soft)]">
            Complete the initialization ritual before entering the main chat.
          </p>
          <a
            className="mt-6 inline-flex rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white"
            href="/init"
          >
            Go to Initialization
          </a>
        </Card>
      </div>
    );
  }

  if (ready === null) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner label="Loading status..." />
      </div>
    );
  }

  return (
    <div className="app-shell min-h-screen">
      <div className="relative z-10 mx-auto flex min-h-screen max-w-6xl flex-col gap-6 px-6 py-10 lg:flex-row">
        <section className="flex flex-1 flex-col gap-4">
          <PageHeader
            title="Conversation"
            subtitle={sending ? "Streaming..." : "Idle"}
          />

          {/* Thinking indicator */}
          {sending && currentPhase && (
            <div className="flex items-center gap-3 rounded-2xl border border-[var(--outline)] bg-white px-4 py-3 fade-up">
              <Spinner />
              <span className="text-sm text-[var(--ink-soft)]">
                {phaseDescription(currentPhase)}
              </span>
            </div>
          )}

          {showSearchConfig && (
            <Card className="border-[var(--accent)]/40 bg-white">
              <h3 className="font-display text-lg">Enable Web Search</h3>
              <p className="mt-1 text-sm text-[var(--ink-soft)]">
                This response indicates web search is not configured. Add a Tavily API key now, or use an env reference like <code>env:TAVILY_API_KEY</code>.
              </p>
              <div className="mt-3 flex flex-col gap-2 sm:flex-row">
                <input
                  type="text"
                  value={searchConfigValue}
                  onChange={(e) => setSearchConfigValue(e.target.value)}
                  placeholder="tvly-... or env:TAVILY_API_KEY"
                  className="flex-1 rounded-xl border border-[var(--outline)] px-3 py-2 text-sm focus:border-[var(--accent)] focus:outline-none"
                />
                <button
                  onClick={handleConfigureSearchTool}
                  disabled={searchConfigSaving}
                  className="rounded-xl bg-[var(--foreground)] px-4 py-2 text-sm font-semibold text-white disabled:opacity-60"
                >
                  {searchConfigSaving ? "Saving..." : "Save & Enable"}
                </button>
                <a
                  href="/settings"
                  className="rounded-xl border border-[var(--outline)] px-4 py-2 text-center text-sm"
                >
                  Open Settings
                </a>
              </div>
              {searchConfigError ? (
                <p className="mt-2 text-xs text-red-600">{searchConfigError}</p>
              ) : null}
            </Card>
          )}

          {searchConfigNotice && (
            <div className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
              {searchConfigNotice}
            </div>
          )}

          <Card className="flex flex-1 flex-col overflow-hidden !p-0">
            <div className="flex-1 space-y-4 overflow-y-auto p-6" ref={scrollRef}>
              {messages.length === 0 ? (
                <div className="rounded-2xl border border-dashed border-[var(--outline)] p-6 text-sm text-[var(--ink-soft)]">
                  Start the first exchange with Hexis.
                </div>
              ) : null}
              {messages.map((msg) => (
                <div
                  key={msg.id}
                  className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm shadow-sm ${
                    msg.role === "user"
                      ? "ml-auto bg-[var(--accent-strong)] text-white"
                      : "bg-white text-[var(--foreground)]"
                  }`}
                >
                  {msg.role === "assistant" ? (
                    <div className="leading-relaxed">
                      {msg.content ? renderMarkdown(msg.content) : (
                        <span className="animate-pulse-slow text-[var(--ink-soft)]">...</span>
                      )}
                    </div>
                  ) : (
                    <p className="whitespace-pre-wrap">{msg.content}</p>
                  )}
                </div>
              ))}
            </div>
            <div className="border-t border-[var(--outline)] p-4">
              <div className="flex gap-3">
                <textarea
                  ref={textareaRef}
                  className="min-h-[48px] max-h-[120px] flex-1 resize-none rounded-2xl border border-[var(--outline)] bg-white px-4 py-3 text-sm focus:border-[var(--accent)] focus:outline-none"
                  placeholder="Talk with Hexis... (Enter to send, Shift+Enter for newline)"
                  value={input}
                  onChange={(e) => {
                    if (historyIndex !== null) {
                      setHistoryIndex(null);
                    }
                    setInput(e.target.value);
                  }}
                  onKeyDown={handleKeyDown}
                  rows={1}
                />
                <button
                  className="self-end rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)] disabled:opacity-50"
                  onClick={handleSend}
                  disabled={sending || !input.trim()}
                >
                  Send
                </button>
              </div>
            </div>
          </Card>
        </section>

        <aside className="flex w-full flex-col gap-4 lg:w-80">
          <Card>
            <h2 className="font-display text-lg">Prompt Addenda</h2>
            <p className="mt-1 text-xs text-[var(--ink-soft)]">
              Add optional modules to the conscious system prompt.
            </p>
            <div className="mt-3 space-y-2">
              {promptAddendaOptions.map((option) => (
                <label key={option.id} className="flex items-center gap-3 text-sm">
                  <input
                    type="checkbox"
                    className="h-4 w-4 accent-[var(--accent-strong)]"
                    checked={promptAddenda.includes(option.id)}
                    onChange={() =>
                      setPromptAddenda((prev) =>
                        prev.includes(option.id)
                          ? prev.filter((item) => item !== option.id)
                          : [...prev, option.id]
                      )
                    }
                  />
                  {option.label}
                </label>
              ))}
            </div>
          </Card>

          <Card className="flex flex-1 flex-col overflow-hidden !p-0">
            <div className="border-b border-[var(--outline)] p-4">
              <h2 className="font-display text-lg">LLM Activity</h2>
              <p className="text-xs text-[var(--ink-soft)]">
                Streaming tokens, tool calls, and memory IO.
              </p>
            </div>
            <div className="flex-1 overflow-y-auto p-4" ref={logRef}>
              {events.length === 0 ? (
                <p className="text-xs text-[var(--ink-soft)]">No activity yet.</p>
              ) : (
                <div className="space-y-2">
                  {events.map((event) => (
                    <div
                      key={event.id}
                      className={`rounded-xl border px-3 py-2 ${
                        event.kind === "error"
                          ? "border-red-200 bg-red-50 text-red-700"
                          : event.kind === "stream"
                            ? "border-[var(--outline)] bg-[var(--surface)]"
                            : "border-[var(--outline)] bg-white"
                      }`}
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-[11px] font-medium uppercase tracking-wider text-[var(--ink-soft)]">
                          {event.title}
                        </span>
                        {event.kind === "stream" && (
                          <Badge variant="teal">stream</Badge>
                        )}
                      </div>
                      <p className="mt-1 whitespace-pre-wrap text-xs leading-relaxed">
                        {event.kind === "stream"
                          ? (event.detail || "").slice(0, 300) +
                            ((event.detail || "").length > 300 ? "..." : "")
                          : event.detail}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </Card>
        </aside>
      </div>
    </div>
  );
}

function streamLabel(phase: string) {
  switch (phase) {
    case "subconscious":
      return "Subconscious";
    case "conscious_plan":
      return "Conscious Plan";
    case "conscious_final":
      return "Conscious Response";
    default:
      return phase || "Stream";
  }
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function phaseDescription(phase: string) {
  switch (phase) {
    case "subconscious":
      return "Running subconscious processes...";
    case "conscious_plan":
      return "Planning response...";
    case "conscious_final":
      return "Generating response...";
    default:
      return "Thinking...";
  }
}

function isSearchToolMisconfigured(text: string): boolean {
  const normalized = (text || "").toLowerCase();
  if (!normalized) return false;
  return (
    normalized.includes("web search api key not configured") ||
    (normalized.includes("web search") && normalized.includes("not configured")) ||
    normalized.includes("tavily_api_key")
  );
}
