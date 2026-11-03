import { defineConfig, devices } from "@playwright/test";

/**
 * E2E runs against the real backend in mock mode, through the Vite dev proxy.
 *   API:  MODE=mock LLM=mock PUBLIC_BASE_URL=http://localhost:5173 uv run uvicorn app.main:app --port 8765
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
