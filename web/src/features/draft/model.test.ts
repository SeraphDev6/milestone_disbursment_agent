import { DEMO_GRANT } from "@/features/demo/seed";
import { agreementOut, agreementProblems, blankDraft, criterionOut, leadShare, normalizeDraft, tranchesFrom, tranchesOut, tranchesProblems, type CriterionForm } from "./model";

const base: CriterionForm = {
  key: "k",
  statement: "At least 200 people attend",
  metric: "attendees",
  comparator: "at_least",
  target: "200",
  unit: "people",
  evidence_required: ["spreadsheet"],
  evidence_description: "Signed sign-in sheets",
  verification: "check",
  check_id: "row_count",
  due_by: null,
  weight: "1",
};

test("targets are typed by comparator, like Measure's validator", () => {
  expect(criterionOut(base).measure.target).toBe(200);
  expect(criterionOut({ ...base, comparator: "exists", target: "x" }).measure.target).toBeNull();
  expect(criterionOut({ ...base, comparator: "on_or_before", target: "2027-09-30" }).measure.target).toBe("2027-09-30");
  expect(criterionOut({ ...base, verification: "model" }).check_id).toBeNull();
});

test("the demo grant round-trips without loss", () => {
  const d = { ...blankDraft(), title: DEMO_GRANT.title, total_amount: "30000.00", counterparty_email: "x@y.org", milestones: tranchesFrom(DEMO_GRANT.milestones) };
  expect(agreementProblems(d, "all")).toEqual([]);
  const out = agreementOut(d);
  expect(out.milestones.map((m) => m.criteria.map((c) => c.measure))).toEqual(DEMO_GRANT.milestones.map((m) => m.criteria.map((c) => ({ ...c.measure, target: c.measure.target ?? null, unit: c.measure.unit ?? null }))));
});

test("shape problems point at the exact field", () => {
  const [t] = tranchesFrom(DEMO_GRANT.milestones);
  const broken = [{ ...t!, amount: "", criteria: [{ ...t!.criteria[0]!, metric: "", check_id: "ci_green" }] }];
  const p = tranchesProblems(broken);
  expect(p.map((x) => x.path)).toEqual(["milestones[0].amount", "milestones[0].criteria[0].measure.metric", "milestones[0].criteria[0].check_id"]);
  expect(p[0]!.href).toBe("#f-milestones-0-amount");
});

test("payee, reviewers and co-funders must be different people", () => {
  const d = { ...blankDraft(), counterparty_email: "a@x.org", reviewer_emails: ["A@x.org"] };
  expect(agreementProblems(d, "parties").map((p) => p.message)).toContain("The payee, reviewers and co-funders must be different people");
  const invoiced = { ...blankDraft(), settlement_mode: "invoice_on_verify" as const, counterparty_email: "a@x.org", co_funder_emails: ["b@x.org"] };
  expect(agreementProblems(invoiced, "parties").map((p) => p.message)).toContain("Co-funding needs escrow: each funder places a PayPal hold on its share");
});

test("co-funders' shares leave the lead the rest, and are dropped when empty", () => {
  const [t] = tranchesFrom(DEMO_GRANT.milestones);
  const shared = { ...t!, amount: "10000.00", shares: [{ email: "b@x.org", amount: "4000" }, { email: "c@x.org", amount: "" }] };
  expect(leadShare(shared)).toBe(6000);
  expect(tranchesOut([shared])[0]!.shares).toEqual([{ email: "b@x.org", amount: "4000.00" }]);
  const over = { ...shared, shares: [{ email: "b@x.org", amount: "12000" }] };
  expect(tranchesProblems([over]).map((p) => p.path)).toContain("milestones[0].shares");
});

test("a draft saved by an older release gets the fields added since", () => {
  // Shape a browser saved before co-funding shipped: no shares on tranches, no co-funder fields on the draft.
  const old = { ...blankDraft(), milestones: [{ key: "k1", title: "T1", amount: "100", due_date: null, criteria: [{ key: "c1", statement: "s" }] }] } as unknown as Record<string, unknown>;
  delete old.co_funder_emails;
  delete old.funding_window_days;
  const d = normalizeDraft(old);
  expect(d.co_funder_emails).toEqual([]);
  expect(d.funding_window_days).toBe(14);
  expect(d.milestones[0]?.shares).toEqual([]);
  expect(d.milestones[0]?.criteria[0]?.comparator).toBe("at_least");
  expect(() => agreementOut(d)).not.toThrow();
  expect(normalizeDraft(null).milestones).toHaveLength(1);
});
