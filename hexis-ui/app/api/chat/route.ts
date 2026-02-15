export const runtime = "nodejs";

/**
 * Canonical chat implementation lives in the Python FastAPI server (`hexis-api`).
 *
 * This route is a thin streaming proxy so the browser can call same-origin
 * `/api/chat` while all LLM/tool logic remains in Python.
 */

const DEFAULT_UPSTREAM = "http://127.0.0.1:43817";

function resolveUpstreamUrl(pathname: string): string {
  const base =
    process.env.HEXIS_API_URL ||
    process.env.HEXIS_API_BASE_URL ||
    DEFAULT_UPSTREAM;
  const normalizedBase = base.endsWith("/") ? base : `${base}/`;
  const normalizedPath = pathname.replace(/^\//, "");
  return new URL(normalizedPath, normalizedBase).toString();
}

function sseError(message: string, status = 200): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(
        encoder.encode(`event: error\ndata: ${JSON.stringify({ message })}\n\n`)
      );
      controller.close();
    },
  });

  return new Response(stream, {
    status,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    },
  });
}

export async function POST(request: Request): Promise<Response> {
  let bodyText = "";
  try {
    bodyText = await request.text();
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    return sseError(message || "Failed to read request body.");
  }

  const url = resolveUpstreamUrl("/api/chat");

  let upstream: Response;
  try {
    upstream = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": request.headers.get("content-type") || "application/json",
        Accept: "text/event-stream",
      },
      body: bodyText,
      signal: request.signal,
    });
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    // Keep status=200 so the UI's SSE parser can surface the error payload.
    return sseError(
      `Failed to reach Hexis API at ${url}: ${message || "Unknown error"}`
    );
  }

  // Pass through SSE stream. Force no-buffering headers defensively.
  const headers = new Headers(upstream.headers);
  headers.set("Content-Type", "text/event-stream");
  headers.set("Cache-Control", "no-cache");
  headers.set("X-Accel-Buffering", "no");

  return new Response(upstream.body, {
    status: upstream.status,
    headers,
  });
}
