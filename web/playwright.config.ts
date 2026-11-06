import { defineConfig, devices } from "@playwright/test";

/**
 * E2E runs against the real backend with stand-in PayPal, email and GitHub (scripts/dev_server.py), through the
 * Vite dev proxy. People sign in through the demo's cast (POST /api/demo/cast), since no email is sent.
 *   API:  PUBLIC_BASE_URL=http://localhost:5173 uv run python scripts/dev_server.py --port 8765
 *   Web:  MDE_API=http://localhost:8765 npm run dev
 * Set E2E_BASE_URL to point elsewhere.
 */
export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
