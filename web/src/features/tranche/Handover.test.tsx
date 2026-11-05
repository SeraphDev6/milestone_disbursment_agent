import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { AuditEventRead, EvidenceRead } from "@/api/types";
import { axeViolations } from "@/test/axe";
import { HandoverButton, isCommitPinned, latestHandovers } from "./Handover";

const PINNED = "a".repeat(40);
const evidence = {
  id: "ev1",
  source: "github",
  evidence_type: "code_change",
  title: "PR #7: final",
  excerpt: null,
  dated: null,
  check_results: {},
  artifact_uris: [],
  has_file: false,
  received_at: "2027-09-01T00:00:00Z",
  pinned_ref: PINNED,
  payer_visibility: "hash_only",
} as EvidenceRead;

test("only commit-pinned evidence offers a handover check", () => {
  expect(isCommitPinned(evidence)).toBe(true);
  expect(isCommitPinned({ ...evidence, pinned_ref: "sha256:abc" })).toBe(false);
});

test("the latest check per evidence item wins", () => {
  const ev = (matches: boolean, at: string) =>
    ({ action: "handover_checked", at, actor: "user:x", detail: { evidence_id: "ev1", matches, how: matches ? "same_tree" : "different", delivered_sha: PINNED } }) as unknown as AuditEventRead;
  const out = latestHandovers([ev(false, "2027-09-01T00:00:00Z"), ev(true, "2027-09-02T00:00:00Z")]);
  expect(out.ev1).toMatchObject({ matches: true, how: "same_tree" });
});

test("checking a repo sends repo and ref and shows the result", async () => {
  const mutateAsync = vi.fn().mockResolvedValue({ evidence_id: "ev1", pinned_ref: PINNED, delivered_sha: "b".repeat(40), matches: true, how: "same_tree" });
  render(<HandoverButton evidence={evidence} check={{ mutateAsync, isPending: false }} />);
  await userEvent.click(screen.getByRole("button", { name: /Check handover/ }));
  const check = screen.getByRole("button", { name: "Check" });
  expect(check).toBeDisabled();
  await userEvent.type(screen.getByRole("textbox", { name: "Repo" }), "harbor/site");
  await userEvent.click(check);
  expect(mutateAsync).toHaveBeenCalledWith({ evidenceId: "ev1", body: { repo: "harbor/site", ref: "main" } });
  expect(await screen.findByText("Matches what was verified")).toBeInTheDocument();
  expect(await axeViolations(screen.getByRole("dialog"))).toEqual([]);
});

test("a malformed SHA can't be sent", async () => {
  const mutateAsync = vi.fn();
  render(<HandoverButton evidence={evidence} check={{ mutateAsync, isPending: false }} />);
  await userEvent.click(screen.getByRole("button", { name: /Check handover/ }));
  await userEvent.click(screen.getByRole("radio", { name: /commit SHA/ }));
  await userEvent.type(screen.getByRole("textbox", { name: "Delivered commit SHA" }), "abc123");
  expect(screen.getByRole("button", { name: "Check" })).toBeDisabled();
  expect(screen.getByRole("textbox", { name: "Delivered commit SHA" })).toHaveAttribute("aria-invalid", "true");
});
