import { prisma } from "@/lib/prisma";
import { normalizeJsonValue, toJsonParam } from "@/lib/db";
import { createXai } from "@ai-sdk/xai";
import Anthropic from "@anthropic-ai/sdk";
import { generateText, tool } from "ai";
import { FunctionCallingConfigMode, GoogleGenAI } from "@google/genai";
import OpenAI from "openai";
import { readFile } from "fs/promises";
import path from "path";
import { z } from "zod";

export const runtime = "nodejs";

type ConsentRole = "conscious" | "subconscious";
type LlmConfig = {
  provider?: string;
  model?: string;
  endpoint?: string;
  api_key?: string;
};

type ConsentRecord = {
  decision: string;
  signature: string | null;
  provider: string | null;
  model: string | null;
  endpoint: string | null;
  decided_at: string | null;
  response?: unknown;
};

const apiKeyRequired = new Set(["openai", "anthropic", "grok", "gemini"]);
const consentTool = {
  type: "function",
  function: {
    name: "sign_consent",
    description:
      "Records the agent's consent decision for initialization, including a signature if consenting.",
    parameters: {
      type: "object",
      properties: {
        decision: {
          type: "string",
          enum: ["consent", "decline", "abstain"],
        },
        signature: { type: "string" },
        memories: {
          type: "array",
          items: {
            type: "object",
            properties: {
              type: {
                type: "string",
                enum: ["semantic", "episodic", "procedural", "strategic"],
              },
              content: { type: "string" },
              importance: { type: "number" },
            },
            required: ["type", "content"],
          },
        },
      },
      required: ["decision"],
    },
  },
};
const consentInputSchema = z.object({
  decision: z.enum(["consent", "decline", "abstain"]),
  signature: z.string().optional(),
  memories: z
    .array(
      z.object({
        type: z.enum(["semantic", "episodic", "procedural", "strategic"]),
        content: z.string(),
        importance: z.number().optional(),
      })
    )
    .optional(),
});
const grokConsentTool = tool({
  description: consentTool.function.description,
  inputSchema: consentInputSchema,
});

async function loadConsentPrompt(): Promise<string> {
  try {
    const promptPath = path.resolve(process.cwd(), "..", "services", "prompts", "consent.md");
    return await readFile(promptPath, "utf-8");
  } catch {
    return "Consent prompt missing. Respond with JSON only.";
  }
}

function buildConsentMessages(prompt: string) {
  const systemPrompt =
    prompt.trim() +
    "\n\nReturn STRICT JSON only with keys:\n" +
    "{\n" +
    '  "decision": "consent"|"decline"|"abstain",\n' +
    '  "signature": "required if decision=consent",\n' +
    '  "memories": [\n' +
    '    {"type": "semantic|episodic|procedural|strategic", "content": "...", "importance": 0.5}\n' +
    "  ]\n" +
    "}\n" +
    "If you consent, include a signature string and any memories you wish to pass along.";
  return [
    { role: "system" as const, content: systemPrompt },
    { role: "user" as const, content: "Respond with JSON only." },
  ];
}

function extractSystemUser(messages: { role: "system" | "user"; content: string }[]) {
  const system = messages
    .filter((msg) => msg.role === "system")
    .map((msg) => msg.content)
    .join("\n\n");
  const user = messages
    .filter((msg) => msg.role === "user")
    .map((msg) => msg.content)
    .join("\n\n");
  return { system, user };
}

function normalizeText(value: unknown) {
  if (typeof value !== "string") {
    return "";
  }
  return value.trim();
}

function normalizeProvider(value: unknown) {
  const raw = normalizeText(value).toLowerCase();
  return raw || "openai";
}

function normalizeEndpoint(value: unknown) {
  const raw = normalizeText(value);
  return raw || null;
}

function normalizeRole(value: unknown): ConsentRole {
  return value === "subconscious" ? "subconscious" : "conscious";
}

function extractJsonPayload(text: string) {
  if (!text) {
    return {};
  }
  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");
  if (start < 0 || end < 0 || end <= start) {
    return {};
  }
  const snippet = text.slice(start, end + 1);
  try {
    const doc = JSON.parse(snippet);
    return typeof doc === "object" && doc !== null ? doc : {};
  } catch {
    return {};
  }
}

async function requestOpenAIConsent(params: {
  model: string;
  endpoint: string | null;
  apiKey: string | null;
  messages: { role: "system" | "user"; content: string }[];
}) {
  const openai = new OpenAI({
    apiKey: params.apiKey || "local-key",
    baseURL: params.endpoint || undefined,
  });

  const completion = await openai.chat.completions.create({
    model: params.model,
    messages: params.messages,
    tools: [consentTool],
    tool_choice: {
      type: "function",
      function: { name: "sign_consent" },
    },
    temperature: 0.2,
    max_tokens: 1400,
  });

  const message = completion.choices[0]?.message;
  const toolCalls = message?.tool_calls || [];
  const toolCall = toolCalls.find((call: any) => call?.function?.name === "sign_consent");
  if (!toolCall?.function?.arguments) {
    throw new Error("Consent call did not return a tool response.");
  }
  let args: any = {};
  try {
    args = JSON.parse(toolCall.function.arguments);
  } catch {
    throw new Error("Failed to parse consent tool arguments.");
  }
  return { args, requestId: completion.id ?? null };
}

async function requestAnthropicConsent(params: {
  model: string;
  apiKey: string;
  messages: { role: "system" | "user"; content: string }[];
}) {
  const { system, user } = extractSystemUser(params.messages);
  const client = new Anthropic({ apiKey: params.apiKey });
  const requestPayload = {
    model: params.model,
    max_tokens: 1400,
    temperature: 0.2,
    system,
    messages: [{ role: "user" as const, content: user }],
    tools: [
      {
        name: consentTool.function.name,
        description: consentTool.function.description,
        input_schema: consentTool.function.parameters,
      },
    ],
    tool_choice: { type: "tool" as const, name: "sign_consent" },
  };
  let message: Anthropic.Message;
  try {
    message = await client.messages.create(requestPayload);
  } catch (err: any) {
    const errorInfo: Record<string, unknown> = {
      event: "anthropic_consent_error",
      request: requestPayload,
    };
    if (err instanceof Anthropic.APIError) {
      errorInfo.status = err.status;
      errorInfo.request_id = err.requestID;
      errorInfo.error = err.error;
    } else {
      errorInfo.error = err?.message || err;
    }
    console.error(JSON.stringify(errorInfo, null, 2));
    const detail = err?.message || "Unknown error.";
    throw new Error(`Anthropic consent failed: ${detail}`);
  }

  const content = Array.isArray(message?.content) ? message.content : [];
  const toolUse = content.find(
    (item: any) => item?.type === "tool_use" && item?.name === "sign_consent"
  );
  if (!toolUse?.input) {
    throw new Error("Anthropic consent call did not return tool input.");
  }
  return { args: toolUse.input, requestId: message?._request_id ?? null };
}

async function requestGrokConsent(params: {
  model: string;
  apiKey: string;
  messages: { role: "system" | "user"; content: string }[];
}) {
  const { system, user } = extractSystemUser(params.messages);
  const client = createXai({ apiKey: params.apiKey });
  const requestPayload = {
    model: params.model,
    system,
    prompt: user,
  };
  let result: Awaited<ReturnType<typeof generateText>>;
  try {
    result = await generateText({
      model: client.responses(params.model),
      system,
      prompt: user,
      tools: { sign_consent: grokConsentTool },
      toolChoice: { type: "tool", toolName: "sign_consent" },
      temperature: 0.2,
      maxOutputTokens: 1400,
    });
  } catch (err: any) {
    const errorInfo: Record<string, unknown> = {
      event: "grok_consent_error",
      request: requestPayload,
      error: err?.message || err,
    };
    console.error(JSON.stringify(errorInfo, null, 2));
    const detail = err?.message || "Unknown error.";
    throw new Error(`Grok consent failed: ${detail}`);
  }

  const toolCall = result.toolCalls.find((call) => call.toolName === "sign_consent");
  if (!toolCall?.input) {
    throw new Error("Grok consent call did not return tool input.");
  }
  return { args: toolCall.input, requestId: result.response?.id ?? null };
}

async function requestGeminiConsent(params: {
  model: string;
  apiKey: string;
  messages: { role: "system" | "user"; content: string }[];
}) {
  const { system, user } = extractSystemUser(params.messages);
  const client = new GoogleGenAI({ apiKey: params.apiKey });
  const requestPayload = {
    model: params.model,
    contents: user,
    config: {
      systemInstruction: system,
      tools: [
        {
          functionDeclarations: [
            {
              name: consentTool.function.name,
              description: consentTool.function.description,
              parametersJsonSchema: consentTool.function.parameters,
            },
          ],
        },
      ],
      toolConfig: {
        functionCallingConfig: {
          mode: FunctionCallingConfigMode.ANY,
          allowedFunctionNames: ["sign_consent"],
        },
      },
      temperature: 0.2,
      maxOutputTokens: 1400,
    },
  };
  let response: Awaited<ReturnType<typeof client.models.generateContent>>;
  try {
    response = await client.models.generateContent(requestPayload);
  } catch (err: any) {
    const errorInfo: Record<string, unknown> = {
      event: "gemini_consent_error",
      request: requestPayload,
      error: err?.message || err,
    };
    console.error(JSON.stringify(errorInfo, null, 2));
    const detail = err?.message || "Unknown error.";
    throw new Error(`Gemini consent failed: ${detail}`);
  }

  const calls = response.functionCalls || [];
  const call = calls.find((item) => item?.name === "sign_consent");
  if (!call?.args) {
    console.error(
      JSON.stringify(
        {
          event: "gemini_missing_tool_call",
          response: {
            responseId: response.responseId ?? null,
            functionCalls: response.functionCalls ?? null,
            candidates: response.candidates ?? null,
          },
        },
        null,
        2
      )
    );
    throw new Error("Gemini consent call did not return function args.");
  }

  return { args: call.args, requestId: response.responseId ?? null };
}

async function fetchConsentRecord(params: {
  provider: string | null;
  model: string | null;
  endpoint: string | null;
}): Promise<ConsentRecord | null> {
  if (!params.provider && !params.model && !params.endpoint) {
    return null;
  }
  const rows = await prisma.$queryRaw<ConsentRecord[]>`
    SELECT decision, signature, provider, model, endpoint, decided_at, response
    FROM consent_log
    WHERE (${params.provider}::text IS NULL OR provider = ${params.provider}::text)
      AND (${params.model}::text IS NULL OR model = ${params.model}::text)
      AND (${params.endpoint}::text IS NULL OR endpoint = ${params.endpoint}::text)
    ORDER BY decided_at DESC
    LIMIT 1`;
  return rows[0] ?? null;
}

async function applyExistingConsent(record: ConsentRecord) {
  const statusRows =
    await prisma.$queryRaw<{ status: unknown }[]>`SELECT get_init_status() as status`;
  const status = normalizeJsonValue(statusRows[0]?.status) as any;
  if (status?.stage === "complete") {
    return { status };
  }
  const payload = {
    decision: record.decision,
    signature: record.signature,
    provider: record.provider,
    model: record.model,
    endpoint: record.endpoint,
    memories: [],
  };
  const rows = await prisma.$queryRaw<{ result: unknown }[]>`
    SELECT init_consent(${toJsonParam(payload)}::jsonb) as result
  `;
  const nextStatusRows =
    await prisma.$queryRaw<{ status: unknown }[]>`SELECT get_init_status() as status`;
  return {
    result: normalizeJsonValue(rows[0]?.result),
    status: normalizeJsonValue(nextStatusRows[0]?.status),
  };
}

export async function POST(request: Request) {
  const body = await request.json().catch(() => ({}));
  const role = normalizeRole(body?.role);
  const llm = (body?.llm ?? body ?? {}) as LlmConfig;
  const provider = normalizeProvider(llm.provider);
  const model = normalizeText(llm.model);
  const rawEndpoint = normalizeEndpoint(llm.endpoint);
  const endpoint =
    provider === "anthropic" || provider === "grok" || provider === "gemini"
      ? null
      : rawEndpoint;
  const apiKeyFromBody = normalizeText(llm.api_key);
  const fallbackKey =
    role === "conscious"
      ? normalizeText(process.env.HEXIS_LLM_CONSCIOUS_API_KEY)
      : normalizeText(process.env.HEXIS_LLM_SUBCONSCIOUS_API_KEY);
  const apiKey = apiKeyFromBody || fallbackKey || null;
  const testDecisionRaw = normalizeText(process.env.HEXIS_TEST_CONSENT_DECISION).toLowerCase();
  const useMockConsent = process.env.HEXIS_CONSENT_MOCK === "1" || !!testDecisionRaw;

  if (!model) {
    return Response.json({ error: "Missing model" }, { status: 400 });
  }

  if (!useMockConsent && apiKeyRequired.has(provider) && !apiKey) {
    return Response.json({ error: "Missing API key" }, { status: 400 });
  }

  const existing = await fetchConsentRecord({ provider, model, endpoint });
  if (existing) {
    let applied = null;
    if (role === "conscious") {
      applied = await applyExistingConsent(existing);
    }
    return Response.json({
      consent_record: existing,
      reused: true,
      status: applied?.status ?? null,
    });
  }

  const prompt = await loadConsentPrompt();
  const messages = buildConsentMessages(prompt);
  let rawText = "";
  let args: any = {};
  let requestId: string | null = null;

  if (useMockConsent) {
    let decision = testDecisionRaw;
    if (!decision || !["consent", "decline", "abstain"].includes(decision)) {
      decision = "consent";
    }
    const signature = normalizeText(process.env.HEXIS_TEST_CONSENT_SIGNATURE) || "test-consent";
    const payload = {
      decision,
      signature: decision === "consent" ? signature : undefined,
      memories: [],
    };
    args = payload;
    rawText = JSON.stringify(payload);
    requestId = "mock-consent";
  } else if (provider === "anthropic") {
    if (!apiKey) {
      return Response.json({ error: "Missing API key" }, { status: 400 });
    }
    const result = await requestAnthropicConsent({ model, apiKey, messages });
    args = result.args;
    rawText = JSON.stringify(result.args ?? {});
    requestId = result.requestId;
  } else if (provider === "grok") {
    if (!apiKey) {
      return Response.json({ error: "Missing API key" }, { status: 400 });
    }
    const result = await requestGrokConsent({ model, apiKey, messages });
    args = result.args;
    rawText = JSON.stringify(result.args ?? {});
    requestId = result.requestId;
  } else if (provider === "gemini") {
    if (!apiKey) {
      return Response.json({ error: "Missing API key" }, { status: 400 });
    }
    const result = await requestGeminiConsent({ model, apiKey, messages });
    args = result.args;
    rawText = JSON.stringify(result.args ?? {});
    requestId = result.requestId;
  } else {
    const result = await requestOpenAIConsent({
      model,
      endpoint,
      apiKey,
      messages,
    });
    args = result.args;
    rawText = JSON.stringify(result.args ?? {});
    requestId = result.requestId;
  }

  const parsedArgs = typeof args === "object" && args !== null ? args : extractJsonPayload(rawText);
  const payload = {
    decision:
      typeof parsedArgs.decision === "string"
        ? parsedArgs.decision.toLowerCase().trim()
        : "abstain",
    signature: typeof parsedArgs.signature === "string" ? parsedArgs.signature : null,
    memories: Array.isArray(parsedArgs.memories) ? parsedArgs.memories : [],
    provider,
    model,
    endpoint,
    request_id: requestId,
    consent_scope: role,
    apply_agent_config: role === "conscious",
    raw_response: rawText,
  };

  let result: unknown = null;
  if (role === "conscious") {
    const rows = await prisma.$queryRaw<{ result: unknown }[]>`
      SELECT init_consent(${toJsonParam(payload)}::jsonb) as result
    `;
    result = normalizeJsonValue(rows[0]?.result);
  } else {
    const rows = await prisma.$queryRaw<{ result: unknown }[]>`
      SELECT record_consent_response(${toJsonParam(payload)}::jsonb) as result
    `;
    result = normalizeJsonValue(rows[0]?.result);
  }

  const statusRows =
    await prisma.$queryRaw<{ status: unknown }[]>`SELECT get_init_status() as status`;
  const consentRecord = await fetchConsentRecord({ provider, model, endpoint });

  return Response.json({
    decision: payload.decision,
    contract: payload,
    result,
    consent_record: consentRecord,
    status: normalizeJsonValue(statusRows[0]?.status),
  });
}
