"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";

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
};

const promptAddendaOptions = [
  { id: "philosophy", label: "Philosophy Grounding" },
  { id: "letter", label: "Letter From Claude" },
];

export default function ChatPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [events, setEvents] = useState<LogEvent[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [ready, setReady] = useState<boolean | null>(null);
  const [promptAddenda, setPromptAddenda] = useState<string[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const logRef = useRef<HTMLDivElement>(null);

  const historyPayload = useMemo(
    () =>
      messages
        .filter((msg) => msg.content.trim())
        .map((msg) => ({ role: msg.role, content: msg.content })),
    [messages]
  );

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
      prev.map((msg) => (msg.id === assistantId ? { ...msg, content: msg.content + text } : msg))
    );
  };

  const handleSend = async () => {
    if (!input.trim() || sending) {
      return;
    }
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
    setSending(true);

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
          let payload: any = {};
          try {
            payload = JSON.parse(data);
          } catch {
            payload = { raw: data };
          }

          if (eventType === "token") {
            const phase = payload.phase || "";
            const text = payload.text || "";
            appendStreamToken(phase, text);
            if (phase === "conscious_final" && text) {
              updateAssistantMessage(assistantMessage.id, text);
            }
          }

          if (eventType === "phase_start") {
            appendLog({
              id: crypto.randomUUID(),
              kind: "log",
              title: streamLabel(payload.phase || "phase"),
              detail: "started",
            });
          }

          if (eventType === "log") {
            appendLog({
              id: payload.id || crypto.randomUUID(),
              kind: "log",
              title: payload.title || payload.kind || "log",
              detail: payload.detail || "",
            });
          }

          if (eventType === "error") {
            appendLog({
              id: crypto.randomUUID(),
              kind: "error",
              title: "Error",
              detail: payload.message || "Unknown error",
            });
          }
        }
      }
    } catch (err: any) {
      appendLog({
        id: crypto.randomUUID(),
        kind: "error",
        title: "Chat error",
        detail: err?.message || "Unknown error",
      });
    } finally {
      setSending(false);
    }
  };

  if (ready === false) {
    return (
      <div className="app-shell min-h-screen">
        <div className="relative z-10 mx-auto flex min-h-screen max-w-3xl items-center justify-center px-6">
          <div className="card-surface w-full rounded-3xl p-10 text-center">
            <h1 className="font-display text-3xl">Initialization required</h1>
            <p className="mt-3 text-sm text-[var(--ink-soft)]">
              Complete the initialization ritual before entering the main chat.
            </p>
            <Link
              className="mt-6 inline-flex rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white"
              href="/"
            >
              Return to Initialization
            </Link>
          </div>
        </div>
      </div>
    );
  }
  if (ready === null) {
    return (
      <div className="app-shell min-h-screen">
        <div className="relative z-10 mx-auto flex min-h-screen max-w-3xl items-center justify-center px-6">
          <div className="card-surface w-full rounded-3xl p-10 text-center">
            <p className="text-sm text-[var(--ink-soft)]">Loading status…</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="app-shell min-h-screen">
      <div className="relative z-10 mx-auto flex min-h-screen max-w-6xl flex-col gap-6 px-6 py-10 lg:flex-row">
        <section className="flex flex-1 flex-col gap-6">
          <header className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <p className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                Hexis Main Screen
              </p>
              <h1 className="font-display text-3xl">Conversation</h1>
            </div>
            <div className="flex items-center gap-2 text-xs text-[var(--ink-soft)]">
              {sending ? "Streaming" : "Idle"}
            </div>
          </header>

          <div className="card-surface flex h-[70vh] flex-col overflow-hidden rounded-3xl">
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
                  <p className="whitespace-pre-wrap">{msg.content || "..."}</p>
                </div>
              ))}
            </div>
            <div className="border-t border-[var(--outline)] p-4">
              <div className="flex flex-col gap-3 sm:flex-row">
                <textarea
                  className="min-h-[80px] flex-1 rounded-2xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                  placeholder="Talk with Hexis."
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                />
                <button
                  className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)]"
                  onClick={handleSend}
                  disabled={sending}
                >
                  Send
                </button>
              </div>
            </div>
          </div>
        </section>

        <aside className="flex w-full flex-col gap-4 lg:w-80">
          <div className="card-surface rounded-3xl p-5">
            <h2 className="font-display text-xl">Prompt Addenda</h2>
            <p className="mt-2 text-xs text-[var(--ink-soft)]">
              Add optional modules to the conscious system prompt.
            </p>
            <div className="mt-4 space-y-2">
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
          </div>

          <div className="card-surface flex h-[60vh] flex-col overflow-hidden rounded-3xl">
            <div className="border-b border-[var(--outline)] p-4">
              <h2 className="font-display text-xl">LLM Activity</h2>
              <p className="text-xs text-[var(--ink-soft)]">Streaming tokens, tool calls, and memory IO.</p>
            </div>
            <div className="flex-1 overflow-y-auto p-4" ref={logRef}>
              {events.length === 0 ? (
                <p className="text-xs text-[var(--ink-soft)]">No activity yet.</p>
              ) : (
                <div className="space-y-3">
                  {events.map((event) => (
                    <div
                      key={event.id}
                      className={`rounded-2xl border px-3 py-2 text-xs ${
                        event.kind === "error"
                          ? "border-red-200 bg-red-50 text-red-700"
                          : "border-[var(--outline)] bg-white"
                      }`}
                    >
                      <p className="text-[10px] uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                        {event.title}
                      </p>
                      <p className="mt-1 whitespace-pre-wrap text-[13px]">
                        {event.detail}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
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
