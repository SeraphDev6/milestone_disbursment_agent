import { expect, test, type Page } from "@playwright/test";
import { addFiles, loadDemo, signIn, switchPerson, actAsPerson, watchErrors } from "./helpers";
import { SAMPLES } from "./samples";

async function openTranche(page: Page, name: RegExp) {
  await page.getByRole("link", { name: "Riverside Literacy Grant 2027" }).first().click();
  await page.getByRole("link", { name }).click();
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
}

test("SOW in invoice mode, built in the wizard: invite, accept, sign, verify, invoice, pay", async ({ page, context }) => {
  test.setTimeout(120_000);
  const errors = watchErrors(page);
  // The client and the contractor are two of the demo's people (the only way to sign in without email).
  const cast = await signIn(page, "funder");
  const contractor = cast.grantee.email;

  // Client creates their org.
  await page.getByLabel("Organization name").fill("Acme Client Co");
  await page.getByLabel("PayPal account email").fill("acme@example.org");
  await page.getByRole("button", { name: "Create organization" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Ledger" })).toBeVisible();

  // Draft from the sample, switch to invoice mode, invite the contractor.
  await page.getByRole("link", { name: "New agreement" }).first().click();
  await page.getByRole("button", { name: "Use the sample grant" }).click();
  await page.getByLabel("Title").fill("Website rebuild SOW");
  await page.getByRole("radio", { name: /Invoice on verify/ }).check({ force: true });
  await expect(page.getByText("Balanced")).toBeVisible();
  await page.getByRole("button", { name: "Next: people" }).click();
  await page.getByLabel("Payee's email").fill(contractor);
  await page.getByRole("button", { name: "Next: review" }).click();
  await expect(page.getByText("Criteria lint is clear")).toBeVisible();
  await page.getByRole("button", { name: "Create and send invitations" }).click();
  await expect(page.getByRole("heading", { name: "Invitations sent" })).toBeVisible();

  // Contractor signs in, finds the invitation waiting on their home page, accepts with a new org.
  await actAsPerson(page, cast.grantee);
  await page.getByRole("link", { name: /Review and accept/ }).click();
  await expect(page.getByRole("heading", { level: 1, name: /receive payments under “Website rebuild SOW”/ })).toBeVisible();
  await page.getByLabel("Organization name").fill("Pixel Studio");
  await page.getByLabel("PayPal account email").fill("studio@example.org");
  await page.getByRole("button", { name: "Accept invitation" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Website rebuild SOW" })).toBeVisible();

  // Both sign.
  for (const who of [null, /^officer-/]) {
    if (who) {
      await switchPerson(page, who);
      await page.getByRole("link", { name: "Website rebuild SOW" }).first().click();
    }
    await page.getByRole("link", { name: "Criteria", exact: true }).click();
    await page.getByText(/I've read every criterion/).click();
    await page.getByRole("button", { name: "Sign the criteria" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Sign", exact: true }).click();
    await expect(page.getByText("Criteria signed", { exact: true })).toBeVisible();
  }
  await expect(page.getByText("Agreement status: Active")).toBeVisible();

  // The contractor submits evidence for tranche 1; the client approves it, sends the invoice, pays it on the stand-in PayPal page.
  await switchPerson(page, /^director-/);
  await page.getByRole("link", { name: "Website rebuild SOW" }).first().click();
  await page.getByRole("link", { name: /spring workshops/ }).click();
  await addFiles(page, [SAMPLES.signIn, SAMPLES.budget]);
  await page.getByRole("button", { name: "Submit evidence" }).click();
  await expect(page.getByText(/Tranche status: (In review|Verified)/)).toBeVisible({ timeout: 20_000 });
  await switchPerson(page, /^officer-/);
  await page.getByRole("link", { name: "Website rebuild SOW" }).first().click();
  await page.getByRole("link", { name: /spring workshops/ }).click();
  await expect(page.getByText(/Tranche status: (In review|Verified)/)).toBeVisible();
  if (await page.getByText("Tranche status: In review").isVisible()) {
    await page.getByRole("button", { name: "Approve" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Approve" }).click();
    // In invoice mode an approval sends the invoice at once.
    await expect(page.getByText(/Tranche status: (Verified|Settling)/)).toBeVisible();
  }
  if (await page.getByText("Tranche status: Verified").isVisible()) {
    await page.getByRole("button", { name: "Send the USD 10,000.00 invoice" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Send invoice" }).click();
  }
  await expect(page.getByText("Tranche status: Settling")).toBeVisible();
  const popup = context.waitForEvent("page");
  await page.getByRole("link", { name: /Pay this tranche/ }).click();
  const paypal = await popup;
  await paypal.getByRole("button", { name: "Pay invoice" }).click();
  await expect(paypal.getByText("PAID")).toBeVisible();
  await paypal.close();
  await page.bringToFront();
  await page.reload();
  await expect(page.getByText("Tranche status: Settled")).toBeVisible();
  await page.getByRole("link", { name: "Website rebuild SOW" }).click();
  await page.getByRole("link", { name: "Ledger", exact: true }).click();
  await expect(page.getByRole("gridcell", { name: /Invoice (paid|collected)/ }).first()).toBeVisible();
  expect(errors).toEqual([]);
});

test("rejection tells the payee what's missing; the payee disputes", async ({ page }) => {
  const errors = watchErrors(page);
  await loadDemo(page);
  await switchPerson(page, /^director-/);
  await openTranche(page, /spring workshops/);
  await addFiles(page, [SAMPLES.thin]);
  await page.getByRole("button", { name: "Submit evidence" }).click();
  await expect(page.getByText("Tranche status: In review")).toBeVisible({ timeout: 20_000 });

  // The payee can see the review buttons aren't theirs.
  await expect(page.getByRole("button", { name: "Approve" })).toHaveCount(0);

  await switchPerson(page, /^officer-/);
  await expect(page.getByRole("heading", { name: /Waiting on you/ })).toBeVisible();
  await page.getByRole("link", { name: "Tranche 1: spring workshops" }).click();
  await page.getByRole("button", { name: "Reject" }).click();
  await page.getByRole("alertdialog").getByLabel(/What should they send/).fill("Attach the signed sign-in sheets and the budget.");
  await page.getByRole("alertdialog").getByRole("button", { name: "Reject" }).click();
  await expect(page.getByText(/Tranche status: (Pending|Held)/)).toBeVisible();

  await switchPerson(page, /^director-/);
  await openTranche(page, /spring workshops/);
  await expect(page.getByText("The last submission was rejected")).toBeVisible();
  await expect(page.getByRole("paragraph").filter({ hasText: "“Attach the signed sign-in sheets and the budget.”" })).toBeVisible();
  await expect(page.getByText(/What would satisfy it/).first()).toBeVisible();
  await page.getByRole("button", { name: "Dispute the rejection" }).click();
  await page.getByRole("alertdialog").getByLabel("Why the rejection is wrong").fill("The note describes attendance the funder saw in person.");
  await page.getByRole("alertdialog").getByRole("button", { name: "Send dispute" }).click();
  await expect(page.getByText("Tranche status: Disputed")).toBeVisible();
  expect(errors).toEqual([]);
});

test("a stale signature is refused and the new criteria are shown", async ({ page }) => {
  await loadDemo(page, "Before signing");
  const agreement = page.url();
  await switchPerson(page, /^director-/);
  await page.getByRole("link", { name: "Riverside Literacy Grant 2027" }).first().click();
  await page.getByRole("link", { name: "Criteria", exact: true }).click();
  await page.getByText(/I've read every criterion/).click();

  // Meanwhile the funder edits the criteria (straight through the API, with the funder's session).
  const funder = await page.evaluate(() => JSON.parse(localStorage.getItem("mde.sessions")!).find((s: { user: { email: string } }) => s.user.email.startsWith("officer-")).token);
  const aid = agreement.split("/a/")[1]!.split("/")[0]!;
  const org = agreement.split("/o/")[1]!.split("/")[0]!;
  const a = await (await page.request.get(`/api/agreements/${aid}`, { headers: { Authorization: `Bearer ${funder}`, "X-Org-Id": org } })).json();
  const ms = a.milestones.map((m: { title: string; amount: string; due_date: string; criteria: unknown[] }) => ({ title: m.title, amount: m.amount, due_date: m.due_date, criteria: m.criteria }));
  ms[1].title = "Tranche 2: final outcomes report";
  const put = await page.request.put(`/api/agreements/${aid}/milestones`, { headers: { Authorization: `Bearer ${funder}`, "X-Org-Id": org }, data: { milestones: ms } });
  expect(put.ok()).toBe(true);

  await page.getByRole("button", { name: "Sign the criteria" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Sign", exact: true }).click();
  await expect(page.getByText("The criteria changed while you were reading")).toBeVisible();
  await expect(page.getByRole("heading", { name: /final outcomes report/ })).toBeVisible();
});
