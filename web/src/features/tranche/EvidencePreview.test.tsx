import { render, screen } from "@testing-library/react";
import type { EvidenceRead } from "@/api/types";
import { EvidenceList } from "./Evidence";

const withheld = {
  id: "e1", source: "upload", evidence_type: "document", title: "report.pdf", excerpt: null, dated: null, check_results: {},
  artifact_uris: [], has_file: true, has_preview: true, preview_url: "https://preview.devco.dev/checkout", preview_expires_at: "2027-10-01T00:00:00Z",
  received_at: "2027-09-01T00:00:00Z", pinned_ref: "sha256:abc", file_sha256: "abc", payer_visibility: "preview", withheld: true,
} as EvidenceRead;

test("withheld evidence offers the preview and the payee's link, never the original", () => {
  render(<EvidenceList evidence={[withheld]} orgId="o1" />);
  expect(screen.getByRole("button", { name: /Open preview/ })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Download original/ })).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Try the payee's preview/ })).toHaveAttribute("href", withheld.preview_url);
  expect(screen.getByText(/not checked; shown until/)).toBeInTheDocument();
});

test("no preview button when there's no preview", () => {
  render(<EvidenceList evidence={[{ ...withheld, has_preview: false, preview_url: null, preview_expires_at: null }]} orgId="o1" />);
  expect(screen.queryByRole("button", { name: /Open preview/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("link")).not.toBeInTheDocument();
});
