import { expect, test } from "@playwright/test";
import { actAs, loadDemo, watchErrors } from "./helpers";

test("demo page: dispute window, the reviewer contests, the payee accepts and the payer is refunded", async ({ page, context }) => {
  test.setTimeout(90_000);
  const errors = watchErrors(page);
  await loadDemo(page, "Signed and active", 3);
  await expect(page.getByText("3-day dispute window")).toBeVisible();

  // Fund tranche 1 as the funder.
  await page.getByRole("link", { name: /Tranche 1: Tranche 1: spring workshops/ }).click();
  await page.getByRole("button", { name: "Fund USD 10,000.00" }).click();
  const popup = context.waitForEvent("page");
  await page.getByRole("alertdialog").getByRole("button", { name: "Continue to PayPal" }).click();
  await (await popup).close();

  // Demo shortcut: mark it verified, then release it from the tranche page.
  await page.goto("/app/demo");
  const row = page.getByRole("listitem").filter({ hasText: "spring workshops" });
  await expect(row.getByText(/^Held/)).toBeVisible();
  await row.getByRole("button", { name: /Mark verified/ }).click();
  await expect(row.getByText(/^Verified/)).toBeVisible();
  await row.getByRole("link", { name: /spring workshops/ }).click();
  await page.getByRole("button", { name: "Release USD 10,000.00" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Release USD 10,000.00" }).click();
  await expect(page.getByText("Tranche status: In window")).toBeVisible();
  await expect(page.getByText(/In the dispute window until/)).toBeVisible();

  // The reviewer contests inside the window.
  await actAs(page, "Reviewer");
  await page.getByRole("link", { name: /Tranche 1: Tranche 1: spring workshops/ }).click();
  await page.getByRole("button", { name: "Contest this payment" }).click();
  await page.getByRole("alertdialog").getByLabel("What's wrong").fill("Sign-in sheets for session 3 are unsigned.");
  await page.getByRole("alertdialog").getByRole("button", { name: "Contest" }).click();
  await expect(page.getByText("Tranche status: Contested")).toBeVisible();

  // The payee can only concede: accept and refund the payer. Paying itself isn't offered.
  await actAs(page, "Payee");
  await page.getByRole("link", { name: /Tranche 1: Tranche 1: spring workshops/ }).click();
  await expect(page.getByRole("paragraph").filter({ hasText: "“Sign-in sheets for session 3 are unsigned.”" })).toBeVisible();
  await expect(page.getByRole("button", { name: /pay$/i })).toHaveCount(0);
  await page.getByRole("button", { name: "Accept and refund the payer" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Refund USD 10,000.00" }).click();
  await expect(page.getByText("Tranche status: Refunded")).toBeVisible({ timeout: 20_000 });

  // The ledger shows the capture and the refund.
  await page.getByRole("link", { name: "Riverside Literacy Grant 2027" }).click();
  await page.getByRole("link", { name: "Ledger", exact: true }).click();
  await expect(page.getByRole("gridcell", { name: "Refunded to payer" })).toBeVisible();
  await expect(page.getByRole("gridcell", { name: "Refund to payer" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("the payee connects PayPal through (mock) partner onboarding", async ({ page, context }) => {
  const errors = watchErrors(page);
  await loadDemo(page);
  await actAs(page, "Payee");
  await expect(page.getByText("Connect PayPal to be paid directly")).toBeVisible();
  await page.getByRole("link", { name: "Connect PayPal" }).click();
  await expect(page.getByRole("heading", { name: "PayPal account" })).toBeVisible();
  const popup = context.waitForEvent("page");
  await page.getByRole("button", { name: "Connect PayPal" }).click();
  const paypal = await popup;
  await expect(paypal.getByRole("heading", { name: "PayPal connected" })).toBeVisible();
  await paypal.close();
  await page.getByRole("button", { name: "Check status" }).click();
  await expect(page.getByText(/^Connected/)).toBeVisible();
  expect(errors).toEqual([]);
});
