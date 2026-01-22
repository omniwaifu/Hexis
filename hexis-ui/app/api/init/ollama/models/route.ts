import { Ollama } from "ollama";

export const runtime = "nodejs";

const DEFAULT_HOST = "http://127.0.0.1:11434";

export async function GET() {
  const host = process.env.OLLAMA_HOST || process.env.OLLAMA_URL || DEFAULT_HOST;
  try {
    const client = new Ollama({ host });
    const response = await client.list();
    const models = Array.isArray(response?.models)
      ? response.models
          .map((model: any) => (typeof model?.name === "string" ? model.name : null))
          .filter((name: string | null) => name)
      : [];
    return Response.json({ models });
  } catch (err: any) {
    return Response.json(
      { models: [], error: err?.message || "Unable to reach Ollama." },
      { status: 503 }
    );
  }
}
