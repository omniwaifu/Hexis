import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    setupFiles: "./test/setup.ts",
    globals: true,
    include: ["app/**/*.test.ts", "app/**/*.test.tsx"],
    exclude: ["**/e2e/**", "**/node_modules/**", "**/.next/**"],
  },
});
