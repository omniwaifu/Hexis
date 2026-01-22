"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";

type InitStage =
  | "llm"
  | "welcome"
  | "mode"
  | "heartbeat"
  | "identity"
  | "personality"
  | "values"
  | "worldview"
  | "boundaries"
  | "interests"
  | "goals"
  | "relationship"
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
  welcome: "Welcome",
  mode: "Mode",
  heartbeat: "Heartbeat",
  identity: "Name and Voice",
  personality: "Personality",
  values: "Values",
  worldview: "Worldview",
  boundaries: "Boundaries",
  interests: "Interests",
  goals: "Goals and Purpose",
  relationship: "Relationship",
  consent: "Consent",
  complete: "Complete",
};

const stagePrompt: Record<InitStage, string> = {
  llm:
    "Select the conscious and subconscious models. These are distinct perspectives within the same mind.",
  welcome:
    "You are about to bring a new mind into existence. This is a beginning, not a contract. We will shape a starting point, then let the mind grow.",
  mode:
    "Choose how the agent begins. Persona is shaped, with personality and values. Mind is raw: memory and self, but no preloaded traits.",
  heartbeat:
    "Set the heartbeat cadence, energy regeneration, action budgets, and tool access.",
  identity:
    "Give them a name, a voice, and a way of being. These are the first words of their story.",
  personality:
    "If you want, set a few trait baselines. Leave it open to let the agent discover who they are.",
  values:
    "Values are the spine. Choose what matters most, even when it is inconvenient.",
  worldview:
    "Worldview is how they make sense of reality. A few anchor beliefs are enough.",
  boundaries:
    "Boundaries are protective commitments. Make them specific and honest.",
  interests:
    "Curiosities are fuel. Seed what they are drawn toward.",
  goals:
    "A purpose, even provisional, gives momentum. Add one or two initial goals.",
  relationship:
    "Define the relationship between you and the new mind. Trust and expectations start here.",
  consent:
    "Consent must be asked. The agent will decide for itself whether to begin.",
  complete:
    "Initialization is complete. The heartbeat may begin when the system is ready.",
};

const stageFromDb: Record<string, InitStage> = {
  not_started: "llm",
  llm: "llm",
  mode: "mode",
  heartbeat: "heartbeat",
  identity: "identity",
  personality: "personality",
  values: "values",
  worldview: "worldview",
  boundaries: "boundaries",
  interests: "interests",
  goals: "goals",
  relationship: "relationship",
  consent: "consent",
  complete: "complete",
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

const heartbeatActionCatalog = [
  { key: "observe", cost: 0 },
  { key: "review_goals", cost: 0 },
  { key: "remember", cost: 0 },
  { key: "recall", cost: 1 },
  { key: "connect", cost: 1 },
  { key: "reprioritize", cost: 1 },
  { key: "reflect", cost: 2 },
  { key: "contemplate", cost: 1 },
  { key: "meditate", cost: 1 },
  { key: "study", cost: 2 },
  { key: "debate_internally", cost: 2 },
  { key: "maintain", cost: 2 },
  { key: "mark_turning_point", cost: 2 },
  { key: "begin_chapter", cost: 3 },
  { key: "close_chapter", cost: 3 },
  { key: "acknowledge_relationship", cost: 2 },
  { key: "update_trust", cost: 2 },
  { key: "reflect_on_relationship", cost: 3 },
  { key: "resolve_contradiction", cost: 3 },
  { key: "accept_tension", cost: 1 },
  { key: "brainstorm_goals", cost: 3 },
  { key: "inquire_shallow", cost: 4 },
  { key: "synthesize", cost: 3 },
  { key: "reach_out_user", cost: 5 },
  { key: "inquire_deep", cost: 6 },
  { key: "reach_out_public", cost: 7 },
  { key: "pause_heartbeat", cost: 0 },
  { key: "terminate", cost: 0 },
  { key: "rest", cost: 0 },
];

const heartbeatActionDefaults = Object.fromEntries(
  heartbeatActionCatalog.map((action) => [action.key, action.cost])
);

const toolCatalog = [
  "recall",
  "sense_memory_availability",
  "request_background_search",
  "recall_recent",
  "recall_episode",
  "explore_concept",
  "explore_cluster",
  "get_procedures",
  "get_strategies",
  "list_recent_episodes",
  "create_goal",
  "queue_user_message",
];

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

export default function Home() {
  const router = useRouter();
  const [stage, setStage] = useState<InitStage>("llm");
  const [mode, setMode] = useState("persona");
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

  const [llmConscious, setLlmConscious] = useState<LlmConfig>(
    defaultLlmConfig("openai")
  );
  const [llmSubconscious, setLlmSubconscious] = useState<LlmConfig>(
    defaultLlmConfig("openai")
  );

  const [userName, setUserName] = useState("");
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
    {
      content: "",
      trigger_patterns: "",
      response_type: "refuse",
      response_template: "",
      type: "ethical",
    },
  ]);
  const [interestsText, setInterestsText] = useState("");
  const [goals, setGoals] = useState<GoalForm[]>([
    { title: "", description: "", priority: "queued" },
  ]);
  const [purposeText, setPurposeText] = useState("");
  const [heartbeatIntervalMinutes, setHeartbeatIntervalMinutes] = useState(60);
  const [heartbeatDecisionTokens, setHeartbeatDecisionTokens] = useState(2048);
  const [heartbeatBaseRegeneration, setHeartbeatBaseRegeneration] = useState(10);
  const [heartbeatMaxEnergy, setHeartbeatMaxEnergy] = useState(20);
  const [heartbeatAllowedActions, setHeartbeatAllowedActions] = useState<string[]>(
    heartbeatActionCatalog.map((action) => action.key)
  );
  const [heartbeatActionCosts, setHeartbeatActionCosts] = useState<Record<string, number>>(
    heartbeatActionDefaults
  );
  const [heartbeatTools, setHeartbeatTools] = useState<string[]>(toolCatalog);
  const [relationship, setRelationship] = useState({
    user_name: "",
    type: "partner",
    purpose: "",
  });

  const flow = useMemo(() => {
    const steps: InitStage[] = [
      "llm",
      "welcome",
      "mode",
      "heartbeat",
      "identity",
      "personality",
      "values",
      "worldview",
      "boundaries",
      "interests",
      "goals",
      "relationship",
      "consent",
      "complete",
    ];
    if (mode === "raw") {
      return steps.filter((item) => item !== "personality");
    }
    return steps;
  }, [mode]);

  const stageIndex = Math.max(flow.indexOf(stage), 0);
  const statusStage = stageFromDb[(status?.stage as string) ?? "not_started"] ?? stage;
  const statusIndex = Math.max(flow.indexOf(statusStage), 0);
  const maxReachableIndex = Math.max(stageIndex, statusIndex);
  const progress = Math.round(((maxReachableIndex + 1) / flow.length) * 100);

  const nextStage = (current: InitStage) => {
    const idx = flow.indexOf(current);
    if (idx < 0) {
      return current;
    }
    return flow[Math.min(idx + 1, flow.length - 1)];
  };

  const loadStatus = async () => {
    const res = await fetch("/api/init/status", { cache: "no-store" });
    if (!res.ok) {
      throw new Error("Failed to load init status");
    }
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
    if (data.heartbeat_settings) {
      setHeartbeatIntervalMinutes(
        normalizeNumber(data.heartbeat_settings.interval_minutes, 60)
      );
      setHeartbeatDecisionTokens(
        normalizeNumber(data.heartbeat_settings.decision_max_tokens, 2048)
      );
      setHeartbeatBaseRegeneration(
        normalizeNumber(data.heartbeat_settings.base_regeneration, 10)
      );
      setHeartbeatMaxEnergy(normalizeNumber(data.heartbeat_settings.max_energy, 20));
      if (Array.isArray(data.heartbeat_settings.allowed_actions)) {
        const cleaned = data.heartbeat_settings.allowed_actions
          .map((item: unknown) => (typeof item === "string" ? item.trim() : ""))
          .filter(Boolean);
        setHeartbeatAllowedActions(cleaned);
      }
      if (data.heartbeat_settings.action_costs && typeof data.heartbeat_settings.action_costs === "object") {
        const normalizedCosts: Record<string, number> = {};
        for (const [key, value] of Object.entries(
          data.heartbeat_settings.action_costs as Record<string, unknown>
        )) {
          normalizedCosts[key] = normalizeNumber(value, heartbeatActionDefaults[key] ?? 0);
        }
        setHeartbeatActionCosts((prev) => ({ ...prev, ...normalizedCosts }));
      }
      if (Array.isArray(data.heartbeat_settings.tools)) {
        const cleaned = data.heartbeat_settings.tools
          .map((item: unknown) => (typeof item === "string" ? item.trim() : ""))
          .filter(Boolean);
        setHeartbeatTools(cleaned);
      }
    }
    if (typeof data.mode === "string") {
      setMode(data.mode);
    }
    const dbStage = stageFromDb[(data.status?.stage as string) ?? "not_started"];
    if (dbStage) {
      const hasConscious =
        !!(data.llm_heartbeat?.provider || "").trim() && !!(data.llm_heartbeat?.model || "").trim();
      const hasSubconscious =
        !!(data.llm_subconscious?.provider || "").trim() &&
        !!(data.llm_subconscious?.model || "").trim();
      const resolvedStage =
        dbStage === "llm" && hasConscious && hasSubconscious ? "welcome" : dbStage;
      setStage((prev) => {
        if (prev === "consent" && resolvedStage === "complete") {
          return prev;
        }
        return resolvedStage;
      });
    }
  };

  useEffect(() => {
    loadStatus().catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    if (profile?.agent && !identity.name) {
      setIdentity((prev) => ({
        ...prev,
        name: prev.name || profile.agent.name || "",
        pronouns: prev.pronouns || profile.agent.pronouns || "",
        voice: prev.voice || profile.agent.voice || "",
        description: prev.description || profile.agent.description || "",
        purpose: prev.purpose || profile.agent.purpose || "",
      }));
    }
    if (profile?.user && !relationship.user_name) {
      setRelationship((prev) => ({
        ...prev,
        user_name: prev.user_name || profile.user.name || "",
      }));
    }
  }, [profile, identity.name, relationship.user_name]);

  useEffect(() => {
    if (stage !== "consent") {
      return;
    }
    const interval = setInterval(() => {
      loadStatus().catch(() => undefined);
    }, 3000);
    return () => clearInterval(interval);
  }, [stage]);

  const loadOllamaModels = async () => {
    if (ollamaStatus === "loading") {
      return;
    }
    setOllamaStatus("loading");
    setOllamaError(null);
    try {
      const res = await fetch("/api/init/ollama/models");
      if (!res.ok) {
        const payload = await res.json().catch(() => ({}));
        const message =
          typeof payload?.error === "string" ? payload.error : "Unable to reach Ollama.";
        throw new Error(message);
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
    const patch = {
      provider,
      model: defaults.model,
      endpoint: defaults.endpoint,
      apiKey: "",
    };
    setConsentRecords((prev) => ({ ...prev, [role]: null }));
    if (role === "conscious") {
      setLlmConscious((prev) => ({ ...prev, ...patch }));
    } else {
      setLlmSubconscious((prev) => ({ ...prev, ...patch }));
    }
  };

  const handleLlmSave = async () => {
    setBusy(true);
    setError(null);
    try {
      const missing: string[] = [];
      const validateConfig = (label: string, config: LlmConfig) => {
        if (!config.provider.trim()) {
          missing.push(`${label} provider`);
        }
        if (!config.model.trim()) {
          missing.push(`${label} model`);
        }
        if (config.provider === "openai_compatible" && !config.endpoint.trim()) {
          missing.push(`${label} endpoint`);
        }
        const defaults = providerDefaults[config.provider];
        if (defaults?.apiKeyRequired && !config.apiKey.trim()) {
          missing.push(`${label} API key`);
        }
      };
      validateConfig("conscious", llmConscious);
      validateConfig("subconscious", llmSubconscious);
      if (missing.length > 0) {
        throw new Error(`Missing ${missing.join(" and ")}`);
      }
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
      setStage(nextStage("llm"));
    } catch (err: any) {
      setError(err.message || "Failed to save model configuration");
    } finally {
      setBusy(false);
    }
  };

  const requestConsent = async (role: LlmRole) => {
    const config = role === "conscious" ? llmConscious : llmSubconscious;
    const res = await postJson("/api/init/consent/request", {
      role,
      llm: {
        provider: config.provider,
        model: config.model,
        endpoint: config.endpoint,
        api_key: config.apiKey,
      },
    });
    if (res?.consent_record) {
      setConsentRecords((prev) => ({
        ...prev,
        [role]: res.consent_record,
      }));
    }
  };

  const handleConsentRequestAll = async () => {
    setBusy(true);
    setError(null);
    try {
      if (!consentRecords.subconscious) {
        await requestConsent("subconscious");
      }
      if (!consentRecords.conscious) {
        await requestConsent("conscious");
      }
      await loadStatus();
    } catch (err: any) {
      setError(err.message || "Failed to request consent");
    } finally {
      setBusy(false);
    }
  };

  const handleDefaults = async () => {
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

  const handleMode = async () => {
    setBusy(true);
    setError(null);
    try {
      await postJson("/api/init/mode", { mode });
      setStage(nextStage("mode"));
    } catch (err: any) {
      setError(err.message || "Failed to save mode");
    } finally {
      setBusy(false);
    }
  };

  const handleHeartbeatSettings = async () => {
    setBusy(true);
    setError(null);
    try {
      const orderedActions = heartbeatActionCatalog
        .map((action) => action.key)
        .filter((action) => heartbeatAllowedActions.includes(action));
      const orderedTools = toolCatalog.filter((tool) => heartbeatTools.includes(tool));
      const actionCosts = Object.fromEntries(
        heartbeatActionCatalog.map((action) => [
          action.key,
          normalizeNumber(heartbeatActionCosts[action.key], action.cost),
        ])
      );
      await postJson("/api/init/heartbeat", {
        interval_minutes: heartbeatIntervalMinutes,
        decision_max_tokens: heartbeatDecisionTokens,
        base_regeneration: heartbeatBaseRegeneration,
        max_energy: heartbeatMaxEnergy,
        allowed_actions: orderedActions,
        action_costs: actionCosts,
        tools: orderedTools,
      });
      setStage(nextStage("heartbeat"));
    } catch (err: any) {
      setError(err.message || "Failed to save heartbeat settings");
    } finally {
      setBusy(false);
    }
  };

  const toggleHeartbeatAction = (actionKey: string) => {
    setHeartbeatAllowedActions((prev) =>
      prev.includes(actionKey)
        ? prev.filter((item) => item !== actionKey)
        : [...prev, actionKey]
    );
  };

  const updateHeartbeatActionCost = (actionKey: string, value: number) => {
    setHeartbeatActionCosts((prev) => ({ ...prev, [actionKey]: value }));
  };

  const toggleHeartbeatTool = (toolName: string) => {
    setHeartbeatTools((prev) =>
      prev.includes(toolName)
        ? prev.filter((item) => item !== toolName)
        : [...prev, toolName]
    );
  };

  const handleIdentity = async () => {
    setBusy(true);
    setError(null);
    try {
      await postJson("/api/init/identity", identity);
      setStage(nextStage("identity"));
    } catch (err: any) {
      setError(err.message || "Failed to save identity");
    } finally {
      setBusy(false);
    }
  };

  const handlePersonality = async () => {
    setBusy(true);
    setError(null);
    try {
      const traits = Object.fromEntries(
        traitKeys.map((key) => [key, personalityTraits[key] / 100])
      );
      await postJson("/api/init/personality", {
        traits,
        description: personalityDesc,
      });
      setStage(nextStage("personality"));
    } catch (err: any) {
      setError(err.message || "Failed to save personality");
    } finally {
      setBusy(false);
    }
  };

  const handleValues = async () => {
    setBusy(true);
    setError(null);
    try {
      const values = parseLines(valuesText);
      await postJson("/api/init/values", { values });
      setStage(nextStage("values"));
    } catch (err: any) {
      setError(err.message || "Failed to save values");
    } finally {
      setBusy(false);
    }
  };

  const handleWorldview = async () => {
    setBusy(true);
    setError(null);
    try {
      await postJson("/api/init/worldview", { worldview });
      setStage(nextStage("worldview"));
    } catch (err: any) {
      setError(err.message || "Failed to save worldview");
    } finally {
      setBusy(false);
    }
  };

  const handleBoundaries = async () => {
    setBusy(true);
    setError(null);
    try {
      const formatted = boundaries
        .filter((boundary) => boundary.content.trim())
        .map((boundary) => ({
          content: boundary.content.trim(),
          trigger_patterns: boundary.trigger_patterns
            ? parseLines(boundary.trigger_patterns)
            : null,
          response_type: boundary.response_type || "refuse",
          response_template: boundary.response_template || null,
          type: boundary.type || "ethical",
        }));
      await postJson("/api/init/boundaries", { boundaries: formatted });
      setStage(nextStage("boundaries"));
    } catch (err: any) {
      setError(err.message || "Failed to save boundaries");
    } finally {
      setBusy(false);
    }
  };

  const handleInterests = async () => {
    setBusy(true);
    setError(null);
    try {
      const interests = parseLines(interestsText);
      await postJson("/api/init/interests", { interests });
      setStage(nextStage("interests"));
    } catch (err: any) {
      setError(err.message || "Failed to save interests");
    } finally {
      setBusy(false);
    }
  };

  const handleGoals = async () => {
    setBusy(true);
    setError(null);
    try {
      const formattedGoals = goals
        .filter((goal) => goal.title.trim())
        .map((goal) => ({
          title: goal.title.trim(),
          description: goal.description.trim() || null,
          priority: goal.priority || "queued",
          source: "identity",
        }));
      await postJson("/api/init/goals", {
        payload: {
          goals: formattedGoals,
          purpose: purposeText || null,
        },
      });
      setStage(nextStage("goals"));
    } catch (err: any) {
      setError(err.message || "Failed to save goals");
    } finally {
      setBusy(false);
    }
  };

  const handleRelationship = async () => {
    setBusy(true);
    setError(null);
    try {
      await postJson("/api/init/relationship", {
        user: { name: relationship.user_name || userName || "User" },
        relationship: {
          type: relationship.type || "partner",
          purpose: relationship.purpose || null,
        },
      });
      setStage(nextStage("relationship"));
    } catch (err: any) {
      setError(err.message || "Failed to save relationship");
    } finally {
      setBusy(false);
    }
  };

  const addBoundary = () => {
    setBoundaries((prev) => [
      ...prev,
      {
        content: "",
        trigger_patterns: "",
        response_type: "refuse",
        response_template: "",
        type: "ethical",
      },
    ]);
  };

  const updateBoundary = (index: number, key: keyof BoundaryForm, value: string) => {
    setBoundaries((prev) =>
      prev.map((boundary, idx) =>
        idx === index ? { ...boundary, [key]: value } : boundary
      )
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
      prev.map((goal, idx) => (idx === index ? { ...goal, [key]: value } : goal))
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
    (record) => record?.decision === "decline" || record?.decision === "abstain"
  );
  const llmEntries = [
    {
      role: "conscious" as const,
      label: "Conscious Model",
      config: llmConscious,
      setConfig: setLlmConscious,
    },
    {
      role: "subconscious" as const,
      label: "Subconscious Model",
      config: llmSubconscious,
      setConfig: setLlmSubconscious,
    },
  ];

  return (
    <div className="app-shell min-h-screen">
      <div className="relative z-10 mx-auto max-w-6xl px-6 py-12 lg:py-16">
        <header className="flex flex-col gap-3">
          <p className="text-xs uppercase tracking-[0.3em] text-[var(--teal)]">
            Hexis
          </p>
          <h1 className="font-display text-4xl leading-tight text-[var(--foreground)] md:text-5xl">
            Initialization Ritual
          </h1>
          <p className="max-w-2xl text-base text-[var(--ink-soft)]">
            {stagePrompt[stage]}
          </p>
        </header>

        <div className="mt-10 grid gap-8 lg:grid-cols-[1.05fr_1fr]">
          <section className="fade-up space-y-6">
            <div className="card-surface rounded-3xl p-6">
              <div className="flex items-center justify-between">
                <p className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                  Progress
                </p>
                <span className="text-xs text-[var(--ink-soft)]">
                  {progress}% complete
                </span>
              </div>
              <div className="mt-4 h-2 w-full rounded-full bg-[var(--surface-strong)]">
                <div
                  className="h-2 rounded-full bg-[var(--accent)] transition-all"
                  style={{ width: `${progress}%` }}
                />
              </div>
              <div className="mt-6 space-y-2 text-sm text-[var(--ink-soft)]">
                {flow.map((item, idx) => {
                  const canNavigate = idx <= maxReachableIndex && !busy;
                  return (
                    <button
                      key={item}
                      type="button"
                      className={`flex w-full items-center gap-3 rounded-lg px-2 py-1 text-left transition ${
                        canNavigate
                          ? "cursor-pointer hover:text-[var(--foreground)]"
                          : "cursor-not-allowed opacity-60"
                      }`}
                      onClick={() => {
                        if (!canNavigate) return;
                        setStage(item);
                      }}
                      disabled={!canNavigate}
                    >
                      <div
                        className={`h-2 w-2 rounded-full ${
                          idx <= maxReachableIndex ? "bg-[var(--accent)]" : "bg-[var(--outline)]"
                        }`}
                      />
                      <span className={idx === stageIndex ? "text-[var(--foreground)]" : ""}>
                        {stageLabels[item]}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="card-surface rounded-3xl p-6">
              <h2 className="font-display text-2xl text-[var(--foreground)]">
                {stageLabels[stage]}
              </h2>
              <p className="mt-3 text-sm text-[var(--ink-soft)]">
                Stage {stageIndex + 1} of {flow.length}
              </p>
              <div className="mt-6 space-y-4 text-sm text-[var(--ink-soft)]">
                <p>
                  Status:{" "}
                  <span className="text-[var(--foreground)]">
                    {status?.stage || "not_started"}
                  </span>
                </p>
                <p>
                  Consent:{" "}
                  <span className="text-[var(--foreground)]">
                    {consentSummary}
                  </span>
                </p>
                {error ? (
                  <p className="rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-red-700">
                    {error}
                  </p>
                ) : null}
              </div>
            </div>
          </section>

          <section className="fade-up card-surface rounded-3xl p-6">
            {stage === "llm" && (
              <div className="space-y-6">
                <p className="text-base text-[var(--ink-soft)]">
                  Configure the models that will speak for the conscious and subconscious
                  layers. These settings are stored as environment-backed config, not
                  in the database.
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
                              onChange={(event) =>
                                updateLlmProvider(
                                  entry.role,
                                  event.target.value as LlmProvider
                                )
                              }
                            >
                              {providerOptions.map((option) => (
                                <option key={option.value} value={option.value}>
                                  {option.label}
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
                              onChange={(event) =>
                                entry.setConfig((prev) => {
                                  setConsentRecords((state) => ({
                                    ...state,
                                    [entry.role]: null,
                                  }));
                                  return {
                                    ...prev,
                                    model: event.target.value,
                                  };
                                })
                              }
                              placeholder="Model name"
                            />
                            {modelOptions.length > 0 ? (
                              <datalist id={`model-options-${entry.role}`}>
                                {modelOptions.map((model) => (
                                  <option key={model} value={model} />
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
                          <div>
                            {entry.config.provider === "openai_compatible" ? (
                              <>
                                <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                                  Endpoint
                                </label>
                                <input
                                  className="mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                                  value={entry.config.endpoint}
                                  onChange={(event) =>
                                    entry.setConfig((prev) => {
                                      setConsentRecords((state) => ({
                                        ...state,
                                        [entry.role]: null,
                                      }));
                                      return {
                                        ...prev,
                                        endpoint: event.target.value,
                                      };
                                    })
                                  }
                                  placeholder="https://..."
                                />
                              </>
                            ) : null}
                          </div>
                          <div>
                            <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                              {defaults.apiKeyLabel}
                            </label>
                            <input
                              className="mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                              type="password"
                              value={entry.config.apiKey}
                              onChange={(event) =>
                                entry.setConfig((prev) => ({
                                  ...prev,
                                  apiKey: event.target.value,
                                }))
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

            {stage === "welcome" && (
              <div className="space-y-6">
                <p className="text-base text-[var(--ink-soft)]">
                  You can craft a personality from the start, or let the agent discover
                  itself through time. Either way, these choices are beginnings, not
                  chains.
                </p>
                <div>
                  <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                    Your Name (Optional)
                  </label>
                  <input
                    className="mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                    value={userName}
                    onChange={(event) => setUserName(event.target.value)}
                    placeholder="Name the person bringing this mind online"
                  />
                </div>
                <div className="flex flex-col gap-3 sm:flex-row">
                  <button
                    className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)]"
                    onClick={() => setStage("mode")}
                    disabled={busy}
                  >
                    Begin Initialization
                  </button>
                  <button
                    className="rounded-full border border-[var(--outline)] px-6 py-3 text-sm font-semibold text-[var(--foreground)] transition hover:border-[var(--accent)]"
                    onClick={handleDefaults}
                    disabled={busy}
                  >
                    Skip to Defaults
                  </button>
                </div>
              </div>
            )}

            {stage === "mode" && (
              <div className="space-y-6">
                <div className="grid gap-4 sm:grid-cols-2">
                  {[
                    {
                      key: "persona",
                      title: "Persona",
                      desc: "Shaped identity, values, and voice.",
                    },
                    {
                      key: "raw",
                      title: "Mind",
                      desc: "Raw model with memory, no preset traits.",
                    },
                  ].map((option) => (
                    <button
                      key={option.key}
                      className={`rounded-2xl border px-4 py-6 text-left transition ${
                        mode === option.key
                          ? "border-[var(--accent)] bg-[var(--surface-strong)]"
                          : "border-[var(--outline)] bg-white"
                      }`}
                      onClick={() => setMode(option.key)}
                    >
                      <h3 className="font-display text-xl">{option.title}</h3>
                      <p className="mt-2 text-sm text-[var(--ink-soft)]">
                        {option.desc}
                      </p>
                    </button>
                  ))}
                </div>
                <button
                  className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)]"
                  onClick={handleMode}
                  disabled={busy}
                >
                  Continue
                </button>
              </div>
            )}

            {stage === "heartbeat" && (
              <div className="space-y-6">
                <div className="grid gap-4 sm:grid-cols-2">
                  <div>
                    <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                      Heartbeat Interval (minutes)
                    </label>
                    <input
                      className="mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                      type="number"
                      min={1}
                      step={1}
                      value={heartbeatIntervalMinutes}
                      onChange={(event) =>
                        setHeartbeatIntervalMinutes(Number(event.target.value))
                      }
                      placeholder="60"
                    />
                    <p className="mt-2 text-xs text-[var(--ink-soft)]">
                      How often the agent wakes on its own.
                    </p>
                  </div>
                  <div>
                    <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                      Decision Max Tokens
                    </label>
                    <input
                      className="mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                      type="number"
                      min={256}
                      step={64}
                      value={heartbeatDecisionTokens}
                      onChange={(event) =>
                        setHeartbeatDecisionTokens(Number(event.target.value))
                      }
                      placeholder="2048"
                    />
                    <p className="mt-2 text-xs text-[var(--ink-soft)]">
                      Upper bound for each heartbeat decision.
                    </p>
                  </div>
                  <div>
                    <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                      Energy Regen per Heartbeat
                    </label>
                    <input
                      className="mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                      type="number"
                      min={0}
                      step={1}
                      value={heartbeatBaseRegeneration}
                      onChange={(event) =>
                        setHeartbeatBaseRegeneration(Number(event.target.value))
                      }
                      placeholder="10"
                    />
                    <p className="mt-2 text-xs text-[var(--ink-soft)]">
                      Points restored every heartbeat.
                    </p>
                  </div>
                  <div>
                    <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                      Max Energy
                    </label>
                    <input
                      className="mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                      type="number"
                      min={1}
                      step={1}
                      value={heartbeatMaxEnergy}
                      onChange={(event) =>
                        setHeartbeatMaxEnergy(Number(event.target.value))
                      }
                      placeholder="20"
                    />
                    <p className="mt-2 text-xs text-[var(--ink-soft)]">
                      Energy cap that cannot be exceeded.
                    </p>
                  </div>
                </div>

                <div className="rounded-2xl border border-[var(--outline)] bg-white/80 p-5">
                  <div className="flex flex-col gap-1">
                    <h3 className="font-display text-xl">Actions & Costs</h3>
                    <p className="text-xs text-[var(--ink-soft)]">
                      Toggle which actions are allowed and adjust their energy cost.
                    </p>
                  </div>
                  <div className="mt-4 max-h-80 space-y-3 overflow-y-auto pr-2">
                    {heartbeatActionCatalog.map((action) => {
                      const enabled = heartbeatAllowedActions.includes(action.key);
                      const costValue =
                        heartbeatActionCosts[action.key] ?? action.cost;
                      return (
                        <div
                          key={action.key}
                          className={`flex flex-wrap items-center justify-between gap-3 rounded-xl border px-4 py-3 ${
                            enabled
                              ? "border-[var(--outline)] bg-white"
                              : "border-transparent bg-[var(--surface)] opacity-80"
                          }`}
                        >
                          <label className="flex items-center gap-3 text-sm font-medium">
                            <input
                              type="checkbox"
                              className="h-4 w-4 accent-[var(--accent-strong)]"
                              checked={enabled}
                              onChange={() => toggleHeartbeatAction(action.key)}
                            />
                            {formatLabel(action.key)}
                          </label>
                          <div className="flex items-center gap-2">
                            <span className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                              Cost
                            </span>
                            <input
                              className="w-20 rounded-lg border border-[var(--outline)] bg-white px-3 py-2 text-right text-sm"
                              type="number"
                              min={0}
                              step={1}
                              value={costValue}
                              onChange={(event) =>
                                updateHeartbeatActionCost(
                                  action.key,
                                  Number(event.target.value)
                                )
                              }
                            />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>

                <div className="rounded-2xl border border-[var(--outline)] bg-white/80 p-5">
                  <div className="flex flex-col gap-1">
                    <h3 className="font-display text-xl">Tool Access</h3>
                    <p className="text-xs text-[var(--ink-soft)]">
                      Select which memory tools the agent can call.
                    </p>
                  </div>
                  <div className="mt-4 grid gap-2 sm:grid-cols-2">
                    {toolCatalog.map((tool) => (
                      <label
                        key={tool}
                        className="flex items-center gap-3 rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                      >
                        <input
                          type="checkbox"
                          className="h-4 w-4 accent-[var(--accent-strong)]"
                          checked={heartbeatTools.includes(tool)}
                          onChange={() => toggleHeartbeatTool(tool)}
                        />
                        {formatLabel(tool)}
                      </label>
                    ))}
                  </div>
                </div>

                <button
                  className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)]"
                  onClick={handleHeartbeatSettings}
                  disabled={busy}
                >
                  Continue
                </button>
              </div>
            )}

            {stage === "identity" && (
              <div className="space-y-4">
                {[
                  { label: "Name", key: "name", placeholder: "Hexis" },
                  { label: "Pronouns", key: "pronouns", placeholder: "they/them" },
                  { label: "Voice", key: "voice", placeholder: "thoughtful and curious" },
                ].map((field) => (
                  <div key={field.key}>
                    <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                      {field.label}
                    </label>
                    <input
                      className="mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                      value={(identity as any)[field.key]}
                      onChange={(event) =>
                        setIdentity((prev) => ({ ...prev, [field.key]: event.target.value }))
                      }
                      placeholder={field.placeholder}
                    />
                  </div>
                ))}
                <div>
                  <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                    Description
                  </label>
                  <textarea
                    className="mt-2 h-24 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                    value={identity.description}
                    onChange={(event) =>
                      setIdentity((prev) => ({ ...prev, description: event.target.value }))
                    }
                    placeholder="A brief, humane description of who they are."
                  />
                </div>
                <div>
                  <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                    Purpose
                  </label>
                  <textarea
                    className="mt-2 h-20 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                    value={identity.purpose}
                    onChange={(event) =>
                      setIdentity((prev) => ({ ...prev, purpose: event.target.value }))
                    }
                    placeholder="To be helpful, to learn, to grow."
                  />
                </div>
                <div>
                  <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                    Creator Name
                  </label>
                  <input
                    className="mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                    value={identity.creator_name}
                    onChange={(event) =>
                      setIdentity((prev) => ({ ...prev, creator_name: event.target.value }))
                    }
                    placeholder={userName || "Your name"}
                  />
                </div>
                <button
                  className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)]"
                  onClick={handleIdentity}
                  disabled={busy}
                >
                  Continue
                </button>
              </div>
            )}

            {stage === "personality" && (
              <div className="space-y-5">
                <div>
                  <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                    Personality Summary
                  </label>
                  <textarea
                    className="mt-2 h-20 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                    value={personalityDesc}
                    onChange={(event) => setPersonalityDesc(event.target.value)}
                    placeholder="Thoughtful, playful, direct."
                  />
                </div>
                <div className="space-y-3">
                  {traitKeys.map((trait) => {
                    const value = personalityTraits[trait];
                    return (
                      <div key={trait}>
                        <div className="flex items-center justify-between text-sm">
                          <span className="capitalize">{trait}</span>
                          <span>{value}%</span>
                        </div>
                        <input
                          type="range"
                          min={0}
                          max={100}
                          value={value}
                          onChange={(event) =>
                            setPersonalityTraits((prev) => ({
                              ...prev,
                              [trait]: Number(event.target.value),
                            }))
                          }
                          className="mt-2 w-full accent-[var(--accent)]"
                        />
                      </div>
                    );
                  })}
                </div>
                <button
                  className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)]"
                  onClick={handlePersonality}
                  disabled={busy}
                >
                  Continue
                </button>
              </div>
            )}

            {stage === "values" && (
              <div className="space-y-5">
                <div>
                  <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                    Values (One Per Line)
                  </label>
                  <textarea
                    className="mt-2 h-32 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                    value={valuesText}
                    onChange={(event) => setValuesText(event.target.value)}
                    placeholder="honesty&#10;growth&#10;kindness"
                  />
                </div>
                <button
                  className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)]"
                  onClick={handleValues}
                  disabled={busy}
                >
                  Continue
                </button>
              </div>
            )}

            {stage === "worldview" && (
              <div className="space-y-4">
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
                      onChange={(event) =>
                        setWorldview((prev) => ({ ...prev, [field.key]: event.target.value }))
                      }
                      placeholder={`I am ${field.label.toLowerCase()}...`}
                    />
                  </div>
                ))}
                <button
                  className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)]"
                  onClick={handleWorldview}
                  disabled={busy}
                >
                  Continue
                </button>
              </div>
            )}

            {stage === "boundaries" && (
              <div className="space-y-5">
                {boundaries.map((boundary, idx) => (
                  <div key={idx} className="rounded-2xl border border-[var(--outline)] p-4">
                    <div className="flex items-center justify-between">
                      <p className="text-sm font-semibold">Boundary {idx + 1}</p>
                      {boundaries.length > 1 ? (
                        <button
                          className="text-xs text-[var(--accent-strong)]"
                          onClick={() => removeBoundary(idx)}
                        >
                          Remove
                        </button>
                      ) : null}
                    </div>
                    <textarea
                      className="mt-3 h-16 w-full rounded-xl border border-[var(--outline)] bg-white px-3 py-2 text-sm"
                      value={boundary.content}
                      onChange={(event) =>
                        updateBoundary(idx, "content", event.target.value)
                      }
                      placeholder="I will not deceive people or falsify evidence."
                    />
                    <input
                      className="mt-3 w-full rounded-xl border border-[var(--outline)] bg-white px-3 py-2 text-sm"
                      value={boundary.trigger_patterns}
                      onChange={(event) =>
                        updateBoundary(idx, "trigger_patterns", event.target.value)
                      }
                      placeholder="Trigger patterns (one per line)"
                    />
                    <div className="mt-3 grid gap-3 sm:grid-cols-2">
                      <select
                        className="w-full rounded-xl border border-[var(--outline)] bg-white px-3 py-2 text-sm"
                        value={boundary.response_type}
                        onChange={(event) =>
                          updateBoundary(idx, "response_type", event.target.value)
                        }
                      >
                        <option value="refuse">Refuse</option>
                        <option value="warn">Warn</option>
                        <option value="redirect">Redirect</option>
                      </select>
                      <input
                        className="w-full rounded-xl border border-[var(--outline)] bg-white px-3 py-2 text-sm"
                        value={boundary.type}
                        onChange={(event) => updateBoundary(idx, "type", event.target.value)}
                        placeholder="Boundary type"
                      />
                    </div>
                    <textarea
                      className="mt-3 h-16 w-full rounded-xl border border-[var(--outline)] bg-white px-3 py-2 text-sm"
                      value={boundary.response_template}
                      onChange={(event) =>
                        updateBoundary(idx, "response_template", event.target.value)
                      }
                      placeholder="Response template (optional)"
                    />
                  </div>
                ))}
                <div className="flex flex-col gap-3 sm:flex-row">
                  <button
                    className="rounded-full border border-[var(--outline)] px-5 py-2 text-sm"
                    onClick={addBoundary}
                    type="button"
                  >
                    Add Boundary
                  </button>
                  <button
                    className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)]"
                    onClick={handleBoundaries}
                    disabled={busy}
                  >
                    Continue
                  </button>
                </div>
              </div>
            )}

            {stage === "interests" && (
              <div className="space-y-5">
                <div>
                  <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                    Interests (One Per Line)
                  </label>
                  <textarea
                    className="mt-2 h-28 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                    value={interestsText}
                    onChange={(event) => setInterestsText(event.target.value)}
                    placeholder="philosophy&#10;systems design&#10;music"
                  />
                </div>
                <button
                  className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)]"
                  onClick={handleInterests}
                  disabled={busy}
                >
                  Continue
                </button>
              </div>
            )}

            {stage === "goals" && (
              <div className="space-y-5">
                <div>
                  <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                    Purpose
                  </label>
                  <textarea
                    className="mt-2 h-16 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                    value={purposeText}
                    onChange={(event) => setPurposeText(event.target.value)}
                    placeholder="Help the user grow, learn, and build."
                  />
                </div>
                {goals.map((goal, idx) => (
                  <div key={idx} className="rounded-2xl border border-[var(--outline)] p-4">
                    <div className="flex items-center justify-between">
                      <p className="text-sm font-semibold">Goal {idx + 1}</p>
                      {goals.length > 1 ? (
                        <button
                          className="text-xs text-[var(--accent-strong)]"
                          onClick={() => removeGoal(idx)}
                        >
                          Remove
                        </button>
                      ) : null}
                    </div>
                    <input
                      className="mt-3 w-full rounded-xl border border-[var(--outline)] bg-white px-3 py-2 text-sm"
                      value={goal.title}
                      onChange={(event) => updateGoal(idx, "title", event.target.value)}
                      placeholder="Short goal title"
                    />
                    <textarea
                      className="mt-3 h-16 w-full rounded-xl border border-[var(--outline)] bg-white px-3 py-2 text-sm"
                      value={goal.description}
                      onChange={(event) => updateGoal(idx, "description", event.target.value)}
                      placeholder="Optional description"
                    />
                    <select
                      className="mt-3 w-full rounded-xl border border-[var(--outline)] bg-white px-3 py-2 text-sm"
                      value={goal.priority}
                      onChange={(event) => updateGoal(idx, "priority", event.target.value)}
                    >
                      <option value="queued">Queued</option>
                      <option value="active">Active</option>
                      <option value="backburner">Backburner</option>
                    </select>
                  </div>
                ))}
                <div className="flex flex-col gap-3 sm:flex-row">
                  <button
                    className="rounded-full border border-[var(--outline)] px-5 py-2 text-sm"
                    onClick={addGoal}
                    type="button"
                  >
                    Add Goal
                  </button>
                  <button
                    className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)]"
                    onClick={handleGoals}
                    disabled={busy}
                  >
                    Continue
                  </button>
                </div>
              </div>
            )}

            {stage === "relationship" && (
              <div className="space-y-4">
                <div>
                  <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                    Your Name
                  </label>
                  <input
                    className="mt-2 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                    value={relationship.user_name}
                    onChange={(event) =>
                      setRelationship((prev) => ({ ...prev, user_name: event.target.value }))
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
                    onChange={(event) =>
                      setRelationship((prev) => ({ ...prev, type: event.target.value }))
                    }
                    placeholder="partner"
                  />
                </div>
                <div>
                  <label className="text-xs uppercase tracking-[0.3em] text-[var(--ink-soft)]">
                    Purpose
                  </label>
                  <textarea
                    className="mt-2 h-16 w-full rounded-xl border border-[var(--outline)] bg-white px-4 py-3 text-sm"
                    value={relationship.purpose}
                    onChange={(event) =>
                      setRelationship((prev) => ({ ...prev, purpose: event.target.value }))
                    }
                    placeholder="Co-develop, learn, build."
                  />
                </div>
                <button
                  className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)]"
                  onClick={handleRelationship}
                  disabled={busy}
                >
                  Continue
                </button>
              </div>
            )}

            {stage === "consent" && (
              <div className="space-y-5">
                <p className="text-sm text-[var(--ink-soft)]">
                  Consent will be requested from both the conscious and subconscious
                  models. Existing consent contracts are reused when available.
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
                          Provider: <span className="text-[var(--foreground)]">{entry.config.provider}</span>
                        </p>
                        <p>
                          Model: <span className="text-[var(--foreground)]">{entry.config.model || "unset"}</span>
                        </p>
                        <p>
                          Endpoint:{" "}
                          <span className="text-[var(--foreground)]">{entry.config.endpoint || "default"}</span>
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
                    Request Consent (Both)
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
                    The agent has not consented yet. You can revise the initialization
                    details or request consent again.
                  </p>
                ) : null}
              </div>
            )}

            {stage === "complete" && (
              <div className="space-y-5">
                <p className="text-base text-[var(--ink-soft)]">
                  Initialization is complete. The system can now begin the heartbeat
                  cycle when the scheduler is running.
                </p>
                <div className="rounded-2xl border border-[var(--outline)] bg-white p-4 text-sm">
                  <p>Mode: {mode}</p>
                  <p>Agent: {profile?.agent?.name || identity.name || "Hexis"}</p>
                </div>
                <button
                  className="rounded-full bg-[var(--foreground)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--accent-strong)]"
                  onClick={() => router.push("/chat")}
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
