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

export type DemoPerson = { key: "funder" | "grantee" | "reviewer"; email: string; token: string; user: { id: string; email: string } };

/** Three fresh people with sessions, from the server's demo (no email is sent, so this is how e2e signs in). */
export async function castDemo(page: Page): Promise<Record<DemoPerson["key"], DemoPerson>> {
  const r = await page.request.post("/api/demo/cast");
  expect(r.ok(), await r.text()).toBeTruthy();
  const { people } = (await r.json()) as { people: DemoPerson[] };
  return Object.fromEntries(people.map((p) => [p.key, p])) as Record<DemoPerson["key"], DemoPerson>;
}

/** Put a person's session in this browser the way the app stores it (src/auth/session.ts) and make it active. */
export async function actAsPerson(page: Page, person: DemoPerson) {
  if (page.url() === "about:blank") await page.goto("/sign-in");
  await page.evaluate(({ token, user }) => {
    const KEY = "mde.sessions";
    const current = JSON.parse(localStorage.getItem(KEY) ?? "[]") as { token: string; user: { id: string; email: string }; persistent: boolean; addedAt: string }[];
    if (!current.some((s) => s.user.id === user.id)) current.push({ token, user, persistent: true, addedAt: new Date().toISOString() });
    localStorage.setItem(KEY, JSON.stringify(current));
    localStorage.setItem("mde.active", user.id);
  }, person);
  await page.goto("/");
  await expect(page).not.toHaveURL(/sign-in|verify/);
}

/** Sign in as one of a fresh cast's people. Returns the cast, for the others. */
export async function signIn(page: Page, key: DemoPerson["key"] = "funder") {
  const cast = await castDemo(page);
  await actAsPerson(page, cast[key]);
  return cast;
}

/** Load the demo grant from /demo (no sign-in needed). Lands on the agreement as the funder. */
export async function loadDemo(page: Page, start: "Signed and active" | "Before signing" = "Signed and active", windowDays = 0) {
  await page.goto("/demo");
  await expect(page.getByRole("heading", { level: 1, name: "Try the whole loop in three minutes" })).toBeVisible();
  await page.getByText(start, { exact: true }).click();
  if (windowDays) await page.getByRole("textbox", { name: "Dispute window" }).fill(String(windowDays));
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
