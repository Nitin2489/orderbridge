import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    // e2e/*.test.ts are unit tests of Node-side E2E helpers (they declare
    // the node environment); the Playwright specs are e2e/*.spec.ts.
    include: ["src/**/*.test.{ts,tsx}", "e2e/**/*.test.ts"],
    clearMocks: true,
    unstubEnvs: true,
    unstubGlobals: true,
  },
});
