/**
 * Every status the API can return, mapped to how it is stamped: a word, a tone (ink colour) and a glyph.
 * The word and glyph carry the meaning on their own, so colour is never the only signal (WCAG 1.4.1).
 */
import type { Tone } from "@/design/palette";
import type { AgreementStatus, MilestoneStatus, SettlementStatus } from "@/api/types";

export type Glyph = "check" | "half" | "ring" | "cross" | "pause" | "clock" | "dot" | "arrow" | "back";

export type StampSpec = {
  label: string;
  tone: Tone;
  glyph: Glyph;
  /** Terminal states get the double border. */
  final?: boolean;
  /** One plain sentence: what this state means for the people involved. */
  meaning: string;
};

export const MILESTONE_STAMPS: Record<MilestoneStatus, StampSpec> = {
  pending: { label: "Pending", tone: "neutral", glyph: "ring", meaning: "Waiting for funding or evidence." },
  funded: { label: "Held", tone: "held", glyph: "half", meaning: "Funds are on hold in PayPal until the tranche verifies." },
  evidence_received: { label: "Received", tone: "held", glyph: "dot", meaning: "Evidence is in; verification starts shortly." },
  verifying: { label: "Verifying", tone: "held", glyph: "clock", meaning: "Checks and the verifier are reading the evidence." },
  needs_review: { label: "In review", tone: "review", glyph: "pause", meaning: "A person on the payer side has to decide." },
  verified: { label: "Verified", tone: "settled", glyph: "check", meaning: "Every criterion is met and cited; ready to release." },
  settling: { label: "Settling", tone: "held", glyph: "arrow", meaning: "Payment sent to PayPal; waiting for confirmation." },
  held: { label: "In window", tone: "held", glyph: "clock", meaning: "Captured from the payer; paid to the payee when the dispute window closes unless someone contests it." },
  contested: { label: "Contested", tone: "alert", glyph: "pause", meaning: "Someone objected during the dispute window; the payout waits for a resolution." },
  settled: { label: "Settled", tone: "settled", glyph: "check", final: true, meaning: "Paid to the payee." },
  refunded: { label: "Refunded", tone: "neutral", glyph: "back", final: true, meaning: "Captured, then returned to the payer after a contest." },
  payout_failed: { label: "Payout failed", tone: "alert", glyph: "cross", meaning: "The money was captured but paying the payee failed; the payout can be retried." },
  settlement_failed: { label: "Failed", tone: "alert", glyph: "cross", meaning: "PayPal didn't complete the payment; it can be retried." },
  disputed: { label: "Disputed", tone: "alert", glyph: "pause", meaning: "The payee disputed a rejection; the payer side reviews again." },
  overdue: { label: "Overdue", tone: "alert", glyph: "clock", meaning: "The due date passed without evidence." },
};

export const AGREEMENT_STAMPS: Record<AgreementStatus, StampSpec> = {
  draft: { label: "Draft", tone: "neutral", glyph: "ring", meaning: "Criteria can still change." },
  awaiting_confirmation: { label: "Signing", tone: "review", glyph: "half", meaning: "Waiting for every party to sign the criteria." },
  active: { label: "Active", tone: "held", glyph: "dot", meaning: "Criteria are locked; tranches can be funded and verified." },
  completed: { label: "Completed", tone: "settled", glyph: "check", final: true, meaning: "Every tranche settled." },
  cancelled: { label: "Cancelled", tone: "neutral", glyph: "cross", final: true, meaning: "No further money moves." },
};

export const SETTLEMENT_STAMPS: Record<SettlementStatus, StampSpec> = {
  submitted: { label: "Submitted", tone: "held", glyph: "arrow", meaning: "Sent to PayPal." },
  succeeded: { label: "Paid", tone: "settled", glyph: "check", final: true, meaning: "PayPal confirmed it." },
  failed: { label: "Failed", tone: "alert", glyph: "cross", meaning: "PayPal refused or reversed it." },
  unclaimed: { label: "Unclaimed", tone: "review", glyph: "pause", meaning: "Sent to an email with no PayPal account yet." },
  expired: { label: "Expired", tone: "neutral", glyph: "clock", final: true, meaning: "The hold or invoice lapsed." },
};

export type InvitationState = "pending" | "accepted" | "revoked" | "expired";
export const INVITATION_STAMPS: Record<InvitationState, StampSpec> = {
  pending: { label: "Invited", tone: "review", glyph: "ring", meaning: "Hasn't accepted yet." },
  accepted: { label: "Joined", tone: "settled", glyph: "check", meaning: "Accepted for their organization." },
  revoked: { label: "Revoked", tone: "neutral", glyph: "cross", final: true, meaning: "The invitation was withdrawn." },
  expired: { label: "Expired", tone: "neutral", glyph: "clock", final: true, meaning: "The invitation lapsed unused." },
};

export function invitationState(i: { accepted_at: string | null; revoked_at: string | null; expires_at: string }, now = Date.now()): InvitationState {
  if (i.accepted_at) return "accepted";
  if (i.revoked_at) return "revoked";
  if (Date.parse(i.expires_at) < now) return "expired";
  return "pending";
}

/** Payee-side to-do: tranches waiting for evidence (or a settlement fix). */
export const NEEDS_EVIDENCE: StampSpec = { label: "Your turn", tone: "review", glyph: "arrow", meaning: "Tranches are waiting for your evidence." };

/** The happy path through a tranche, for the state track. Side states (disputed, overdue, failed) are stamped. */
export const ESCROW_TRACK: MilestoneStatus[] = ["pending", "funded", "evidence_received", "verifying", "verified", "settling", "held", "settled"];
export const INVOICE_TRACK: MilestoneStatus[] = ["pending", "evidence_received", "verifying", "verified", "settling", "settled"];

/** Where a status sits on the track; review and side states sit at the verification step. */
export function trackIndex(status: MilestoneStatus, track: MilestoneStatus[]): number {
  const direct = track.indexOf(status);
  if (direct >= 0) return direct;
  if (status === "needs_review" || status === "disputed") return track.indexOf("verifying");
  if (status === "settlement_failed") return track.indexOf("settling");
  if (status === "contested" || status === "payout_failed") return track.indexOf("held");
  if (status === "refunded") return track.length - 1;
  return 0; // overdue: nothing has happened yet
}

/** Statuses during which the server is working and the UI should poll. */
export const IN_FLIGHT: ReadonlySet<MilestoneStatus> = new Set(["evidence_received", "verifying", "settling"]);
