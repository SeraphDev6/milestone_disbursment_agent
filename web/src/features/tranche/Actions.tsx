import { useState } from "react";
import { API_BASE } from "@/api/client";
import { useNow } from "@/app/useNow";
import type { AgreementRead, MilestoneRead, VerdictRead } from "@/api/types";
import { errorMessage } from "@/api/errors";
import type { useTrancheActions } from "@/api/queries";
import { dateTime, money } from "@/domain/format";
import { approveBlockedByReviewerRule, canOnTranche, type Seat, type TrancheAction } from "@/domain/permissions";
import { Button, ButtonLink, Callout, ConfirmDialog, PermissionNote, Sheet, TextArea, toast } from "@/design/components";
import s from "./Tranche.module.css";

type Acts = ReturnType<typeof useTrancheActions>;

/**
 * Everything this seat can do to this tranche now. Actions that aren't available stay listed with the
 * reason, so nobody wonders where a button went. Every money-moving action is confirmed in a dialog.
 */
export function Actions({ a, m, seat, verdict, acts, rejected }: { a: AgreementRead; m: MilestoneRead; seat: Seat; verdict: VerdictRead | null; acts: Acts; rejected: boolean }) {
  const [dialog, setDialog] = useState<null | "fund" | "void" | "release" | "approve" | "reject" | "dispute" | "payNow" | "contest" | "resolveRelease" | "resolveRefund">(null);
  const [contestReason, setContestReason] = useState("");
  const [resolveNote, setResolveNote] = useState("");
  const [note, setNote] = useState("");
  const [reason, setReason] = useState("");
  const [fundUrl, setFundUrl] = useState<string | null>(null);
  const now = useNow();
  const windowClosed = !!m.release_due_at && Date.parse(m.release_due_at) <= now;
  const ctx = { status: m.status, mode: a.settlement_mode, agreementActive: a.status === "active", hasHold: !!m.paypal_authorization_id, rejected, windowClosed };
  const check = (x: TrancheAction) => canOnTranche(seat, x, ctx);
  const payee = a.parties.find((p) => p.role === "payee")?.display_name ?? "the payee";
  const amt = money(m.amount, a.currency);
  const reviewerOnly = approveBlockedByReviewerRule(seat, a.require_reviewer_approval);

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
    add("fund", <Button variant="primary" icon="ledger" isDisabled={!check("fund").ok} onPress={() => setDialog("fund")}>Fund {amt}</Button>, m.status === "pending" || m.status === "overdue");
  if (seat.role === "payer") add("void", <Button icon="close" isDisabled={!check("void").ok} onPress={() => setDialog("void")}>Cancel the hold</Button>, m.status === "funded");
  if (seat.role === "payer" || seat.role === "reviewer") {
    const r = check("review");
    add("review", (
      <div className={s.action}>
        <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap" }}>
          <Button variant="primary" icon="check" isDisabled={!r.ok || !!reviewerOnly} onPress={() => setDialog("approve")}>Approve</Button>
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
        title="Fund this tranche?"
        summary={<p style={{ margin: 0 }}>Place a PayPal hold of <strong>{amt}</strong> for {payee}.</p>}
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

      <ConfirmDialog isOpen={dialog === "void"} onOpenChange={(o) => !o && setDialog(null)} title="Cancel the hold?" summary={<p style={{ margin: 0 }}>Release the <strong>{amt}</strong> hold back to your PayPal account.</p>} confirmLabel="Cancel the hold" confirmVariant="danger" isPending={acts.void.isPending} onConfirm={() => run("Hold cancelled", () => acts.void.mutateAsync())}>
        <p>The tranche goes back to pending; you can fund it again later.</p>
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
        summary={<p style={{ margin: 0 }}>Mark <strong>{m.title}</strong> as verified{verdict && !verdict.met ? ", even though the verifier found gaps" : ""}.{a.auto_release_enabled && seat.role === "payer" ? ` With a live hold, ${amt} is then released.` : ""}</p>}
        confirmLabel="Approve"
        isPending={acts.review.isPending}
        onConfirm={() => run("Approved", () => acts.review.mutateAsync({ decision: "approve", note: note.trim() || null }))}
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
    default: return seat.role === "payee" ? "Submit evidence when the work is done." : "Nothing to do right now.";
  }
}
