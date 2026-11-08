import { useEffect, useState } from "react";
import { API_BASE } from "@/api/client";
import { useNow } from "@/app/useNow";
import type { AgreementRead, MilestoneRead, VerdictRead } from "@/api/types";
import { errorMessage } from "@/api/errors";
import type { useTrancheActions } from "@/api/queries";
import { dateTime, money } from "@/domain/format";
import { approveBlockedByReviewerRule, canOnTranche, isCoFunder, type Seat, type TrancheAction } from "@/domain/permissions";
import { Button, ButtonLink, Callout, ConfirmDialog, PermissionNote, Select, Sheet, TextArea, toast } from "@/design/components";
import s from "./Tranche.module.css";

type Acts = ReturnType<typeof useTrancheActions>;

/**
 * Everything this seat can do to this tranche now. Actions that aren't available stay listed with the
 * reason, so nobody wonders where a button went. Every money-moving action is confirmed in a dialog.
 */
export function Actions({ a, m, seat, verdict, acts, rejected, myPartyId }: { a: AgreementRead; m: MilestoneRead; seat: Seat; verdict: VerdictRead | null; acts: Acts; rejected: boolean; myPartyId?: string }) {
  const [dialog, setDialog] = useState<null | "fund" | "void" | "release" | "approve" | "releaseMet" | "reject" | "dispute" | "payNow" | "contest" | "resolveRelease" | "resolveRefund" | "voidAll" | "shrink" | "cover" | "acceptShrink" | "declineShrink">(null);
  const [contestReason, setContestReason] = useState("");
  const [resolveNote, setResolveNote] = useState("");
  const [coverBy, setCoverBy] = useState<string | null>(null);
  const [shortfallNote, setShortfallNote] = useState("");
  const [note, setNote] = useState("");
  const [reason, setReason] = useState("");
  const [fundUrl, setFundUrl] = useState<string | null>(null);
  // The "approve in PayPal" callout lasts only while PayPal is waiting on this payer.
  useEffect(() => {
    if (fundUrl && !m.awaiting_approval) setFundUrl(null);
  }, [fundUrl, m.awaiting_approval]);
  const now = useNow();
  const windowClosed = !!m.release_due_at && Date.parse(m.release_due_at) <= now;
  // Co-funded: each funder holds its own share. "Has a hold" means this seat's share is held (the lead's share is
  // the tranche less the co-funders'); the tranche is funded once every share is.
  const coFunded = m.shares.length > 0;
  const liveHolds = m.holds.filter((x) => x.paypal_authorization_id);
  const myHolds = m.holds.filter((x) => x.party_id === myPartyId);
  const myShare = coFunded ? (isCoFunder(seat) ? Number(m.shares.find((sh) => sh.email.toLowerCase() === (a.parties.find((p) => p.id === myPartyId)?.invited_email ?? "").toLowerCase())?.amount ?? 0) : Number(m.amount) - m.shares.reduce((n, sh) => n + Number(sh.amount), 0)) : Number(m.amount);
  const myHeld = myHolds.filter((x) => x.paypal_authorization_id).reduce((n, x) => n + Number(x.amount), 0);
  const myDue = Math.max(0, myShare - myHeld);
  const hasHold = coFunded ? myDue <= 0 && myHeld > 0 : liveHolds.length > 0;
  const fullyFunded = Number(m.funded_amount) >= Number(m.amount);
  const gap = Math.max(0, Number(m.amount) - Number(m.funded_amount));
  const ctx = { status: m.status, mode: a.settlement_mode, agreementActive: a.status === "active", hasHold, rejected, windowClosed, anyHold: liveHolds.length > 0, lapsed: m.funding_lapsed, proposed: m.proposed_amount != null };
  const check = (x: TrancheAction) => canOnTranche(seat, x, ctx);
  const payee = a.parties.find((p) => p.role === "payee")?.display_name ?? "the payee";
  const amt = money(m.amount, a.currency);
  const fundAmt = money(coFunded ? myDue : Number(m.amount) - Number(m.amount_released ?? 0), a.currency);
  // Who can take on the unfunded part: the lead, and co-funders who have held their share.
  const coverers = [
    { id: "__lead", label: `${a.parties.find((p) => p.role === "payer" && p.lead)?.display_name ?? "the lead funder"} (lead)` },
    ...a.parties.filter((p) => p.role === "payer" && !p.lead && p.invited_email && liveHolds.some((x) => x.party_id === p.id)).map((p) => ({ id: p.invited_email!, label: p.display_name })),
  ];
  const hasReviewer = a.parties.some((p) => p.role === "reviewer" && p.org_id);
  const withheldForReviewer = a.deliverable_release === "on_capture" && !m.revealed_at && a.reviewer_decides_withheld && hasReviewer;
  const reviewerOnly = approveBlockedByReviewerRule(seat, a.require_reviewer_approval, withheldForReviewer);
  // Partial release: the criteria this verdict meets that no capture has paid for yet, and what they add.
  const unpaidMet = a.partial_release && verdict && !verdict.met ? verdict.criteria_results.filter((r) => r.met && !(m.criteria_paid ?? []).includes(r.criterion_id)).map((r) => r.criterion_id) : [];
  const shareAmt = verdict?.weighted_share != null ? money((Number(m.amount) * verdict.weighted_share - Number(m.amount_released ?? 0)).toFixed(2), a.currency) : null;

  async function run(label: string, fn: () => Promise<unknown>, body?: string) {
    try {
      await fn();
      toast(label, { body });
      setDialog(null);
    } catch (e) {
      setDialog(null);
      toast(`Couldn't ${label.toLowerCase()}`, { body: errorMessage(e), tone: "error" });
    }
  }

  const rows: { key: TrancheAction; show: boolean; button: React.ReactNode }[] = [];
  const add = (key: TrancheAction, button: React.ReactNode, show = true) => rows.push({ key, show, button });

  if (seat.role === "payer" && a.settlement_mode === "escrow_release")
    add("fund", <Button variant="primary" icon="ledger" isDisabled={!check("fund").ok} onPress={() => setDialog("fund")}>{coFunded ? `Fund my share (${fundAmt})` : `Fund ${fundAmt}`}</Button>, (m.status === "pending" || m.status === "overdue") && !fullyFunded);
  if (seat.role === "payer") add("void", <Button icon="close" isDisabled={!check("void").ok} onPress={() => setDialog("void")}>{coFunded ? (isCoFunder(seat) ? "Cancel my hold" : "Cancel every hold") : "Cancel the hold"}</Button>, m.status === "funded" || (coFunded && liveHolds.length > 0 && ["pending", "overdue"].includes(m.status) && (isCoFunder(seat) ? myHeld > 0 : true)));
  if (seat.role === "payer" && coFunded && !isCoFunder(seat)) {
    const sf = check("shortfall");
    add("shortfall", (
      <div className={s.action}>
        <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap" }}>
          <Button icon="arrow" isDisabled={!sf.ok || !m.funding_lapsed} onPress={() => setDialog("shrink")}>Propose {money(m.funded_amount, a.currency)} to {payee}</Button>
          <Button icon="plus" isDisabled={!sf.ok || !m.funding_lapsed} onPress={() => setDialog("cover")}>Ask a funder to cover {money(gap, a.currency)}</Button>
        </div>
        {sf.ok && !m.funding_lapsed && <PermissionNote>These open once the funding window ({a.funding_window_days} days after the first hold) has passed. You can cancel every hold any time.</PermissionNote>}
      </div>
    ), ["pending", "overdue"].includes(m.status) && liveHolds.length > 0 && !fullyFunded);
  }
  if (seat.role === "payee" && m.proposed_amount != null) {
    const an = check("answer_shortfall");
    add("answer_shortfall", (
      <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap" }}>
        <Button variant="primary" icon="check" isDisabled={!an.ok} onPress={() => setDialog("acceptShrink")}>Accept {money(m.proposed_amount, a.currency)}</Button>
        <Button variant="danger" icon="cross" isDisabled={!an.ok} onPress={() => setDialog("declineShrink")}>Decline</Button>
      </div>
    ));
  }
  if (seat.role === "payer" || seat.role === "reviewer") {
    const r = check("review");
    add("review", (
      <div className={s.action}>
        <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap" }}>
          <Button variant="primary" icon="check" isDisabled={!r.ok || !!reviewerOnly} onPress={() => setDialog("approve")}>Approve</Button>
          {unpaidMet.length > 0 && <Button icon="arrow" isDisabled={!r.ok || !!reviewerOnly || !m.paypal_authorization_id} onPress={() => setDialog("releaseMet")}>Pay the met share{shareAmt ? ` (${shareAmt})` : ""}</Button>}
          <Button variant="danger" icon="cross" isDisabled={!r.ok} onPress={() => setDialog("reject")}>Reject</Button>
        </div>
        {reviewerOnly && r.ok && <PermissionNote>{reviewerOnly}</PermissionNote>}
      </div>
    ), ["needs_review", "disputed"].includes(m.status));
  }
  if (seat.role === "payer") add("release", <Button variant="primary" icon="arrow" isDisabled={!check("release").ok} onPress={() => setDialog("release")}>{m.status === "settlement_failed" ? "Retry payment" : a.settlement_mode === "invoice_on_verify" ? `Send the ${amt} invoice` : `Release ${amt}`}</Button>, ["verified", "settlement_failed"].includes(m.status));
  if (seat.role === "payer")
    add("pay_now", <Button variant="primary" icon="arrow" isDisabled={!check("pay_now").ok} onPress={() => setDialog("payNow")}>{m.status === "payout_failed" ? "Retry the payout" : `Pay ${payee} now`}</Button>, m.status === "held" || m.status === "payout_failed");
  if (seat.role === "payer" || seat.role === "reviewer")
    add("contest", <Button variant="danger" icon="pause" isDisabled={!check("contest").ok} onPress={() => setDialog("contest")}>Contest this payment</Button>, m.status === "held");
  if (seat.role !== "payee")
    add("resolve_release", <Button variant="primary" icon="check" isDisabled={!check("resolve_release").ok} onPress={() => setDialog("resolveRelease")}>{seat.role === "payer" ? "Withdraw the contest and pay" : `Decide for ${payee}: pay`}</Button>, m.status === "contested");
  if (seat.role !== "payer")
    add("resolve_refund", <Button variant={seat.role === "payee" ? "danger" : "secondary"} icon="back" isDisabled={!check("resolve_refund").ok} onPress={() => setDialog("resolveRefund")}>{seat.role === "payee" ? "Accept and refund the payer" : "Decide for the payer: refund"}</Button>, m.status === "contested");
  if (seat.role === "payee") add("dispute", <Button icon="warning" isDisabled={!check("dispute").ok} onPress={() => setDialog("dispute")}>Dispute the rejection</Button>, rejected && ["pending", "funded"].includes(m.status));
  add("reverify", <Button variant="quiet" icon="refresh" isDisabled={!check("reverify").ok} isPending={acts.reverify.isPending} onPress={() => run("Verification restarted", () => acts.reverify.mutateAsync())}>Run verification again</Button>, m.status === "evidence_received");

  const visible = rows.filter((r) => r.show);

  return (
    <Sheet title="What you can do" titleLevel={2} tight>
      <div className={s.actions}>
        {fundUrl && (
          <Callout tone="info" title="Approve the hold in PayPal" live actions={<ButtonLink external variant="primary" href={fundUrl} target="_blank" icon="external">Open PayPal</ButtonLink>}>
            <p>PayPal opened in a new tab. Approve the hold there; this page updates when you come back.</p>
          </Callout>
        )}
        {m.pay_path && m.status === "settling" && a.settlement_mode === "invoice_on_verify" && (
          seat.role === "payer" ? (
            <Callout tone="info" title="Payment requested" actions={<ButtonLink external variant="primary" href={API_BASE + m.pay_path} target="_blank" icon="external">Pay this tranche</ButtonLink>}>
              <p>Pay {amt} in PayPal. The tranche settles when PayPal confirms it.</p>
            </Callout>
          ) : (
            <Callout tone="info" title="Payment requested">
              <p>The payer has been asked to pay {amt} in PayPal.</p>
            </Callout>
          )
        )}
        {visible.length === 0 ? (
          <p style={{ margin: 0, color: "var(--text-secondary)" }}>{idleText(m, seat)}</p>
        ) : (
          visible.map((r) => {
            const d = check(r.key);
            return (
              <div key={r.key} className={s.action}>
                {r.button}
                {!d.ok && <PermissionNote>{d.reason}</PermissionNote>}
              </div>
            );
          })
        )}
      </div>

      <ConfirmDialog
        isOpen={dialog === "fund"}
        onOpenChange={(o) => !o && setDialog(null)}
        title={coFunded ? "Fund your share?" : "Fund this tranche?"}
        summary={<p style={{ margin: 0 }}>Place a PayPal hold of <strong>{fundAmt}</strong> for {payee}.{coFunded ? ` The tranche (${amt}) is funded once every funder's share is held.` : ""}</p>}
        confirmLabel="Continue to PayPal"
        isPending={acts.fund.isPending}
        onConfirm={() =>
          run("Funding started", async () => {
            const r = await acts.fund.mutateAsync();
            setFundUrl(r.approve_url);
            window.open(r.approve_url, "_blank", "noopener");
          }, "Approve the hold in the PayPal tab.")
        }
      >
        <p>The money stays in your PayPal account until the tranche verifies and is captured. You can cancel the hold any time before evidence arrives. Holds last 29 days.</p>
      </ConfirmDialog>

      <ConfirmDialog
        isOpen={dialog === "payNow"}
        onOpenChange={(o) => !o && setDialog(null)}
        title={m.status === "payout_failed" ? "Retry the payout?" : "Pay now and waive the dispute window?"}
        summary={<p style={{ margin: 0 }}>Pay <strong>{amt}</strong> to {payee} now{m.status === "held" && m.release_due_at ? `, instead of on ${dateTime(m.release_due_at)}` : ""}.</p>}
        confirmLabel={m.status === "payout_failed" ? "Retry payout" : `Pay ${amt}`}
        isPending={acts.payNow.isPending}
        onConfirm={() => run(m.status === "payout_failed" ? "Payout retried" : "Payout sent", () => acts.payNow.mutateAsync(), "PayPal confirms by webhook.")}
      >
        {m.status === "held" && <p>Nobody can contest this tranche once it's paid.</p>}
      </ConfirmDialog>

      <ConfirmDialog
        isOpen={dialog === "contest"}
        onOpenChange={(o) => !o && setDialog(null)}
        title="Contest this payment?"
        summary={<p style={{ margin: 0 }}>The <strong>{amt}</strong> stays captured but isn't paid to {payee} until the contest is resolved.</p>}
        confirmLabel="Contest"
        confirmVariant="danger"
        isPending={acts.contest.isPending}
        confirmDisabled={!contestReason.trim()}
        onConfirm={() => run("Payment contested", () => acts.contest.mutateAsync(contestReason.trim()), "The payout is paused.")}
      >
        <TextArea label="What's wrong" description="Goes in the audit record and is shown to everyone on the agreement." value={contestReason} onChange={setContestReason} rows={3} maxLength={2000} isRequired />
        <p style={{ margin: 0, color: "var(--text-secondary)" }}>Each side can only concede: the payer can withdraw a contest and pay; the payee can accept it and refund the payer; a reviewer can decide either way. If it isn't resolved, PayPal pays the payee 28 days after capture.</p>
      </ConfirmDialog>

      <ConfirmDialog
        isOpen={dialog === "resolveRelease" || dialog === "resolveRefund"}
        onOpenChange={(o) => !o && setDialog(null)}
        title={dialog === "resolveRelease" ? "Pay the payee?" : "Refund the payer?"}
        summary={<p style={{ margin: 0 }}>{dialog === "resolveRelease" ? <>End the contest and pay <strong>{amt}</strong> to {payee}.</> : <>End the contest and return <strong>{amt}</strong> to {a.parties.find((p) => p.role === "payer")?.display_name ?? "the payer"}.</>}</p>}
        confirmLabel={dialog === "resolveRelease" ? `Pay ${amt}` : `Refund ${amt}`}
        confirmVariant={dialog === "resolveRefund" ? "danger" : "primary"}
        isPending={acts.resolve.isPending}
        onConfirm={() => run("Contest resolved", () => acts.resolve.mutateAsync({ decision: dialog === "resolveRelease" ? "release" : "refund", note: resolveNote.trim() }))}
      >
        <TextArea label="Note" optional description="Kept in the audit record." value={resolveNote} onChange={setResolveNote} rows={2} maxLength={2000} />
        <p style={{ margin: 0 }}>This can't be undone from here.</p>
      </ConfirmDialog>

      <ConfirmDialog isOpen={dialog === "void"} onOpenChange={(o) => !o && setDialog(null)} title={coFunded && !isCoFunder(seat) ? "Cancel every hold?" : "Cancel the hold?"} summary={<p style={{ margin: 0 }}>{coFunded && !isCoFunder(seat) ? <>Release <strong>{money(m.funded_amount, a.currency)}</strong> of holds back to the funders' PayPal accounts.</> : <>Release the <strong>{coFunded ? money(myHeld, a.currency) : amt}</strong> hold back to your PayPal account.</>}</p>} confirmLabel={coFunded && !isCoFunder(seat) ? "Cancel every hold" : "Cancel the hold"} confirmVariant="danger" isPending={acts.void.isPending} onConfirm={() => run("Hold cancelled", () => acts.void.mutateAsync())}>
        <p>The tranche goes back to pending; it can be funded again later.</p>
      </ConfirmDialog>

      <ConfirmDialog
        isOpen={dialog === "shrink"}
        onOpenChange={(o) => !o && setDialog(null)}
        title="Propose the tranche at what was funded?"
        summary={<p style={{ margin: 0 }}><strong>{m.title}</strong> would become <strong>{money(m.funded_amount, a.currency)}</strong> instead of {amt}, and the agreement total drops by {money(gap, a.currency)}. {payee} has to accept; funders who held their share keep it as it is.</p>}
        confirmLabel="Send the proposal"
        isPending={acts.shortfall.isPending}
        onConfirm={() => run("Proposal sent", () => acts.shortfall.mutateAsync({ decision: "shrink", note: shortfallNote.trim() }), `${payee} decides from the tranche.`)}
      >
        <TextArea label="Note" optional description="Shown to the payee and kept in the audit record." value={shortfallNote} onChange={setShortfallNote} rows={2} maxLength={2000} />
      </ConfirmDialog>

      <ConfirmDialog
        isOpen={dialog === "cover"}
        onOpenChange={(o) => !o && setDialog(null)}
        title="Who covers the gap?"
        summary={<p style={{ margin: 0 }}>Reassign the unfunded <strong>{money(gap, a.currency)}</strong> of <strong>{m.title}</strong>. The funders who never held their share drop out of it; whoever covers it agrees by funding the extra.</p>}
        confirmLabel="Reassign the gap"
        isPending={acts.shortfall.isPending}
        confirmDisabled={!coverBy}
        onConfirm={() => run("Gap reassigned", () => acts.shortfall.mutateAsync({ decision: "cover", cover: [{ email: coverBy === "__lead" ? null : coverBy, amount: gap.toFixed(2) }], note: shortfallNote.trim() }), coverBy === "__lead" ? "Fund the extra from this tranche." : "They've been asked to fund it.")}
      >
        <Select label="Covered by" value={coverBy} onChange={setCoverBy} options={coverers} description="The lead, or a co-funder who has already held its share." />
        <TextArea label="Note" optional description="Sent with the request and kept in the audit record." value={shortfallNote} onChange={setShortfallNote} rows={2} maxLength={2000} />
      </ConfirmDialog>

      <ConfirmDialog
        isOpen={dialog === "acceptShrink" || dialog === "declineShrink"}
        onOpenChange={(o) => !o && setDialog(null)}
        title={dialog === "acceptShrink" ? "Accept the smaller tranche?" : "Decline the proposal?"}
        summary={<p style={{ margin: 0 }}>{dialog === "acceptShrink" ? <><strong>{m.title}</strong> becomes <strong>{money(m.proposed_amount, a.currency)}</strong> instead of {amt}; the criteria stay the same and the tranche is funded at once with the holds in place.</> : <>The tranche stays at {amt} and the funders' holds stay as they are; the lead funder can cancel them or ask the others to cover the gap.</>}</p>}
        confirmLabel={dialog === "acceptShrink" ? `Accept ${money(m.proposed_amount, a.currency)}` : "Decline"}
        confirmVariant={dialog === "acceptShrink" ? "primary" : "danger"}
        isPending={acts.answerShortfall.isPending}
        onConfirm={() => run(dialog === "acceptShrink" ? "Tranche shrunk" : "Proposal declined", () => acts.answerShortfall.mutateAsync({ accept: dialog === "acceptShrink", note: shortfallNote.trim() }))}
      >
        <TextArea label="Note" optional description="Sent to the lead funder and kept in the audit record." value={shortfallNote} onChange={setShortfallNote} rows={2} maxLength={2000} />
      </ConfirmDialog>

      <ConfirmDialog
        isOpen={dialog === "release"}
        onOpenChange={(o) => !o && setDialog(null)}
        title={a.settlement_mode === "invoice_on_verify" ? "Send the invoice?" : "Release this payment?"}
        summary={<p style={{ margin: 0 }}>{a.settlement_mode === "invoice_on_verify" ? <>PayPal sends you an invoice for <strong>{amt}</strong> from {payee}.</> : <>Capture the hold and pay <strong>{amt}</strong> to {payee}.</>}</p>}
        confirmLabel={a.settlement_mode === "invoice_on_verify" ? "Send invoice" : `Release ${amt}`}
        isPending={acts.release.isPending}
        onConfirm={() => run(a.settlement_mode === "invoice_on_verify" ? "Invoice sent" : "Payment released", () => acts.release.mutateAsync(), "PayPal confirms by webhook; the tranche settles when it does.")}
      >
        <p>This can't be undone from here.</p>
      </ConfirmDialog>

      <ConfirmDialog
        isOpen={dialog === "approve"}
        onOpenChange={(o) => !o && setDialog(null)}
        title="Approve this tranche?"
        summary={<p style={{ margin: 0 }}>Mark <strong>{m.title}</strong> as verified{verdict && !verdict.met ? ", even though the verifier found gaps" : verdict?.skeptic?.disagrees_on?.length ? `, even though the second, skeptical check disagreed on ${verdict.skeptic.disagrees_on.join(", ")}` : ""}.{a.auto_release_enabled && seat.role === "payer" ? ` With a live hold, ${amt} is then released.` : ""}</p>}
        confirmLabel="Approve"
        isPending={acts.review.isPending}
        onConfirm={() => run("Approved", () => acts.review.mutateAsync({ decision: "approve", note: note.trim() || null }))}
      >
        <TextArea label="Note" optional description="Kept in the audit record and shown to the payee." value={note} onChange={setNote} rows={3} maxLength={2000} />
      </ConfirmDialog>

      <ConfirmDialog
        isOpen={dialog === "releaseMet"}
        onOpenChange={(o) => !o && setDialog(null)}
        title="Pay the met share now?"
        summary={<p style={{ margin: 0 }}>Release <strong>{shareAmt ?? "the share"}</strong> of <strong>{m.title}</strong> to {payee} for {unpaidMet.join(", ")}. The rest stays on hold until the remaining criteria are met; {payee} is told what's missing.</p>}
        confirmLabel={shareAmt ? `Pay ${shareAmt}` : "Pay the share"}
        isPending={acts.review.isPending}
        onConfirm={() => run("Share released", () => acts.review.mutateAsync({ decision: "release_met", note: note.trim() || null }), "The remainder stays on hold for the criteria still to be met.")}
      >
        <TextArea label="Note" optional description="Kept in the audit record and shown to the payee." value={note} onChange={setNote} rows={3} maxLength={2000} />
      </ConfirmDialog>

      <ConfirmDialog
        isOpen={dialog === "reject"}
        onOpenChange={(o) => !o && setDialog(null)}
        title="Reject this evidence?"
        summary={<p style={{ margin: 0 }}>{payee} gets back each unmet criterion and what would satisfy it. They can resubmit or dispute.</p>}
        confirmLabel="Reject"
        confirmVariant="danger"
        isPending={acts.review.isPending}
        onConfirm={() => run("Rejected", () => acts.review.mutateAsync({ decision: "reject", note: note.trim() || null }), "The payee has been told what's missing.")}
      >
        <TextArea label="What should they send?" optional description="Shown to the payee alongside the verifier's list." value={note} onChange={setNote} rows={3} maxLength={2000} />
      </ConfirmDialog>

      <ConfirmDialog
        isOpen={dialog === "dispute"}
        onOpenChange={(o) => !o && setDialog(null)}
        title="Dispute the rejection?"
        summary={<p style={{ margin: 0 }}>The payer side has to review <strong>{m.title}</strong> again, with your reason and the full record.</p>}
        confirmLabel="Send dispute"
        isPending={acts.dispute.isPending}
        confirmDisabled={reason.trim().length < 10}
        onConfirm={() => run("Dispute sent", () => acts.dispute.mutateAsync(reason.trim()))}
      >
        <TextArea label="Why the rejection is wrong" description="At least 10 characters. Point to the evidence that meets each criterion." value={reason} onChange={setReason} rows={4} maxLength={4000} isRequired error={reason && reason.trim().length < 10 ? "Say a little more (at least 10 characters)" : null} />
      </ConfirmDialog>
    </Sheet>
  );
}

function idleText(m: MilestoneRead, seat: Seat): string {
  switch (m.status) {
    case "settled": return "Paid. Nothing left to do.";
    case "refunded": return "Refunded to the payer after a contest.";
    case "held": return seat.role === "payee" ? "Captured. It's paid to you when the dispute window closes." : "Captured and in the dispute window.";
    case "contested": return "Contested. Waiting for a resolution.";
    case "payout_failed": return "The payout failed; the payer can retry it.";
    case "settling": return "Waiting for PayPal to confirm the payment.";
    case "verifying":
    case "evidence_received": return "The verifier is reading the evidence.";
    case "verified": return seat.role === "payee" ? "Verified. The payer releases the payment next." : "Verified.";
    case "needs_review": return seat.role === "payee" ? "Waiting for the payer side to review." : "Waiting for a reviewer.";
    case "funded": return seat.role === "payee" ? "Funds are held. Submit evidence to get paid." : "Funded; waiting for evidence.";
    case "pending": return m.shares.length && Number(m.funded_amount) > 0 ? "Partly funded; waiting for the other funders to hold their share." : seat.role === "payee" ? "Submit evidence when the work is done." : "Nothing to do right now.";
    default: return seat.role === "payee" ? "Submit evidence when the work is done." : "Nothing to do right now.";
  }
}
