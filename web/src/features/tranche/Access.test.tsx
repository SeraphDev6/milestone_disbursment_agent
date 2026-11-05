import { render, screen } from "@testing-library/react";
import type { AuditEventRead, PayerView } from "@/api/types";
import { axeViolations } from "@/test/axe";
import { AccessLog, PayerSees } from "./Access";

const ev = (action: string, detail: Record<string, unknown>, actor = "user:u1@org:o1"): AuditEventRead =>
  ({ id: action, milestone_id: "m1", actor, action, from_status: null, to_status: null, detail, at: "2027-09-02T00:00:00Z" }) as AuditEventRead;

test("the access log says which side opened what, and how much it saw", async () => {
  const names = { orgs: { o1: "Acme", o2: "Dev Co" }, users: { u1: "Pat", u2: "Sam" } };
  const { container } = render(
    <AccessLog
      names={names}
      events={[
        ev("review_approved", {}),
        ev("evidence_viewed", { side: "payer", view: "filtered", evidence_ids: ["e1", "e2"] }),
        ev("evidence_viewed", { side: "payee", view: "full", evidence_ids: ["e1"] }, "user:u2@org:o2"),
        ev("deliverable_downloaded", { evidence_id: "e1", sha256: "a".repeat(64) }),
      ]}
    />,
  );
  const rows = screen.getAllByRole("listitem");
  expect(rows).toHaveLength(3); // the review isn't an access
  expect(rows[0]).toHaveTextContent("Viewed 2 items, as withheld");
  expect(rows[0]).toHaveTextContent("Payer side");
  expect(rows[1]).toHaveTextContent("in full");
  expect(rows[2]).toHaveTextContent("Downloaded the original file");
  expect(await axeViolations(container)).toEqual([]);
});

test("the payer's view says it's withheld and lists only what the payer gets", () => {
  const view = {
    withheld: true,
    verdict: null,
    evidence: [{ id: "e1", source: "upload", evidence_type: "document", title: "report.txt", excerpt: null, dated: null, check_results: {}, artifact_uris: [], has_file: true, received_at: "2027-09-01T00:00:00Z", pinned_ref: "sha256:abc", file_sha256: "abc", payer_visibility: "hash_only", withheld: true }],
  } as unknown as PayerView;
  render(<PayerSees view={view} criteria={[]} orgId="o1" payerName="Acme" />);
  expect(screen.getByText(/all Acme sees until the tranche is paid/)).toBeInTheDocument();
  expect(screen.getByText("report.txt")).toBeInTheDocument();
});
