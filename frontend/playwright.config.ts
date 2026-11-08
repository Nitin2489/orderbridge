import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests against the real stack: Next.js dev server and the
 * FastAPI backend with seeded demo data. Nothing is mocked. See e2e/README.md.
 *
 * Sign-in tests read one-time codes from the backend's development OTP
 * outbox through e2e/otp-outbox.ts, in this Node process only. Set
 * E2E_OTP_OUTBOX to the backend's AUTH_DEV_OTP_OUTBOX_PATH; without it those
 * tests are skipped with a reason.
 */
export default defineConfig({
  testDir: "./e2e",
  // Only *.spec.ts: e2e/*.test.ts are Vitest unit tests of the helpers.
  testMatch: "**/*.spec.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: "list",
  use: {
    // Must be an origin listed in the backend's FRONTEND_ORIGINS.
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
});
