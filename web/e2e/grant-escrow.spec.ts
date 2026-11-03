import { expect, test } from "@playwright/test";
import { addFiles, loadDemo, switchPerson, watchErrors } from "./helpers";
import { SAMPLES } from "./samples";

test("grant escrow: fund, pre-check, submit, review, release, settle", async ({ page, context }) => {
  const errors = watchErrors(page);
  await loadDemo(page);
  await expect(page.getByText("Agreement status: Active")).toBeVisible();

  // Funder funds tranche 1 through the mock PayPal approval page.
  await page.getByRole("link", { name: /Tranche 1: Tranche 1: spring workshops/ }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Tranche 1: spring workshops" })).toBeVisible();
  await page.getByRole("button", { name: "Fund USD 10,000.00" }).click();
  const popup = context.waitForEvent("page");
  await page.getByRole("alertdialog").getByRole("button", { name: "Continue to PayPal" }).click();
  const paypal = await popup;
  await expect(paypal.getByRole("heading", { name: "Tranche funded" })).toBeVisible();
  await paypal.close();
  await page.bringToFront();
  await page.reload();
  await expect(page.getByText("Tranche status: Held")).toBeVisible();

  // Grantee pre-checks thin evidence, then submits what the criteria ask for.
  await switchPerson(page, /director\+/);
  await page.getByRole("link", { name: "Riverside Literacy Grant 2027" }).first().click();
  await page.getByRole("link", { name: /spring workshops/ }).click();
  await addFiles(page, [SAMPLES.thin]);
  await page.getByRole("button", { name: "Pre-check without submitting" }).click();
  await expect(page.getByText("Not everything is met yet")).toBeVisible();
  await page.getByRole("button", { name: /Remove Workshop photos note/ }).click();
  await addFiles(page, [SAMPLES.signIn, SAMPLES.budget]);
  await page.getByRole("button", { name: "Submit evidence" }).click();
  await expect(page.getByText(/Tranche status: (Verified|In review)/)).toBeVisible({ timeout: 20_000 });

  // Funder approves if needed, then releases; mock PayPal confirms by webhook.
  await switchPerson(page, /officer\+/);
  await page.getByRole("link", { name: "Riverside Literacy Grant 2027" }).first().click();
  await page.getByRole("link", { name: /spring workshops/ }).click();
  await expect(page.getByText(/Tranche status: (Verified|In review)/)).toBeVisible();
  if (await page.getByText("Tranche status: In review").isVisible()) {
    await page.getByRole("button", { name: "Approve" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Approve" }).click();
    // With a live hold, approval captures straight away; otherwise it waits verified for a manual release.
    await expect(page.getByText(/Tranche status: (Verified|Settling|In window|Settled)/)).toBeVisible();
  }
  if (await page.getByText("Tranche status: Verified").isVisible()) {
    await page.getByRole("button", { name: "Release USD 10,000.00" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Release USD 10,000.00" }).click();
  }

  // Riverside hasn't connected PayPal, so the capture is paid on with a platform Payout that settles asynchronously.
  await expect(page.getByText("Tranche status: Settled")).toBeVisible({ timeout: 30_000 });

  // The money shows up on the agreement and in the ledger.
  await page.getByRole("link", { name: "Riverside Literacy Grant 2027" }).click();
  await expect(page.getByRole("group", { name: "Funding" }).or(page.getByLabel("Funding"))).toContainText("10,000.00");
  expect(errors).toEqual([]);
});
