import { readdir, readFile } from "fs/promises";
import path from "path";

export const runtime = "nodejs";

const CHARACTERS_DIR = path.resolve(process.cwd(), "..", "services", "characters");

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const loadFile = searchParams.get("load");

  if (loadFile) {
    // Load a specific character file
    const safeName = path.basename(loadFile);
    if (!safeName.endsWith(".json")) {
      return Response.json({ error: "Invalid file" }, { status: 400 });
    }
    try {
      const content = await readFile(path.join(CHARACTERS_DIR, safeName), "utf-8");
      const card = JSON.parse(content);
      return Response.json({ card });
    } catch {
      return Response.json({ error: "Character not found" }, { status: 404 });
    }
  }

  // List available characters
  try {
    const files = await readdir(CHARACTERS_DIR);
    const characters = await Promise.all(
      files
        .filter((f) => f.endsWith(".json"))
        .map(async (filename) => {
          try {
            const content = await readFile(path.join(CHARACTERS_DIR, filename), "utf-8");
            const card = JSON.parse(content);
            const name = card?.data?.name ?? card?.name ?? filename.replace(/\.json$/, "");
            return { filename, name };
          } catch {
            return { filename, name: filename.replace(/\.json$/, "") };
          }
        })
    );
    return Response.json({ characters });
  } catch {
    return Response.json({ characters: [] });
  }
}
