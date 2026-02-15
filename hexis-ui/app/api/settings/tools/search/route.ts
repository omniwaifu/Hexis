import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { normalizeJsonValue } from "@/lib/db";

type ToolsConfigDoc = {
  enabled?: string[] | null;
  disabled?: string[];
  api_keys?: Record<string, string>;
  [key: string]: unknown;
};

function toStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => (typeof item === "string" ? item.trim() : ""))
    .filter(Boolean);
}

function parseToolsConfig(value: unknown): ToolsConfigDoc {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return {};
  }
  return { ...(value as Record<string, unknown>) };
}

function enableTool(config: ToolsConfigDoc, toolName: string): void {
  const disabled = toStringArray(config.disabled).filter((name) => name !== toolName);
  config.disabled = disabled;

  if (Array.isArray(config.enabled)) {
    const enabled = toStringArray(config.enabled);
    if (!enabled.includes(toolName)) {
      enabled.push(toolName);
    }
    config.enabled = enabled;
  }
}

export async function POST(req: NextRequest) {
  let body: unknown = {};
  try {
    body = await req.json();
  } catch {
    body = {};
  }

  const payload = (body ?? {}) as Record<string, unknown>;
  const apiKey = typeof payload.api_key === "string" ? payload.api_key.trim() : "";
  const keyRef = typeof payload.key_ref === "string" ? payload.key_ref.trim() : "";
  const requestedEnable = payload.enable !== false;

  if (!apiKey && !keyRef) {
    return NextResponse.json(
      { error: "Provide api_key or key_ref (for example: env:TAVILY_API_KEY)." },
      { status: 400 }
    );
  }

  const resolver = keyRef || apiKey;
  try {
    const rows = await prisma.$queryRawUnsafe<{ value: unknown }[]>(
      "SELECT value FROM config WHERE key = 'tools' LIMIT 1"
    );
    const current = normalizeJsonValue(rows[0]?.value);
    const nextConfig = parseToolsConfig(current);

    if (!nextConfig.api_keys || typeof nextConfig.api_keys !== "object") {
      nextConfig.api_keys = {};
    }
    nextConfig.api_keys.tavily = resolver;

    if (requestedEnable) {
      enableTool(nextConfig, "web_search");
    }

    await prisma.$queryRawUnsafe(
      `
      INSERT INTO config (key, value, description, updated_at)
      VALUES ('tools', $1::jsonb, 'Tool system configuration', NOW())
      ON CONFLICT (key) DO UPDATE SET value = $1::jsonb, updated_at = NOW()
      `,
      JSON.stringify(nextConfig)
    );

    // Also write legacy flat key so the existing settings table can display toggle state.
    await prisma.$queryRawUnsafe(
      `
      INSERT INTO config (key, value, updated_at)
      VALUES ('tools.web_search.enabled', 'true'::jsonb, NOW())
      ON CONFLICT (key) DO UPDATE SET value = 'true'::jsonb, updated_at = NOW()
      `
    );

    return NextResponse.json({
      ok: true,
      tool: "web_search",
      enabled: true,
      key_source: keyRef ? "reference" : "direct",
    });
  } catch (error: unknown) {
    console.error("Search tool config update failed:", error);
    return NextResponse.json(
      {
        error:
          error instanceof Error ? error.message : "Failed to configure search tool",
      },
      { status: 500 }
    );
  }
}
