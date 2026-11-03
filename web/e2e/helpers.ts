import { expect, type Page } from "@playwright/test";

/** Collect uncaught errors and console errors so a test fails on them. */
export function watchErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() === "error" && !/Failed to load resource/.test(m.text())) errors.push(`console: ${m.text()}`);
  });
  return errors;
}

export async function signIn(page: Page, email: string) {
  await page.goto("/sign-in");
  await page.getByLabel("Email").fill(email);
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Open the sign-in link" }).click();
  await expect(page).not.toHaveURL(/sign-in|verify/);
}

/** Load the demo grant from /demo (mock mode; no sign-in needed). Lands on the agreement as the funder. */
export async function loadDemo(page: Page, start: "Signed and active" | "Before signing" = "Signed and active", windowDays = 0) {
  await page.goto("/demo");
  await expect(page.getByRole("heading", { level: 1, name: "Try the whole loop in three minutes" })).toBeVisible();
  await page.getByText(start, { exact: true }).click();
  if (windowDays) await page.getByLabel("Dispute window").fill(String(windowDays));
  await page.getByRole("button", { name: "Load demo grant" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Riverside Literacy Grant 2027" })).toBeVisible({ timeout: 20_000 });
}

export async function switchPerson(page: Page, emailPrefix: RegExp) {
  await page.getByRole("button", { name: /account menu/ }).click();
  await page.getByRole("menuitem", { name: emailPrefix }).click();
}

/** Add files to the evidence basket through the real file input. */
export async function addFiles(page: Page, files: { name: string; mimeType: string; buffer: Buffer }[]) {
  await page.locator('#add-evidence input[type="file"]').setInputFiles(files);
  for (const f of files) await expect(page.getByRole("list").getByText(f.name, { exact: true })).toBeVisible();
}

/** Act as one of the demo's people, from the demo page's cast. */
export async function actAs(page: Page, role: "Payer" | "Payee" | "Reviewer") {
  await page.goto("/demo");
  const card = page.getByRole("listitem").filter({ has: page.getByText(role, { exact: true }) });
  await card.getByRole("button").click();
  await expect(page.getByRole("heading", { level: 1, name: "Riverside Literacy Grant 2027" })).toBeVisible();
}
