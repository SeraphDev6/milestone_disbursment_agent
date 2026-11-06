import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

/**
 * The marketing site's accessibility contract, the same one the app is held to (web/DESIGN.md):
 * axe with the WCAG 2.0–2.2 A/AA rules on every page in all three themes, no console errors, one h1, a working
 * skip link, no horizontal scrolling at 320px, and a render under forced colours.
 */
const PAGES = ["/", "/security.html", "/accessibility.html", "/privacy.html", "/terms.html", "/404.html"];
const THEMES = ["paper", "carbon", "contrast"] as const;
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

function watchErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() === "error" && !/Failed to load resource/.test(m.text())) errors.push(`console: ${m.text()}`);
  });
  return errors;
}

async function setTheme(page: Page, theme: (typeof THEMES)[number]) {
  await page.addInitScript((t) => localStorage.setItem("mde.theme", t), theme);
}

for (const path of PAGES) {
  test.describe(path, () => {
    for (const theme of THEMES) {
      test(`axe passes in ${theme}`, async ({ page }) => {
        const errors = watchErrors(page);
        await setTheme(page, theme);
        await page.goto(path);
        await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
        const results = await new AxeBuilder({ page }).withTags(TAGS).analyze();
        expect(results.violations.map((v) => `${v.id}: ${v.help}\n  ${v.nodes.map((n) => n.target.join(" ")).join("\n  ")}`)).toEqual([]);
        expect(errors).toEqual([]);
      });
    }

    test("structure: one h1, landmarks, skip link reaches main", async ({ page }) => {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
      await expect(page.getByRole("banner")).toBeVisible();
      await expect(page.getByRole("main")).toBeVisible();
      await expect(page.getByRole("contentinfo")).toBeVisible();
      await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
      await page.keyboard.press("Tab");
      const skip = page.getByRole("link", { name: "Skip to content" });
      await expect(skip).toBeFocused();
      await page.keyboard.press("Enter");
      await expect(page.locator("#main")).toBeFocused();
    });

    test("reflows at 320px without horizontal scrolling", async ({ page }) => {
      await page.setViewportSize({ width: 320, height: 800 });
      await page.goto(path);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow).toBeLessThanOrEqual(0);
    });

    test("renders under forced colours", async ({ page }) => {
      await page.emulateMedia({ forcedColors: "active" });
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      const results = await new AxeBuilder({ page }).withTags(TAGS).disableRules(["color-contrast"]).analyze();
      expect(results.violations.map((v) => v.id)).toEqual([]);
    });
  });
}

test("theme picker applies and remembers a theme", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Theme").selectOption("carbon");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "carbon");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "carbon");
  await expect(page.getByLabel("Theme")).toHaveValue("carbon");
  await page.getByLabel("Theme").selectOption("system");
  await expect(page.locator("html")).not.toHaveAttribute("data-theme", /./);
});

test("every internal link resolves", async ({ page, request }) => {
  const seen = new Set<string>();
  for (const path of PAGES) {
    await page.goto(path);
    const hrefs = await page.locator("a[href]").evaluateAll((as) => as.map((a) => (a as HTMLAnchorElement).href));
    for (const href of hrefs) {
      const url = new URL(href);
      if (url.origin !== new URL(page.url()).origin || seen.has(url.pathname)) continue;
      seen.add(url.pathname);
      const r = await request.get(url.pathname);
      expect(r.status(), `${path} links to ${url.pathname}`).toBeLessThan(400);
    }
  }
});
