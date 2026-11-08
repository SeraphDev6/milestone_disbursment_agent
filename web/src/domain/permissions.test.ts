import { can, canOnTranche, RULES, type Seat } from "./permissions";

const seat = (role: Seat["role"], orgRole: Seat["orgRole"]): Seat => ({ role, orgRole, orgName: "Harbor" });

// The spec's "Who can do what" table (docs/SPEC.md), one row per action: payer, payee, reviewer minimums.
const SPEC: Record<string, [string | null, string | null, string | null]> = {
  view: ["viewer", "viewer", "viewer"],
  edit_tranches: ["member", null, null],
  confirm_criteria: ["admin", "admin", "admin"],
  set_auto_release: ["admin", null, null],
  invite: ["admin", null, null],
  fund: ["member", null, null],
  void: ["member", null, null],
  review: ["admin", null, "member"],
  release: ["admin", null, null],
  submit_evidence: [null, "member", null],
  connect_github: [null, "admin", null],
  dispute: [null, "admin", null],
  compliance_packet: ["member", null, "member"],
  manage_subscriptions: ["admin", "admin", "admin"],
  contest: ["admin", null, "member"],
  resolve_for_payee: ["admin", null, "member"],
  resolve_for_payer: [null, "admin", "member"],
  resolve_shortfall: ["admin", null, null],
  accept_shortfall: [null, "admin", null],
};

test("a co-funder funds and contests; the lead funder does the rest", () => {
  const co: Seat = { ...seat("payer", "admin"), lead: false };
  expect(can(co, "fund").ok).toBe(true);
  expect(can(co, "contest").ok).toBe(true);
  expect(can(co, "resolve_for_payee").ok).toBe(true);
  expect(can(co, "review")).toEqual({ ok: false, reason: expect.stringContaining("Only the lead funder") });
  expect(can(co, "release").ok).toBe(false);
  expect(can(co, "resolve_shortfall").ok).toBe(false);
  const lead: Seat = { ...seat("payer", "admin"), lead: true };
  expect(can(lead, "review").ok).toBe(true);
  const ctx = { status: "pending" as const, mode: "escrow_release" as const, agreementActive: true, hasHold: false, anyHold: true };
  expect(canOnTranche(lead, "shortfall", ctx).ok).toBe(true);
  expect(canOnTranche(co, "fund", ctx).ok).toBe(true);
  expect(canOnTranche(co, "fund", { ...ctx, hasHold: true }).ok).toBe(false);
});

test.each(Object.entries(SPEC))("%s matches the spec", (action, [payer, payee, reviewer]) => {
  const rule = RULES[action as keyof typeof RULES];
  expect([rule.payer ?? null, rule.payee ?? null, rule.reviewer ?? null]).toEqual([payer, payee, reviewer]);
});

test("a payee can never approve its own tranche", () => {
  expect(can(seat("payee", "owner"), "review").ok).toBe(false);
});

test("reasons name the role you'd need and the one you have", () => {
  const d = can(seat("payer", "member"), "release");
  expect(d.ok).toBe(false);
  if (!d.ok) expect(d.reason).toMatch(/admin.*Harbor.*member/);
});

const ctx = { status: "pending" as const, mode: "escrow_release" as const, agreementActive: true, hasHold: false };

test("funding needs escrow mode, no live hold and an active agreement", () => {
  const payer = seat("payer", "member");
  expect(canOnTranche(payer, "fund", ctx).ok).toBe(true);
  expect(canOnTranche(payer, "fund", { ...ctx, hasHold: true }).ok).toBe(false);
  expect(canOnTranche(payer, "fund", { ...ctx, mode: "invoice_on_verify" }).ok).toBe(false);
  expect(canOnTranche(payer, "fund", { ...ctx, agreementActive: false }).ok).toBe(false);
});

test("evidence is accepted in the server's ACCEPTS_EVIDENCE states only", () => {
  const payee = seat("payee", "member");
  for (const status of ["pending", "funded", "evidence_received", "verifying", "needs_review"] as const)
    expect(canOnTranche(payee, "submit_evidence", { ...ctx, status }).ok).toBe(true);
  for (const status of ["verified", "settling", "settled", "overdue", "disputed"] as const)
    expect(canOnTranche(payee, "submit_evidence", { ...ctx, status }).ok).toBe(false);
});

test("after capture: pay now, contest and resolve follow the spec", () => {
  const held = { ...ctx, status: "held" as const };
  expect(canOnTranche(seat("payer", "admin"), "pay_now", held).ok).toBe(true);
  expect(canOnTranche(seat("payer", "admin"), "pay_now", { ...ctx, status: "payout_failed" }).ok).toBe(true);
  expect(canOnTranche(seat("payer", "member"), "pay_now", held).ok).toBe(false);
  expect(canOnTranche(seat("reviewer", "member"), "contest", held).ok).toBe(true);
  expect(canOnTranche(seat("reviewer", "member"), "contest", { ...held, windowClosed: true }).ok).toBe(false);
  expect(canOnTranche(seat("payee", "owner"), "contest", held).ok).toBe(false);
  const contested = { ...ctx, status: "contested" as const };
  expect(canOnTranche(seat("payer", "admin"), "resolve_release", contested).ok).toBe(true);
  expect(canOnTranche(seat("payer", "admin"), "resolve_refund", contested).ok).toBe(false); // payer can't refund itself
  expect(canOnTranche(seat("payee", "admin"), "resolve_refund", contested).ok).toBe(true);
  expect(canOnTranche(seat("payee", "admin"), "resolve_release", contested).ok).toBe(false); // payee can't pay itself
  expect(canOnTranche(seat("reviewer", "member"), "resolve_release", contested).ok).toBe(true);
  expect(canOnTranche(seat("reviewer", "member"), "resolve_refund", contested).ok).toBe(true);
});

test("dispute only after a rejection", () => {
  const payee = seat("payee", "admin");
  expect(canOnTranche(payee, "dispute", { ...ctx, rejected: false }).ok).toBe(false);
  expect(canOnTranche(payee, "dispute", { ...ctx, rejected: true }).ok).toBe(true);
});
