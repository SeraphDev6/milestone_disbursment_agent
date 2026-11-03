import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { MILESTONE_STAMPS } from "@/domain/status";
import { axeViolations } from "@/test/axe";
import { Button, ConfirmDialog, ErrorSummary, Seal, Stamp, TextField, ThresholdMeter, Totals } from ".";

test("a stamp says its status in words, not only colour", async () => {
  const { container } = render(<Stamp spec={MILESTONE_STAMPS.needs_review} context="Tranche status" />);
  expect(container).toHaveTextContent("Tranche status: In review");
  expect(container).toHaveTextContent("A person on the payer side has to decide.");
  expect(await axeViolations(container)).toEqual([]);
});

test("a field with an error is invalid and describes itself", async () => {
  const { container } = render(<TextField label="Email" value="" onChange={() => undefined} error="Enter an email address" />);
  const input = screen.getByRole("textbox", { name: "Email" });
  expect(input).toHaveAttribute("aria-invalid", "true");
  expect(input).toHaveAccessibleDescription(/Enter an email address/);
  expect(await axeViolations(container)).toEqual([]);
});

test("the error summary takes focus and links to fields", async () => {
  render(
    <>
      <ErrorSummary problems={[{ message: "Enter the total", href: "#total" }]} focusKey={1} />
      <input id="total" aria-label="Total" />
    </>,
  );
  const summary = screen.getByRole("alert");
  expect(summary).toHaveFocus();
  await userEvent.click(screen.getByRole("link", { name: "Enter the total" }));
  expect(screen.getByLabelText("Total")).toHaveFocus();
});

test("totals, seal and meter have accessible names", async () => {
  const { container } = render(
    <>
      <Totals label="Funding" currency="USD" lines={[{ label: "Released", value: "100" }]} total={{ label: "Total", value: "300" }} />
      <Seal hash={"a".repeat(64)} />
      <ThresholdMeter label="Verifier confidence" value={0.7} threshold={0.85} />
    </>,
  );
  expect(screen.getByText("USD 300.00")).toBeInTheDocument();
  expect(screen.getByRole("meter", { name: "Verifier confidence" })).toHaveAttribute("aria-valuetext", expect.stringContaining("below"));
  expect(await axeViolations(container)).toEqual([]);
});

function DialogHarness({ onConfirm }: { onConfirm: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button onPress={() => setOpen(true)}>Release</Button>
      <ConfirmDialog isOpen={open} onOpenChange={setOpen} title="Release this payment?" summary="Pay USD 10,000.00" confirmLabel="Release USD 10,000.00" onConfirm={onConfirm} />
    </>
  );
}

test("confirm dialogs are alert dialogs, trap focus and return it", async () => {
  const onConfirm = vi.fn();
  render(<DialogHarness onConfirm={onConfirm} />);
  const opener = screen.getByRole("button", { name: "Release" });
  await userEvent.click(opener);
  const dialog = screen.getByRole("alertdialog", { name: "Release this payment?" });
  expect(dialog).toContainElement(document.activeElement as HTMLElement);
  expect(await axeViolations(document.body)).toEqual([]);
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  await waitFor(() => expect(opener).toHaveFocus());
  expect(onConfirm).not.toHaveBeenCalled();
});
