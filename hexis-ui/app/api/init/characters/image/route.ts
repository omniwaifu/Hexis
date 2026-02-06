import { readFile, access } from "fs/promises";
import path from "path";

export const runtime = "nodejs";

const CHARACTERS_DIR = path.resolve(process.cwd(), "..", "services", "characters");

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const name = searchParams.get("name");
  if (!name) {
    return new Response("Missing name parameter", { status: 400 });
  }

  // Sanitize: only allow alphanumeric, hyphen, underscore
  const safeName = path.basename(name).replace(/[^a-zA-Z0-9_-]/g, "");
  if (!safeName) {
    return new Response("Invalid name", { status: 400 });
  }

  const filePath = path.join(CHARACTERS_DIR, `${safeName}.jpg`);

  try {
    await access(filePath);
    const buffer = await readFile(filePath);
    return new Response(buffer, {
      headers: {
        "Content-Type": "image/jpeg",
        "Cache-Control": "public, max-age=86400, immutable",
      },
    });
  } catch {
    return new Response("Image not found", { status: 404 });
  }
}
