/**
 * Mirror of RULES in app/services/access.py: action -> {agreement role: minimum org role}.
 * The server stays the authority; this decides what to enable and how to explain what isn't.
 * tests: permissions.test.ts pins this table to the spec's "Who can do what".
 */
import type { MilestoneStatus, OrgRole, Role, SettlementMode } from "@/api/types";

export type Action =
  | "view"
  | "edit_tranches"
  | "confirm_criteria"
  | "set_auto_release"
  | "invite"
  | "fund"
  | "void"
  | "review"
  | "release"
  | "submit_evidence"
  | "connect_github"
  | "dispute"
  | "compliance_packet"
  | "manage_subscriptions"
  | "assign"
  | "contest"
  | "resolve_for_payee"
  | "resolve_for_payer";

const RANK: Record<OrgRole, number> = { viewer: 0, member: 1, admin: 2, owner: 3 };
export const atLeast = (have: OrgRole, need: OrgRole) => RANK[have] >= RANK[need];

export const RULES: Record<Action, Partial<Record<Role, OrgRole>>> = {
  view: { payer: "viewer", payee: "viewer", reviewer: "viewer" },
  edit_tranches: { payer: "member" },
  confirm_criteria: { payer: "admin", payee: "admin", reviewer: "admin" },
  set_auto_release: { payer: "admin" },
  invite: { payer: "admin" },
  fund: { payer: "member" },
  void: { payer: "member" },
  review: { payer: "admin", reviewer: "member" },
  release: { payer: "admin" },
  submit_evidence: { payee: "member" },
  connect_github: { payee: "admin" },
  dispute: { payee: "admin" },
  compliance_packet: { payer: "member", reviewer: "member" },
  manage_subscriptions: { payer: "admin", payee: "admin", reviewer: "admin" },
  assign: { payer: "member", payee: "member" },
  // A contest pauses a held tranche's payout. Each side can only concede; a reviewer can decide either way.
  contest: { payer: "admin", reviewer: "member" },
  resolve_for_payee: { payer: "admin", reviewer: "member" },
  resolve_for_payer: { payee: "admin", reviewer: "member" },
};

const VERB: Record<Action, string> = {
  view: "view this agreement",
  edit_tranches: "edit tranches",
  confirm_criteria: "sign the criteria",
  set_auto_release: "turn on auto-release",
  invite: "invite people",
  fund: "fund tranches",
  void: "cancel holds",
  review: "approve or reject",
  release: "release payments",
  submit_evidence: "submit evidence",
  connect_github: "connect GitHub",
  dispute: "dispute a rejection",
  compliance_packet: "export the compliance packet",
  manage_subscriptions: "manage webhooks",
  assign: "assign people",
  contest: "contest a payment",
  resolve_for_payee: "pay the payee",
  resolve_for_payer: "refund the payer",
};

export type Seat = { role: Role; orgRole: OrgRole; orgName: string };

export type Decision = { ok: true } | { ok: false; reason: string };

/** Can this seat take this action at all (ignoring the tranche's state)? */
export function can(seat: Seat, action: Action): Decision {
  const need = RULES[action][seat.role];
  if (!need) return { ok: false, reason: `As the ${seat.role} on this agreement, you can't ${VERB[action]}.` };
  if (!atLeast(seat.orgRole, need))
    return {
      ok: false,
      reason: `To ${VERB[action]} you need to be ${need === "admin" ? "an admin" : `a ${need}`} or above in ${seat.orgName}; you're a ${seat.orgRole}.`,
    };
  return { ok: true };
}

/** Tranche actions, combining the role rule with the state the server requires. */
export type TrancheAction = "fund" | "void" | "release" | "pay_now" | "contest" | "resolve_release" | "resolve_refund" | "review" | "dispute" | "submit_evidence" | "precheck" | "reverify";

const VOIDABLE: MilestoneStatus[] = ["funded"];
const RELEASABLE: MilestoneStatus[] = ["verified", "settlement_failed"];
const REVIEWABLE: MilestoneStatus[] = ["needs_review", "disputed"];
// ACCEPTS_EVIDENCE in app/services/verification.py
const EVIDENCE_OPEN: MilestoneStatus[] = ["pending", "funded", "evidence_received", "verifying", "needs_review"];

export function canOnTranche(
  seat: Seat,
  action: TrancheAction,
  ctx: { status: MilestoneStatus; mode: SettlementMode; agreementActive: boolean; hasHold: boolean; rejected?: boolean; windowClosed?: boolean },
): Decision {
  const role = (a: Action) => can(seat, a);
  const state = (ok: boolean, why: string): Decision => (ok ? { ok: true } : { ok: false, reason: why });
  if (!ctx.agreementActive)
    return { ok: false, reason: "Every party has to sign the criteria before tranches can move." };
  switch (action) {
    case "fund": {
      if (ctx.mode !== "escrow_release") return { ok: false, reason: "Invoice-mode tranches are billed when they verify; nothing is funded up front." };
      const r = role("fund");
      if (!r.ok) return r;
      if (ctx.hasHold) return { ok: false, reason: "This tranche already has a live hold." };
      return state(!["settling", "held", "contested", "settled", "refunded", "payout_failed"].includes(ctx.status), "This tranche is already being paid.");
    }
    case "void": {
      const r = role("void");
      return r.ok ? state(VOIDABLE.includes(ctx.status), "Only a held tranche with no evidence yet can be cancelled.") : r;
    }
    case "release": {
      const r = role("release");
      return r.ok ? state(RELEASABLE.includes(ctx.status), "A tranche is released once it's verified.") : r;
    }
    case "pay_now": {
      // POST /milestones/{id}/payout: waive the rest of the window, or retry a failed payout (needs release rights).
      const r = role("release");
      return r.ok ? state(ctx.status === "held" || ctx.status === "payout_failed", "Only a captured tranche can be paid out.") : r;
    }
    case "contest": {
      const r = role("contest");
      if (!r.ok) return r;
      if (ctx.status !== "held") return { ok: false, reason: "Only a payment held in its dispute window can be contested." };
      return state(!ctx.windowClosed, "The dispute window has closed.");
    }
    case "resolve_release": {
      const r = role("resolve_for_payee");
      return r.ok ? state(ctx.status === "contested", "Nothing is contested.") : r;
    }
    case "resolve_refund": {
      const r = role("resolve_for_payer");
      return r.ok ? state(ctx.status === "contested", "Nothing is contested.") : r;
    }
    case "review": {
      const r = role("review");
      if (!r.ok) return r;
      return state(REVIEWABLE.includes(ctx.status), "There's nothing to review until a verdict lands in review.");
    }
    case "dispute": {
      const r = role("dispute");
      return r.ok ? state(!!ctx.rejected && ["pending", "funded"].includes(ctx.status), "You can dispute after the payer side rejects a verdict.") : r;
    }
    case "submit_evidence":
    case "precheck": {
      const r = role("submit_evidence");
      if (!r.ok) return r;
      return action === "precheck" ? { ok: true } : state(EVIDENCE_OPEN.includes(ctx.status), "Evidence is closed while this tranche is being verified or paid.");
    }
    case "reverify":
      return state(ctx.status === "evidence_received", "Verification re-runs only on evidence that hasn't been judged yet.");
  }
}

/** Why the payer can't approve this tranche: the agreement requires a reviewer's approval, or the work is withheld
 * from the payer and the agreement leaves withheld tranches to its reviewer. Either way the payer can still reject.
 * `withheldForReviewer`: deliverable_release is on_capture, the tranche isn't revealed yet, the option is on and a
 * reviewer has joined. */
export function approveBlockedByReviewerRule(seat: Seat, requireReviewer: boolean, withheldForReviewer = false): string | null {
  if (seat.role !== "payer") return null;
  if (requireReviewer) return "This agreement requires a reviewer's approval. You can still reject.";
  if (withheldForReviewer) return "The evidence is withheld from you until this tranche is paid, so your reviewer decides. You can still reject.";
  return null;
}
