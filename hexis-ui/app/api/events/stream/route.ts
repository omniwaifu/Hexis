export const runtime = "nodejs";

/**
 * SSE proxy to the Python FastAPI gateway events stream.
 *
 * The browser connects to same-origin `/api/events/stream` and receives
 * real-time gateway events (heartbeat, maintenance, webhook, etc.) via SSE.
 * Used by the dashboard and sidebar to trigger instant status refreshes
 * instead of 30-second polling.
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

export async function GET(): Promise<Response> {
  const url = resolveUpstreamUrl("/api/events/stream");

  let upstream: Response;
  try {
    upstream = await fetch(url, {
      headers: { Accept: "text/event-stream" },
    });
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(
          encoder.encode(
            `event: error\ndata: ${JSON.stringify({ message: `Failed to reach Hexis API: ${message}` })}\n\n`
          )
        );
        controller.close();
      },
    });
    return new Response(stream, {
      status: 200,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
      },
    });
  }

  const headers = new Headers(upstream.headers);
  headers.set("Content-Type", "text/event-stream");
  headers.set("Cache-Control", "no-cache");
  headers.set("X-Accel-Buffering", "no");

  return new Response(upstream.body, {
    status: upstream.status,
    headers,
  });
}
