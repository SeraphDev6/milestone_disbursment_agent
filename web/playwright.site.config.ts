import { defineConfig, devices } from "@playwright/test";

/**
 * Accessibility check for the marketing site (site/ -> _site/). Build it first: `node site/build.mjs` from the repo
 * root. Serves _site/ with Python's http.server, which is on every CI runner and developer machine.
 *   cd web && npx playwright test -c playwright.site.config.ts
 */
export default defineConfig({
  testDir: "e2e-site",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [["list"]],
  use: {
    baseURL: process.env.SITE_BASE_URL ?? "http://localhost:8008",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: process.env.SITE_BASE_URL
    ? undefined
    : {
        command: "python3 -m http.server 8008 --bind 127.0.0.1 --directory ../_site",
        url: "http://localhost:8008/",
        reuseExistingServer: true,
        stdout: "ignore",
        stderr: "ignore",
      },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
