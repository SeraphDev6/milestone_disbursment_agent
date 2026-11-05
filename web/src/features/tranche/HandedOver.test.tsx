import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { GitHubHandoverRead } from "@/api/types";
import { deliveryText } from "@/features/agreement/Packet";
import { axeViolations } from "@/test/axe";
import { HandedOver } from "./Handover";

const base: GitHubHandoverRead = {
  id: "h1",
  milestone_id: "m1",
  evidence_id: "ev1",
  method: "pull_request",
  source_repo: "devco/shop-work",
  pinned_sha: "a".repeat(40),
  target_repo: "acme/shop",
  transferred_to: null,
  transfer_to: ["acme"],
  status: "delivered",
  attempts: 1,
  tree_sha: "c".repeat(40),
  pr_url: "https://github.com/acme/shop/pull/3",
  archive_sha256: null,
  error: null,
  delivered_at: "2027-09-02T00:00:00Z",
};

test("a delivered PR handover links the PR and shows the tree it matched", async () => {
  const { container } = render(<HandedOver handovers={[base]} orgId="o1" retry={{ mutateAsync: vi.fn(), isPending: false }} />);
  expect(screen.getByText("Pull request into acme/shop")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Open the pull request/ })).toHaveAttribute("href", base.pr_url);
  expect(screen.getByText(/files match tree/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Retry now" })).not.toBeInTheDocument();
  expect(await axeViolations(container)).toEqual([]);
});

test("a waiting handover says why and can be retried", async () => {
  const mutateAsync = vi.fn().mockResolvedValue({ ...base, status: "pending" });
  const waiting = { ...base, status: "pending", pr_url: null, tree_sha: null, delivered_at: null, error: "the client's organization needs the GitHub App installed on acme/shop" };
  render(<HandedOver handovers={[waiting]} orgId="o1" retry={{ mutateAsync, isPending: false }} />);
  expect(screen.getByText(/needs the GitHub App installed/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Retry now" }));
  expect(mutateAsync).toHaveBeenCalledWith("h1");
});

test("an archive handover offers the download", () => {
  const archive = { ...base, method: "archive", target_repo: null, pr_url: null, archive_sha256: "d".repeat(64) };
  render(<HandedOver handovers={[archive]} orgId="o1" retry={{ mutateAsync: vi.fn(), isPending: false }} />);
  expect(screen.getByText("Source archive")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Download archive" })).toBeInTheDocument();
});

test("a transfer handover says where to transfer, then links the repo once it arrived", async () => {
  const waiting = { ...base, method: "transfer", status: "pending", target_repo: null, pr_url: null, tree_sha: null, delivered_at: null, error: "devco/shop-work hasn't been transferred to the client yet" };
  const { rerender, container } = render(<HandedOver handovers={[waiting]} orgId="o1" retry={{ mutateAsync: vi.fn(), isPending: false }} />);
  expect(screen.getByText(/transfer devco\/shop-work to acme/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Check now" })).toBeInTheDocument();
  expect(await axeViolations(container)).toEqual([]);

  const done = { ...waiting, status: "delivered", transferred_to: "acme/shop-work", target_repo: "acme/shop-work", error: null, delivered_at: "2027-09-02T00:00:00Z" };
  rerender(<HandedOver handovers={[done]} orgId="o1" retry={{ mutateAsync: vi.fn(), isPending: false }} />);
  expect(screen.getByText("Repository transferred: acme/shop-work")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Open acme\/shop-work/ })).toHaveAttribute("href", "https://github.com/acme/shop-work");
  expect(screen.queryByRole("button", { name: "Check now" })).not.toBeInTheDocument();
});

test("the packet describes each handover step", () => {
  expect(deliveryText({ action: "deliverable_handed_over", tree_sha: "c".repeat(40), pr_url: base.pr_url })).toContain("pull/3");
  expect(deliveryText({ action: "deliverable_mismatch", handover_id: "h1", stage: "archive" })).toContain("doesn't reproduce the verified tree");
  expect(deliveryText({ action: "deliverable_mismatch", matches: false })).toContain("Download refused");
  expect(deliveryText({ action: "handover_failed", why: "too big" })).toBe("Handover failed: too big");
  expect(deliveryText({ action: "deliverable_handed_over", transferred_to: "acme/shop-work" })).toContain("transferred to the client as acme/shop-work");
});
