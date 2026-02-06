"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";

type InitStage =
  | "llm"
  | "choose_path"
  | "express"
  | "character"
  | "custom"
  | "consent"
  | "complete";

const traitKeys = [
  "openness",
  "conscientiousness",
  "extraversion",
  "agreeableness",
  "neuroticism",
] as const;
type TraitKey = (typeof traitKeys)[number];

const stageLabels: Record<InitStage, string> = {
  llm: "Models",
  choose_path: "Choose Path",
  express: "Express Setup",
  character: "Character Selection",
  custom: "Custom Setup",
  consent: "Consent",
  complete: "Complete",
};

const stagePrompt: Record<InitStage, string> = {
  llm: "Select the conscious and subconscious models. These are distinct perspectives within the same mind.",
  choose_path:
    "Choose how to begin. Express starts with sensible defaults. Character picks a personality preset. Custom gives you full control.",
  express:
    "Express setup applies sensible defaults. Just tell us your name and we handle the rest.",
  character:
    "Pick a personality from the gallery. Each character comes with a complete identity, values, and voice.",
  custom:
    "Full control over identity, personality, values, worldview, goals, and more. Every field has a sensible default.",
  consent:
    "Consent must be asked. The agent will decide for itself whether to begin.",
  complete:
    "Initialization is complete. The heartbeat may begin when the system is ready.",
};

type LlmProvider =
  | "openai"
  | "anthropic"
  | "grok"
  | "gemini"
  | "ollama"
  | "openai_compatible";
type LlmRole = "conscious" | "subconscious";
type LlmConfig = {
  provider: LlmProvider;
  model: string;
  endpoint: string;
  apiKey: string;
};
type ConsentRecord = {
  decision: string;
  signature: string | null;
  provider: string | null;
  model: string | null;
  endpoint: string | null;
  decided_at: string | null;
};
type CharacterEntry = {
  filename: string;
  name: string;
  description: string;
  voice: string;
  values: string[];
  personality: string;
  image: string | null;
};

const providerOptions: { value: LlmProvider; label: string }[] = [
  { value: "openai", label: "OpenAI" },
  { value: "anthropic", label: "Anthropic" },
  { value: "grok", label: "Grok (xAI)" },
  { value: "gemini", label: "Gemini" },
  { value: "ollama", label: "Ollama (local)" },
  {
    value: "openai_compatible",
    label: "Local (OpenAI-compatible: vLLM, llama.cpp, LM Studio)",
  },
];

const providerDefaults: Record<
  LlmProvider,
  { model: string; endpoint: string; apiKeyLabel: string; apiKeyRequired: boolean }
> = {
  openai: {
    model: "gpt-5.2",
    endpoint: "https://api.openai.com/v1",
    apiKeyLabel: "OpenAI API Key",
    apiKeyRequired: true,
  },
  anthropic: {
    model: "claude-opus-4-5",
    endpoint: "",
    apiKeyLabel: "Anthropic API Key",
    apiKeyRequired: true,
  },
  grok: {
    model: "grok-4-1-fast-reasoning",
    endpoint: "",
    apiKeyLabel: "Grok API Key",
    apiKeyRequired: true,
  },
  gemini: {
    model: "gemini-3-pro-preview",
    endpoint: "",
    apiKeyLabel: "Gemini API Key",
    apiKeyRequired: true,
  },
  ollama: {
    model: "",
    endpoint: "http://localhost:11434/v1",
    apiKeyLabel: "API Key (optional)",
    apiKeyRequired: false,
  },
  openai_compatible: {
    model: "",
    endpoint: "http://localhost:8000/v1",
    apiKeyLabel: "API Key (optional)",
    apiKeyRequired: false,
  },
};

const providerModels: Record<LlmProvider, string[]> = {
  openai: [
    "gpt-5.2",
    "gpt-5.2-chat-latest",
    "gpt-5.2-codex",
    "gpt-5.1",
    "gpt-5.1-codex-max",
    "gpt-5-mini",
    "gpt-5-nano",
  ],
  anthropic: [
    "claude-opus-4-5",
    "claude-sonnet-4-5",
    "claude-haiku-4-5",
  ],
  grok: ["grok-4-1-fast-reasoning", "grok-4-1-fast-non-reasoning"],
  gemini: ["gemini-3-pro-preview", "gemini-3-flash-preview"],
  ollama: [],
  openai_compatible: [],
};

const defaultLlmConfig = (provider: LlmProvider): LlmConfig => ({
  provider,
  model: providerDefaults[provider].model,
  endpoint: providerDefaults[provider].endpoint,
  apiKey: "",
});

type BoundaryForm = {
  content: string;
  trigger_patterns: string;
  response_type: string;
  response_template: string;
  type: string;
};

type GoalForm = {
  title: string;
  description: string;
  priority: string;
};

async function postJson<T>(url: string, payload?: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: payload ? JSON.stringify(payload) : "{}",
  });
  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`);
  }
  return res.json();
}

function parseLines(text: string) {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function normalizeNumber(value: unknown, fallback: number) {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return fallback;
}

function formatLabel(value: string) {
  return value
    .replace(/_/g, " ")
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

// Map DB init_stage to our UI stages
function dbStageToUiStage(dbStage: string): InitStage {
  if (dbStage === "complete") return "complete";
  if (dbStage === "consent") return "consent";
  if (dbStage === "not_started" || dbStage === "llm") return "llm";
  // If past llm but not at consent/complete, they're in a tier
  return "choose_path";
}

export default function Home() {
  const router = useRouter();
  const [stage, setStage] = useState<InitStage>("llm");
  const [status, setStatus] = useState<any>({});
  const [profile, setProfile] = useState<any>({});
  const [consentRecords, setConsentRecords] = useState<Record<LlmRole, ConsentRecord | null>>({
    conscious: null,
    subconscious: null,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ollamaModels, setOllamaModels] = useState<string[]>([]);
  const [ollamaStatus, setOllamaStatus] = useState<"idle" | "loading" | "ready" | "error">(
    "idle"
  );
  const [ollamaError, setOllamaError] = useState<string | null>(null);
  const ollamaActiveRef = useRef(false);

  const [llmConscious, setLlmConscious] = useState<LlmConfig>(defaultLlmConfig("openai"));
  const [llmSubconscious, setLlmSubconscious] = useState<LlmConfig>(
    defaultLlmConfig("openai")
  );

  // Shared state
  const [userName, setUserName] = useState("User");

  // Character selection state
  const [characters, setCharacters] = useState<CharacterEntry[]>([]);
  const [selectedCharacter, setSelectedCharacter] = useState<CharacterEntry | null>(null);
  const [characterLoading, setCharacterLoading] = useState(false);

  // Custom tier state
  const [customSection, setCustomSection] = useState<"identity" | "values" | "goals">(
    "identity"
  );
  const [identity, setIdentity] = useState({
    name: "",
    pronouns: "",
    voice: "",
    description: "",
    purpose: "",
    creator_name: "",
  });
  const [personalityDesc, setPersonalityDesc] = useState("");
  const [personalityTraits, setPersonalityTraits] = useState<Record<TraitKey, number>>({
    openness: 50,
    conscientiousness: 50,
    extraversion: 50,
    agreeableness: 50,
    neuroticism: 50,
  });
  const [valuesText, setValuesText] = useState("");
  const [worldview, setWorldview] = useState({
    metaphysics: "",
    human_nature: "",
    epistemology: "",
    ethics: "",
  });
  const [boundaries, setBoundaries] = useState<BoundaryForm[]>([
    { content: "", trigger_patterns: "", response_type: "refuse", response_template: "", type: "ethical" },
  ]);
  const [interestsText, setInterestsText] = useState("");
  const [goals, setGoals] = useState<GoalForm[]>([
    { title: "", description: "", priority: "queued" },
  ]);
  const [purposeText, setPurposeText] = useState("");
  const [relationship, setRelationship] = useState({
    user_name: "",
    type: "partner",
    purpose: "",
  });

  const flow: InitStage[] = [
    "llm",
    "choose_path",
    "consent",
    "complete",
  ];
  const stageIndex = Math.max(flow.indexOf(stage), 0);
  const progress =
    stage === "complete"
      ? 100
      : stage === "consent"
        ? 85
        : stage === "choose_path" || stage === "express" || stage === "character" || stage === "custom"
          ? 40
          : stage === "llm"
            ? 10
            : 50;

  const loadStatus = async () => {
    const res = await fetch("/api/init/status", { cache: "no-store" });
    if (!res.ok) throw new Error("Failed to load init status");
    const data = await res.json();
    setStatus(data.status ?? {});
    setProfile(data.profile ?? {});
    if (data.consent_records) {
      setConsentRecords({
        conscious: data.consent_records.conscious ?? null,
        subconscious: data.consent_records.subconscious ?? null,
      });
    }
    if (data.llm_heartbeat) {
      setLlmConscious((prev) => ({
        ...prev,
        provider: data.llm_heartbeat.provider || prev.provider,
        model: data.llm_heartbeat.model || prev.model,
        endpoint: data.llm_heartbeat.endpoint || prev.endpoint,
      }));
    }
    if (data.llm_subconscious) {
      setLlmSubconscious((prev) => ({
        ...prev,
        provider: data.llm_subconscious.provider || prev.provider,
        model: data.llm_subconscious.model || prev.model,
        endpoint: data.llm_subconscious.endpoint || prev.endpoint,
      }));
    }
    if (typeof data.mode === "string") {
      // no longer tracking mode separately
    }
    const dbStage = (data.status?.stage as string) ?? "not_started";
    const uiStage = dbStageToUiStage(dbStage);
    if (uiStage === "llm") {
      const hasConscious =
        !!(data.llm_heartbeat?.provider || "").trim() && !!(data.llm_heartbeat?.model || "").trim();
      const hasSubconscious =
        !!(data.llm_subconscious?.provider || "").trim() &&
        !!(data.llm_subconscious?.model || "").trim();
      if (hasConscious && hasSubconscious) {
        setStage("choose_path");
      }
    } else {
      setStage((prev) => {
        if (prev === "consent" && uiStage === "complete") return prev;
        if (uiStage === "consent" || uiStage === "complete") return uiStage;
        return prev;
      });
    }
  };

  useEffect(() => {
    loadStatus().catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    if (stage === "consent") {
      const interval = setInterval(() => loadStatus().catch(() => undefined), 3000);
      return () => clearInterval(interval);
    }
  }, [stage]);

  // Load characters when entering character stage
  useEffect(() => {
    if (stage === "character" && characters.length === 0) {
      fetch("/api/init/characters")
        .then((res) => res.json())
        .then((data) => {
          if (Array.isArray(data?.characters)) setCharacters(data.characters);
        })
        .catch(() => undefined);
    }
  }, [stage, characters.length]);

  const loadOllamaModels = async () => {
    if (ollamaStatus === "loading") return;
    setOllamaStatus("loading");
    setOllamaError(null);
    try {
      const res = await fetch("/api/init/ollama/models");
      if (!res.ok) {
        const payload = await res.json().catch(() => ({}));
        throw new Error(typeof payload?.error === "string" ? payload.error : "Unable to reach Ollama.");
      }
      const payload = await res.json();
      const models = Array.isArray(payload?.models)
        ? payload.models.filter((item: unknown) => typeof item === "string")
        : [];
      setOllamaModels(models);
      setOllamaStatus("ready");
    } catch (err: any) {
      setOllamaModels([]);
      setOllamaStatus("error");
      setOllamaError(err?.message || "Unable to reach Ollama.");
    }
  };

  const needsOllama =
    llmConscious.provider === "ollama" || llmSubconscious.provider === "ollama";

  useEffect(() => {
    if (needsOllama && !ollamaActiveRef.current) {
      loadOllamaModels().catch(() => undefined);
    }
    ollamaActiveRef.current = needsOllama;
  }, [needsOllama]);

  const updateLlmProvider = (role: LlmRole, provider: LlmProvider) => {
    const defaults = providerDefaults[provider];
    const patch = { provider, model: defaults.model, endpoint: defaults.endpoint, apiKey: "" };
    setConsentRecords((prev) => ({ ...prev, [role]: null }));
    if (role === "conscious") {
      setLlmConscious((prev) => ({ ...prev, ...patch }));
    } else {
      setLlmSubconscious((prev) => ({ ...prev, ...patch }));
    }
  };

  // --- Handlers ---

  const handleLlmSave = async () => {
    setBusy(true);
    setError(null);
    try {
      const missing: string[] = [];
      const validateConfig = (label: string, config: LlmConfig) => {
        if (!config.provider.trim()) missing.push(`${label} provider`);
        if (!config.model.trim()) missing.push(`${label} model`);
        if (config.provider === "openai_compatible" && !config.endpoint.trim())
          missing.push(`${label} endpoint`);
        const defaults = providerDefaults[config.provider];
        if (defaults?.apiKeyRequired && !config.apiKey.trim()) missing.push(`${label} API key`);
      };
      validateConfig("conscious", llmConscious);
      validateConfig("subconscious", llmSubconscious);
      if (missing.length > 0) throw new Error(`Missing ${missing.join(" and ")}`);
      await postJson("/api/init/llm", {
        conscious: {
          provider: llmConscious.provider,
          model: llmConscious.model,
          endpoint: llmConscious.endpoint,
          api_key: llmConscious.apiKey,
        },
        subconscious: {
          provider: llmSubconscious.provider,
          model: llmSubconscious.model,
          endpoint: llmSubconscious.endpoint,
          api_key: llmSubconscious.apiKey,
        },
      });
      setStage("choose_path");
    } catch (err: any) {
      setError(err.message || "Failed to save model configuration");
    } finally {
      setBusy(false);
    }
  };

  const handleExpress = async () => {
    setBusy(true);
    setError(null);
    try {
      await postJson("/api/init/defaults", { user_name: userName || "User" });
      await loadStatus();
      setStage("consent");
    } catch (err: any) {
      setError(err.message || "Failed to apply defaults");
    } finally {
      setBusy(false);
    }
  };

  const handleCharacterApply = async () => {
    if (!selectedCharacter) return;
    setBusy(true);
    setError(null);
    setCharacterLoading(true);
    try {
      // Load the full card
      const res = await fetch(
        `/api/init/characters?load=${encodeURIComponent(selectedCharacter.filename)}`
      );
      if (!res.ok) throw new Error("Failed to load character");
      const data = await res.json();
      if (!data.card) throw new Error("No card data returned");
      const hexisExt = data.card?.data?.extensions?.hexis ?? {};

      // Apply via init_from_character_card
      await postJson("/api/init/character-card", {
        card: hexisExt,
        user_name: userName || "User",
      });
      await loadStatus();
      setStage("consent");
    } catch (err: any) {
      setError(err.message || "Failed to apply character");
    } finally {
      setBusy(false);
      setCharacterLoading(false);
    }
  };

  const handleCustomSubmit = async () => {
    setBusy(true);
    setError(null);
    try {
      // Mode
      await postJson("/api/init/mode", { mode: "persona" });

      // Identity
      await postJson("/api/init/identity", {
        ...identity,
        creator_name: identity.creator_name || userName || "User",
      });

      // Personality
      const traits = Object.fromEntries(
        traitKeys.map((key) => [key, personalityTraits[key] / 100])
      );
      await postJson("/api/init/personality", { traits, description: personalityDesc });

      // Values
      const values = parseLines(valuesText);
      await postJson("/api/init/values", { values: values.length > 0 ? values : [] });

      // Worldview
      await postJson("/api/init/worldview", { worldview });

      // Boundaries
      const formatted = boundaries
        .filter((b) => b.content.trim())
        .map((b) => ({
          content: b.content.trim(),
          trigger_patterns: b.trigger_patterns ? parseLines(b.trigger_patterns) : null,
          response_type: b.response_type || "refuse",
          response_template: b.response_template || null,
          type: b.type || "ethical",
        }));
      await postJson("/api/init/boundaries", { boundaries: formatted });

      // Interests
      const interests = parseLines(interestsText);
      await postJson("/api/init/interests", { interests });

      // Goals
      const formattedGoals = goals
        .filter((g) => g.title.trim())
        .map((g) => ({
          title: g.title.trim(),
          description: g.description.trim() || null,
          priority: g.priority || "queued",
          source: "identity",
        }));
      await postJson("/api/init/goals", {
        payload: { goals: formattedGoals, purpose: purposeText || null },
      });

      // Relationship
      await postJson("/api/init/relationship", {
        user: { name: relationship.user_name || userName || "User" },
        relationship: { type: relationship.type || "partner", purpose: relationship.purpose || null },
      });

      await loadStatus();
      setStage("consent");
    } catch (err: any) {
      setError(err.message || "Failed to save custom configuration");
    } finally {
      setBusy(false);
    }
  };

  const requestConsent = async (role: LlmRole) => {
    const config = role === "conscious" ? llmConscious : llmSubconscious;
    const res = await postJson<any>("/api/init/consent/request", {
      role,
      llm: {
        provider: config.provider,
        model: config.model,
        endpoint: config.endpoint,
        api_key: config.apiKey,
      },
    });
    if (res?.consent_record) {
      setConsentRecords((prev) => ({ ...prev, [role]: res.consent_record }));
    }
  };

  const handleConsentRequestAll = async () => {
    setBusy(true);
    setError(null);
    try {
      if (!consentRecords.subconscious) await requestConsent("subconscious");
      if (!consentRecords.conscious) await requestConsent("conscious");
      await loadStatus();
    } catch (err: any) {
      setError(err.message || "Failed to request consent");
    } finally {
      setBusy(false);
    }
  };

  const addBoundary = () => {
    setBoundaries((prev) => [
      ...prev,
      { content: "", trigger_patterns: "", response_type: "refuse", response_template: "", type: "ethical" },
    ]);
  };

  const updateBoundary = (index: number, key: keyof BoundaryForm, value: string) => {
    setBoundaries((prev) =>
      prev.map((b, idx) => (idx === index ? { ...b, [key]: value } : b))
    );
  };

  const removeBoundary = (index: number) => {
    setBoundaries((prev) => prev.filter((_, idx) => idx !== index));
  };

  const addGoal = () => {
    setGoals((prev) => [...prev, { title: "", description: "", priority: "queued" }]);
  };

  const updateGoal = (index: number, key: keyof GoalForm, value: string) => {
    setGoals((prev) =>
      prev.map((g, idx) => (idx === index ? { ...g, [key]: value } : g))
    );
  };

  const removeGoal = (index: number) => {
    setGoals((prev) => prev.filter((_, idx) => idx !== index));
  };

  const consentSummary = [
    consentRecords.conscious?.decision || "pending",
    consentRecords.subconscious?.decision || "pending",
  ].join(" / ");
  const consentDeclined = Object.values(consentRecords).some(
    (r) => r?.decision === "decline" || r?.decision === "abstain"
  );
  const statusStage = (status?.stage as string) ?? "not_started";

  const llmEntries = [
    { role: "conscious" as const, label: "Conscious Model", config: llmConscious, setConfig: setLlmConscious },
    { role: "subconscious" as const, label: "Subconscious Model", config: llmSubconscious, setConfig: setLlmSubconscious },
  ];

  return (
    <div className="app-shell min-h-screen">
      <div className="relative z-10 mx-auto max-w-6xl px-6 py-12 lg:py-16">
        <header className="flex flex-col gap-3">
          <p className="text-xs uppercase tracking-[0.3em] text-[var(--teal)]">
            Hexis
          </p>
          <h1 className="font-display text-4xl leading-tight text-[var(--foreground)] md:text-5xl">
            Initialization
          </h1>
          <p className="max-w-2xl text-base text-[var(--ink-soft)]">
            {stagePrompt[stage]}
          </p>
        </header>

        <div className="mt-10 grid gap-8 lg:grid-cols-[280px_1fr]">
          {/* Left sidebar */}
          <section className="fade-up space-y-6">
            <div className="card-surface rounded-3xl p-6">
              <div className="flex items-center justify-between">
                <p className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                  Progress
                </p>
                <span className="text-xs text-[var(--ink-soft)]">{progress}%</span>
              </div>
              <div className="mt-4 h-2 w-full rounded-full bg-[var(--surface-strong)]">
                <div
                  className="h-2 rounded-full bg-[var(--accent)] transition-all"
                  style={{ width: `${progress}%` }}
                />
              </div>
              <div className="mt-6 space-y-2 text-sm text-[var(--ink-soft)]">
                {(["llm", "choose_path", "consent", "complete"] as InitStage[]).map(
                  (item) => {
                    const isCurrent =
                      item === stage ||
                      (item === "choose_path" &&
                        ["express", "character", "custom"].includes(stage));
                    return (
                      <div
                        key={item}
                        className={`flex items-center gap-3 rounded-lg px-2 py-1 ${
                          isCurrent ? "text-[var(--foreground)]" : ""
                        }`}
                      >
                        <div
                          className={`h-2 w-2 rounded-full ${
                            isCurrent ? "bg-[var(--accent)]" : "bg-[var(--outline)]"
                          }`}
                        />
                        <span>{stageLabels[item]}</span>
                      </div>
                    );
                  }
                )}
              </div>
            </div>

            <div className="card-surface rounded-3xl p-6">
              <p className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                Status
              </p>
              <p className="mt-3 text-sm">
                <span className="text-[var(--foreground)]">
                  {statusStage || "not_started"}
                </span>
              </p>
              <p className="mt-2 text-sm">
                Consent:{" "}
                <span className="text-[var(--foreground)]">{consentSummary}</span>
              </p>
              {error ? (
                <p className="mt-3 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                  {error}
                </p>
              ) : null}
            </div>
          </section>

          {/* Main content */}
          <section className="fade-up card-surface rounded-3xl p-6">
            {/* --- LLM Stage --- */}
            {stage === "llm" && (
              <div className="space-y-6">
                <p className="text-base text-[var(--ink-soft)]">
                  Configure the models for the conscious and subconscious layers.
                </p>
                <div className="space-y-6">
                  {llmEntries.map((entry) => {
                    const defaults = providerDefaults[entry.config.provider];
                    const modelOptions =
                      entry.config.provider === "ollama"
                        ? ollamaModels
                        : providerModels[entry.config.provider];
                    return (
                      <fieldset
                        key={entry.role}
                        className="rounded-2xl border border-[var(--outline)] p-4"
                      >
                        <legend className="px-2 text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                          {entry.label}
                        </legend>
                        <div className="mt-3 grid gap-4">
                          <div>
                            <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                              Provider
                            </label>
                            <select
                              className="mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                              value={entry.config.provider}
                              onChange={(e) =>
                                updateLlmProvider(entry.role, e.target.value as LlmProvider)
                              }
                            >
                              {providerOptions.map((opt) => (
                                <option key={opt.value} value={opt.value}>
                                  {opt.label}
                                </option>
                              ))}
                            </select>
                          </div>
                          <div>
                            <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                              Model
                            </label>
                            <input
                              className="mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                              list={`model-options-${entry.role}`}
                              value={entry.config.model}
                              onChange={(e) =>
                                entry.setConfig((prev) => {
                                  setConsentRecords((s) => ({ ...s, [entry.role]: null }));
                                  return { ...prev, model: e.target.value };
                                })
                              }
                              placeholder="Model name"
                            />
                            {modelOptions.length > 0 ? (
                              <datalist id={`model-options-${entry.role}`}>
                                {modelOptions.map((m) => (
                                  <option key={m} value={m} />
                                ))}
                              </datalist>
                            ) : null}
                            {entry.config.provider === "ollama" ? (
                              <p className="mt-2 text-xs text-[var(--ink-soft)]">
                                {ollamaStatus === "loading"
                                  ? "Loading Ollama models..."
                                  : ollamaStatus === "error"
                                    ? ollamaError || "Ollama not reachable."
                                    : ollamaModels.length > 0
                                      ? `${ollamaModels.length} Ollama models detected.`
                                      : "No local Ollama models found."}
                                {ollamaStatus === "error" ? (
                                  <button
                                    type="button"
                                    className="ml-2 text-[var(--accent-strong)] underline"
                                    onClick={() => loadOllamaModels().catch(() => undefined)}
                                  >
                                    Retry
                                  </button>
                                ) : null}
                              </p>
                            ) : null}
                          </div>
                          {entry.config.provider === "openai_compatible" ? (
                            <div>
                              <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                                Endpoint
                              </label>
                              <input
                                className="mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                                value={entry.config.endpoint}
                                onChange={(e) =>
                                  entry.setConfig((prev) => {
                                    setConsentRecords((s) => ({ ...s, [entry.role]: null }));
                                    return { ...prev, endpoint: e.target.value };
                                  })
                                }
                                placeholder="https://..."
                              />
                            </div>
                          ) : null}
                          <div>
                            <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                              {defaults.apiKeyLabel}
                            </label>
                            <input
                              className="mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                              type="password"
                              value={entry.config.apiKey}
                              onChange={(e) =>
                                entry.setConfig((prev) => ({ ...prev, apiKey: e.target.value }))
                              }
                              placeholder={defaults.apiKeyRequired ? "Required" : "Optional"}
                            />
                          </div>
                        </div>
                      </fieldset>
                    );
                  })}
                </div>
                <button
                  className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)]"
                  onClick={handleLlmSave}
                  disabled={busy}
                >
                  Save Models
                </button>
              </div>
            )}

            {/* --- Choose Path Stage --- */}
            {stage === "choose_path" && (
              <div className="space-y-6">
                <div className="grid gap-4 sm:grid-cols-3">
                  {[
                    {
                      key: "express",
                      title: "Express",
                      desc: "Sensible defaults. Just add your name.",
                      icon: "~",
                    },
                    {
                      key: "character",
                      title: "Character",
                      desc: "Pick a personality preset from the gallery.",
                      icon: "~",
                    },
                    {
                      key: "custom",
                      title: "Custom",
                      desc: "Full control over identity, values, and goals.",
                      icon: "~",
                    },
                  ].map((option) => (
                    <button
                      key={option.key}
                      className="rounded-2xl border border-[var(--outline)] bg-white px-5 py-8 text-left transition hover:border-[var(--accent)] hover:bg-[var(--surface-strong)]"
                      onClick={() => setStage(option.key as InitStage)}
                    >
                      <h3 className="font-display text-xl">{option.title}</h3>
                      <p className="mt-2 text-sm text-[var(--ink-soft)]">{option.desc}</p>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* --- Express Stage --- */}
            {stage === "express" && (
              <div className="space-y-6">
                <p className="text-base text-[var(--ink-soft)]">
                  Start with sensible defaults. You can customize later.
                </p>
                <div>
                  <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                    What should Hexis call you?
                  </label>
                  <input
                    className="mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                    value={userName}
                    onChange={(e) => setUserName(e.target.value)}
                    placeholder="User"
                  />
                </div>
                <div className="rounded-2xl border border-[var(--outline)] bg-[var(--surface)] p-4 text-sm text-[var(--ink-soft)]">
                  <p><strong>Name:</strong> Hexis</p>
                  <p><strong>Voice:</strong> Thoughtful and curious</p>
                  <p><strong>Values:</strong> Honesty, growth, kindness, wisdom, humility</p>
                  <p><strong>Mode:</strong> Persona</p>
                </div>
                <div className="flex gap-3">
                  <button
                    className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)]"
                    onClick={handleExpress}
                    disabled={busy}
                  >
                    {busy ? "Setting up..." : "Go"}
                  </button>
                  <button
                    className="rounded-full border border-[var(--outline)] px-6 py-3 text-sm font-semibold text-[var(--foreground)]"
                    onClick={() => setStage("choose_path")}
                    disabled={busy}
                  >
                    Back
                  </button>
                </div>
              </div>
            )}

            {/* --- Character Stage --- */}
            {stage === "character" && (
              <div className="space-y-6">
                <div>
                  <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                    Your name
                  </label>
                  <input
                    className="mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                    value={userName}
                    onChange={(e) => setUserName(e.target.value)}
                    placeholder="User"
                  />
                </div>

                {characters.length === 0 ? (
                  <p className="text-sm text-[var(--ink-soft)]">Loading characters...</p>
                ) : (
                  <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                    {characters.map((ch) => {
                      const isSelected = selectedCharacter?.filename === ch.filename;
                      return (
                        <button
                          key={ch.filename}
                          className={`group rounded-2xl border text-left transition overflow-hidden ${
                            isSelected
                              ? "border-[var(--accent)] bg-[var(--surface-strong)] ring-2 ring-[var(--accent)]/30"
                              : "border-[var(--outline)] bg-white hover:border-[var(--accent)]"
                          }`}
                          onClick={() => setSelectedCharacter(ch)}
                        >
                          {ch.image ? (
                            <div className="relative aspect-square w-full overflow-hidden bg-[var(--surface-strong)]">
                              <img
                                src={`/api/init/characters/image?name=${encodeURIComponent(ch.image)}`}
                                alt={ch.name}
                                className="h-full w-full object-cover transition-transform group-hover:scale-105"
                                loading="lazy"
                              />
                              <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/70 to-transparent px-4 pb-3 pt-8">
                                <h4 className="font-display text-lg text-white">{ch.name}</h4>
                              </div>
                            </div>
                          ) : (
                            <div className="flex aspect-square w-full items-center justify-center bg-[var(--surface-strong)]">
                              <span className="font-display text-3xl text-[var(--ink-soft)]">
                                {ch.name.charAt(0)}
                              </span>
                            </div>
                          )}
                          <div className="px-4 py-3">
                            {!ch.image && <h4 className="font-display text-lg">{ch.name}</h4>}
                            {ch.values.length > 0 && (
                              <p className="text-xs text-[var(--ink-soft)]">
                                {ch.values.slice(0, 3).join(", ")}
                              </p>
                            )}
                            {ch.voice && (
                              <p className="mt-1 text-xs text-[var(--ink-soft)] line-clamp-2">
                                {ch.voice.slice(0, 80)}
                              </p>
                            )}
                          </div>
                        </button>
                      );
                    })}
                  </div>
                )}

                {selectedCharacter && (
                  <div className="flex gap-4 rounded-2xl border border-[var(--accent)] bg-[var(--surface)] p-4 text-sm">
                    {selectedCharacter.image && (
                      <img
                        src={`/api/init/characters/image?name=${encodeURIComponent(selectedCharacter.image)}`}
                        alt={selectedCharacter.name}
                        className="h-20 w-20 flex-shrink-0 rounded-xl object-cover"
                      />
                    )}
                    <div>
                      <p className="font-semibold">{selectedCharacter.name}</p>
                      {selectedCharacter.voice && (
                        <p className="mt-1 text-[var(--ink-soft)]">
                          <strong>Voice:</strong> {selectedCharacter.voice}
                        </p>
                      )}
                      {selectedCharacter.values.length > 0 && (
                        <p className="mt-1 text-[var(--ink-soft)]">
                          <strong>Values:</strong> {selectedCharacter.values.join(", ")}
                        </p>
                      )}
                    </div>
                  </div>
                )}

                <div className="flex gap-3">
                  <button
                    className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)] disabled:opacity-50"
                    onClick={handleCharacterApply}
                    disabled={busy || !selectedCharacter}
                  >
                    {characterLoading ? "Applying..." : "Use This Character"}
                  </button>
                  <button
                    className="rounded-full border border-[var(--outline)] px-6 py-3 text-sm font-semibold text-[var(--foreground)]"
                    onClick={() => setStage("choose_path")}
                    disabled={busy}
                  >
                    Back
                  </button>
                </div>
              </div>
            )}

            {/* --- Custom Stage --- */}
            {stage === "custom" && (
              <div className="space-y-6">
                {/* Section tabs */}
                <div className="flex gap-2 border-b border-[var(--outline)] pb-2">
                  {(
                    [
                      { key: "identity", label: "Identity" },
                      { key: "values", label: "Values & Worldview" },
                      { key: "goals", label: "Goals & Relationship" },
                    ] as const
                  ).map((tab) => (
                    <button
                      key={tab.key}
                      className={`rounded-t-lg px-4 py-2 text-sm font-medium transition ${
                        customSection === tab.key
                          ? "bg-[var(--surface-strong)] text-[var(--foreground)]"
                          : "text-[var(--ink-soft)] hover:text-[var(--foreground)]"
                      }`}
                      onClick={() => setCustomSection(tab.key)}
                    >
                      {tab.label}
                    </button>
                  ))}
                </div>

                {/* Identity section */}
                {customSection === "identity" && (
                  <div className="space-y-4">
                    <div className="grid gap-4 sm:grid-cols-2">
                      {[
                        { label: "Name", key: "name", placeholder: "Hexis" },
                        { label: "Pronouns", key: "pronouns", placeholder: "they/them" },
                        { label: "Voice", key: "voice", placeholder: "thoughtful and curious" },
                        { label: "Creator Name", key: "creator_name", placeholder: userName || "Your name" },
                      ].map((field) => (
                        <div key={field.key}>
                          <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                            {field.label}
                          </label>
                          <input
                            className="mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                            value={(identity as any)[field.key]}
                            onChange={(e) =>
                              setIdentity((prev) => ({ ...prev, [field.key]: e.target.value }))
                            }
                            placeholder={field.placeholder}
                          />
                        </div>
                      ))}
                    </div>
                    <div>
                      <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                        Description
                      </label>
                      <textarea
                        className="mt-2 h-20 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                        value={identity.description}
                        onChange={(e) =>
                          setIdentity((prev) => ({ ...prev, description: e.target.value }))
                        }
                        placeholder="A brief description of who they are."
                      />
                    </div>
                    <div>
                      <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                        Purpose
                      </label>
                      <textarea
                        className="mt-2 h-16 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                        value={identity.purpose}
                        onChange={(e) =>
                          setIdentity((prev) => ({ ...prev, purpose: e.target.value }))
                        }
                        placeholder="To be helpful, to learn, to grow."
                      />
                    </div>
                    <div>
                      <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                        Personality Summary
                      </label>
                      <textarea
                        className="mt-2 h-16 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                        value={personalityDesc}
                        onChange={(e) => setPersonalityDesc(e.target.value)}
                        placeholder="Thoughtful, playful, direct."
                      />
                    </div>
                    <div className="space-y-3">
                      <p className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                        Big Five Traits
                      </p>
                      {traitKeys.map((trait) => (
                        <div key={trait}>
                          <div className="flex items-center justify-between text-sm">
                            <span className="capitalize">{trait}</span>
                            <span>{personalityTraits[trait]}%</span>
                          </div>
                          <input
                            type="range"
                            min={0}
                            max={100}
                            value={personalityTraits[trait]}
                            onChange={(e) =>
                              setPersonalityTraits((prev) => ({
                                ...prev,
                                [trait]: Number(e.target.value),
                              }))
                            }
                            className="mt-1 w-full accent-[var(--accent)]"
                          />
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Values & Worldview section */}
                {customSection === "values" && (
                  <div className="space-y-5">
                    <div>
                      <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                        Values (One Per Line)
                      </label>
                      <textarea
                        className="mt-2 h-28 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                        value={valuesText}
                        onChange={(e) => setValuesText(e.target.value)}
                        placeholder={"honesty\ngrowth\nkindness"}
                      />
                    </div>
                    <div className="grid gap-4 sm:grid-cols-2">
                      {[
                        { key: "metaphysics", label: "Metaphysics" },
                        { key: "human_nature", label: "Human Nature" },
                        { key: "epistemology", label: "Epistemology" },
                        { key: "ethics", label: "Ethics" },
                      ].map((field) => (
                        <div key={field.key}>
                          <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                            {field.label}
                          </label>
                          <textarea
                            className="mt-2 h-16 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                            value={(worldview as any)[field.key]}
                            onChange={(e) =>
                              setWorldview((prev) => ({
                                ...prev,
                                [field.key]: e.target.value,
                              }))
                            }
                            placeholder={`${field.label}...`}
                          />
                        </div>
                      ))}
                    </div>
                    <div>
                      <p className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                        Boundaries
                      </p>
                      <div className="mt-3 space-y-3">
                        {boundaries.map((b, idx) => (
                          <div key={idx} className="flex gap-2">
                            <input
                              className="flex-1 rounded-xl border border-[var(--outline)] bg-white px-3 py-2 text-sm"
                              value={b.content}
                              onChange={(e) => updateBoundary(idx, "content", e.target.value)}
                              placeholder="I will not deceive people."
                            />
                            {boundaries.length > 1 ? (
                              <button
                                className="text-xs text-[var(--accent-strong)]"
                                onClick={() => removeBoundary(idx)}
                              >
                                Remove
                              </button>
                            ) : null}
                          </div>
                        ))}
                        <button
                          className="text-xs text-[var(--accent-strong)]"
                          onClick={addBoundary}
                          type="button"
                        >
                          + Add boundary
                        </button>
                      </div>
                    </div>
                  </div>
                )}

                {/* Goals & Relationship section */}
                {customSection === "goals" && (
                  <div className="space-y-5">
                    <div>
                      <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                        Interests (One Per Line)
                      </label>
                      <textarea
                        className="mt-2 h-20 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                        value={interestsText}
                        onChange={(e) => setInterestsText(e.target.value)}
                        placeholder={"philosophy\nsystems design\nmusic"}
                      />
                    </div>
                    <div>
                      <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                        Purpose
                      </label>
                      <textarea
                        className="mt-2 h-16 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                        value={purposeText}
                        onChange={(e) => setPurposeText(e.target.value)}
                        placeholder="Help the user grow, learn, and build."
                      />
                    </div>
                    <div>
                      <p className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                        Goals
                      </p>
                      <div className="mt-3 space-y-3">
                        {goals.map((g, idx) => (
                          <div key={idx} className="flex gap-2">
                            <input
                              className="flex-1 rounded-xl border border-[var(--outline)] bg-white px-3 py-2 text-sm"
                              value={g.title}
                              onChange={(e) => updateGoal(idx, "title", e.target.value)}
                              placeholder="Short goal title"
                            />
                            {goals.length > 1 ? (
                              <button
                                className="text-xs text-[var(--accent-strong)]"
                                onClick={() => removeGoal(idx)}
                              >
                                Remove
                              </button>
                            ) : null}
                          </div>
                        ))}
                        <button
                          className="text-xs text-[var(--accent-strong)]"
                          onClick={addGoal}
                          type="button"
                        >
                          + Add goal
                        </button>
                      </div>
                    </div>
                    <div className="grid gap-4 sm:grid-cols-3">
                      <div>
                        <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                          Your Name
                        </label>
                        <input
                          className="mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                          value={relationship.user_name || userName}
                          onChange={(e) =>
                            setRelationship((prev) => ({ ...prev, user_name: e.target.value }))
                          }
                          placeholder={userName || "User"}
                        />
                      </div>
                      <div>
                        <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                          Relationship Type
                        </label>
                        <input
                          className="mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                          value={relationship.type}
                          onChange={(e) =>
                            setRelationship((prev) => ({ ...prev, type: e.target.value }))
                          }
                          placeholder="partner"
                        />
                      </div>
                      <div>
                        <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                          Purpose
                        </label>
                        <input
                          className="mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                          value={relationship.purpose}
                          onChange={(e) =>
                            setRelationship((prev) => ({ ...prev, purpose: e.target.value }))
                          }
                          placeholder="Co-develop, learn, build."
                        />
                      </div>
                    </div>
                  </div>
                )}

                {/* Custom submit/back buttons */}
                <div className="flex gap-3">
                  <button
                    className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)]"
                    onClick={handleCustomSubmit}
                    disabled={busy}
                  >
                    {busy ? "Saving..." : "Save & Continue to Consent"}
                  </button>
                  <button
                    className="rounded-full border border-[var(--outline)] px-6 py-3 text-sm font-semibold text-[var(--foreground)]"
                    onClick={() => setStage("choose_path")}
                    disabled={busy}
                  >
                    Back
                  </button>
                </div>
              </div>
            )}

            {/* --- Consent Stage --- */}
            {stage === "consent" && (
              <div className="space-y-5">
                <p className="text-sm text-[var(--ink-soft)]">
                  Consent will be requested from both models. Existing contracts are reused
                  when available.
                </p>
                <div className="grid gap-4">
                  {[
                    { key: "conscious", label: "Conscious Model", config: llmConscious },
                    { key: "subconscious", label: "Subconscious Model", config: llmSubconscious },
                  ].map((entry) => {
                    const record = consentRecords[entry.key as LlmRole];
                    return (
                      <div
                        key={entry.key}
                        className="rounded-2xl border border-[var(--outline)] bg-white p-4 text-sm"
                      >
                        <p className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                          {entry.label}
                        </p>
                        <p className="mt-3">
                          Provider:{" "}
                          <span className="text-[var(--foreground)]">{entry.config.provider}</span>
                        </p>
                        <p>
                          Model:{" "}
                          <span className="text-[var(--foreground)]">
                            {entry.config.model || "unset"}
                          </span>
                        </p>
                        <p className="mt-3">
                          Decision:{" "}
                          <span className="text-[var(--foreground)]">
                            {record?.decision || "pending"}
                          </span>
                        </p>
                        {record?.signature ? (
                          <p className="mt-2">
                            Signature: <span className="font-mono">{record.signature}</span>
                          </p>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
                <div className="flex flex-col gap-3 sm:flex-row">
                  <button
                    className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)]"
                    onClick={handleConsentRequestAll}
                    disabled={busy}
                  >
                    {busy ? "Requesting..." : "Request Consent"}
                  </button>
                  <button
                    className="rounded-full border border-[var(--outline)] px-6 py-3 text-sm font-semibold text-[var(--foreground)]"
                    onClick={() => loadStatus().catch(() => undefined)}
                    disabled={busy}
                  >
                    Refresh
                  </button>
                  {statusStage === "complete" ? (
                    <button
                      className="rounded-full border border-[var(--outline)] px-6 py-3 text-sm font-semibold text-[var(--foreground)]"
                      onClick={() => setStage("complete")}
                      disabled={busy}
                    >
                      Continue
                    </button>
                  ) : null}
                </div>
                {consentDeclined ? (
                  <p className="rounded-2xl border border-[var(--outline)] bg-white p-4 text-sm text-[var(--ink-soft)]">
                    The agent has not consented yet. You can revise the initialization or
                    request consent again.
                  </p>
                ) : null}
              </div>
            )}

            {/* --- Complete Stage --- */}
            {stage === "complete" && (
              <div className="space-y-5">
                <p className="text-base text-[var(--ink-soft)]">
                  Initialization is complete. The heartbeat cycle may begin when the system
                  is running.
                </p>
                <div className="rounded-2xl border border-[var(--outline)] bg-white p-4 text-sm">
                  <p>Agent: {profile?.agent?.name || identity.name || "Hexis"}</p>
                </div>
                <button
                  className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)]"
                  onClick={() => router.push("/")}
                >
                  Enter Hexis
                </button>
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
