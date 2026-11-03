import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { loadDemo, watchErrors } from "./helpers";

const THEMES = ["paper", "carbon", "contrast"] as const;
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

async function routes(page: Page): Promise<string[]> {
  await loadDemo(page);
  const agreement = new URL(page.url()).pathname;
  const org = agreement.replace(/\/a\/.*/, "");
  await page.getByRole("link", { name: /spring workshops/ }).click();
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  const tranche = new URL(page.url()).pathname;
  return [
    org,
    `${org}/portfolio`,
    `${org}/new`,
    `${org}/settings`,
    agreement,
    `${agreement}/criteria`,
    `${agreement}/parties`,
    `${agreement}/ledger`,
    `${agreement}/activity`,
    `${agreement}/integrations`,
    `${agreement}/packet`,
    tranche,
    "/app/account",
    "/app/design",
    "/app/demo",
  ];
}

async function scan(page: Page, label: string) {
  await page.waitForLoadState("networkidle");
  await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
  // ag-grid's virtualised header uses aria roles axe misreads in jsdom-less scans only when empty; scan everything.
  const r = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  const summary = r.violations.map((v) => `${label}: ${v.id} (${v.impact}) x${v.nodes.length}: ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`);
  return summary;
}

test("axe: every route, every theme, no WCAG 2.2 AA violations", async ({ page }) => {
  test.setTimeout(240_000);
  const errors = watchErrors(page);
  const list = await routes(page);
  const found: string[] = [];
  for (const theme of THEMES) {
    await page.evaluate((t) => localStorage.setItem("mde.theme", t), theme);
    for (const path of list) {
      await page.goto(path);
      found.push(...(await scan(page, `${theme} ${path.replace(/[0-9a-f-]{36}/g, ":id")}`)));
    }
  }
  console.log(found.join("\n"));
  expect(found).toEqual([]);
  expect(errors).toEqual([]);
});

test("forced colors and 320px reflow: no horizontal page scroll", async ({ page }) => {
  test.setTimeout(120_000);
  const list = await routes(page);
  await page.emulateMedia({ forcedColors: "active" });
  await page.setViewportSize({ width: 320, height: 800 });
  const overflow: string[] = [];
  for (const path of list) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const wide = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    if (wide > 1) overflow.push(`${path.replace(/[0-9a-f-]{36}/g, ":id")} overflows by ${wide}px`);
  }
  expect(overflow).toEqual([]);
});

test("keyboard only: sign the criteria", async ({ page }) => {
  await loadDemo(page, "Before signing");
  await page.goto(page.url() + "/criteria");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  const box = page.getByRole("checkbox", { name: /I've read every criterion/ });
  await box.focus();
  await page.keyboard.press("Space");
  await expect(box).toBeChecked();
  await page.getByRole("button", { name: "Sign the criteria" }).focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toBeVisible();
  // Focus is trapped in the dialog; Escape closes it and returns focus.
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("button", { name: "Sign the criteria" })).toBeFocused();
  await page.keyboard.press("Enter");
  await dialog.getByRole("button", { name: "Sign" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByText("Criteria signed", { exact: true })).toBeVisible();
});
