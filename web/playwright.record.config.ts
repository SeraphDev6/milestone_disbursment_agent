import { defineConfig, devices } from "@playwright/test";

/**
 * Records the demo walkthrough as a video to narrate over (record/walkthrough.spec.ts). Same servers as e2e:
 *   API:  PUBLIC_BASE_URL=http://localhost:5173 uv run python scripts/dev_server.py --port 8765
 *   Web:  MDE_API=http://localhost:8765 npm run dev
 *   Rec:  npm run record            → recordings/walkthrough.mp4 + walkthrough-cues.md
 * Set E2E_BASE_URL to record somewhere else (the sandbox deployment needs real PayPal sign-ins, so it's not hands-off).
 */
const size = { width: 1600, height: 900 };

export default defineConfig({
  testDir: "record",
  timeout: 10 * 60_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  outputDir: "test-results/record",
  use: {
    ...devices["Desktop Chrome"],
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5173",
    viewport: size,
    deviceScaleFactor: 1,
    video: { mode: "on", size },
    colorScheme: (process.env.THEME as "light" | "dark" | undefined) ?? "light",
    launchOptions: { slowMo: Number(process.env.SLOWMO ?? 60) },
  },
});
