import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTypeScript from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTypeScript,
  {
    // Browser boundary: application code must not import Node-only modules.
    // e2e/ helpers (such as the OTP outbox reader) run only in the Playwright
    // process and must never reach a browser bundle. e2e/ itself is not
    // covered, so its Node imports stay legitimate.
    files: ["src/**/*.{ts,tsx,js,jsx,mjs,cjs}"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              regex: "^node:",
              message: "Node built-in modules cannot be used in browser application code.",
            },
            {
              regex: "(^|/)e2e(/|$)",
              message: "e2e/ modules are Node-only test helpers; do not import them from src/.",
            },
          ],
        },
      ],
    },
  },
  globalIgnores([".next/**", "out/**", "build/**", "next-env.d.ts"]),
]);

export default eslintConfig;
