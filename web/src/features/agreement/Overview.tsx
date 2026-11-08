import { Link } from "react-router";
import type { AgreementRead, MilestoneRead } from "@/api/types";
import { useAgreementCtx, useOrgCtx, paths } from "@/app/context";
import { useTitle } from "@/app/RouteAnnouncer";
import { date } from "@/domain/format";
import { can, type Seat } from "@/domain/permissions";
import { MILESTONE_STAMPS } from "@/domain/status";
import { Amount, ButtonLink, Callout, layout, Sheet, Stamp, Totals } from "@/design/components";
import { ActivationChecklist, SignatureBlock } from "./parts";
import s from "./Agreement.module.css";

export function Overview() {
  const { agreement: a, seat, base } = useAgreementCtx();
  const org = useOrgCtx();
  useTitle(a.title);
  const f = a.funding;
  return (
    <div className={layout.split}>
      <div className={layout.stackLg}>
        <NextStep a={a} seat={seat} base={base} />
        <Sheet title="Tranches">
          <ol className={s.ledger} aria-label="Tranches in payment order">
            {a.milestones.map((m, i) => (
              <TrancheRow key={m.id} m={m} i={i} currency={a.currency} to={paths.tranche(org.orgId, a.id, m.id)} />
            ))}
          </ol>
          <div className={s.ledgerFoot} aria-label="Agreement total">
            <span />
            <span>Total</span>
            <span className={s.rowAmount}><Amount value={a.total_amount} currency={a.currency} /></span>
          </div>
        </Sheet>
      </div>
      <div className={layout.stack}>
        <Sheet title="Money" titleLevel={2} tight>
          <Totals
            label="Funding"
            currency={a.currency}
            lines={[
              { label: "Paid to payee", value: f.released },
              ...(a.settlement_mode === "escrow_release"
                ? [
                    { label: "In the dispute window", value: f.in_dispute_window, hint: "Captured from the payer; paid when the window closes" },
                    { label: "Held in PayPal", value: f.held, hint: "Authorized on the payer's account, not yet captured" },
                  ]
                : [{ label: "Invoiced, unpaid", value: f.invoiced }]),
              ...(Number(f.refunded) > 0 ? [{ label: "Refunded to payer", value: f.refunded }] : []),
              { label: "Not yet funded", value: f.unfunded },
            ]}
            total={{ label: "Agreement total", value: a.total_amount }}
          />
        </Sheet>
        <Sheet title="Signatures" titleLevel={2} tight actions={<ButtonLink size="sm" href={`${base}/criteria`}>Review criteria</ButtonLink>}>
          <SignatureBlock a={a} />
        </Sheet>
        {!a.locked && (
          <Sheet title="To go live" titleLevel={2} tight>
            <ActivationChecklist a={a} />
          </Sheet>
        )}
      </div>
    </div>
  );
}

function TrancheRow({ m, i, currency, to }: { m: MilestoneRead; i: number; currency: string; to: string }) {
  return (
    <li className={s.ledgerRow}>
      <span className={s.no} aria-hidden="true">{i + 1}</span>
      <span className={s.rowTitle}>
        <Link to={to}><span className="sr-only">Tranche {i + 1}: </span>{m.title}</Link>
        <span className={s.rowMeta}>{m.criteria.length} criteri{m.criteria.length === 1 ? "on" : "a"}</span>
      </span>
      <span className={s.rowDue}>{m.due_date ? `Due ${date(m.due_date)}` : "No due date"}</span>
      <span className={s.rowAmount}><Amount value={m.amount} currency={currency} /></span>
      <span className={s.rowStamp}><Stamp spec={MILESTONE_STAMPS[m.status]} context="Status" /></span>
    </li>
  );
}

/** The single most useful thing this person can do now, worked out from status and seat. */
function NextStep({ a, seat, base }: { a: AgreementRead; seat: Seat; base: string }) {
  const me = a.parties.find((p) => p.role === seat.role && p.display_name === seat.orgName) ?? a.parties.find((p) => p.role === seat.role);
  if (a.status === "draft" || a.status === "awaiting_confirmation") {
    if (me && !me.confirmed_current && can(seat, "confirm_criteria").ok)
      return (
        <Callout tone="warning" title="Your signature is needed" actions={<ButtonLink variant="primary" href={`${base}/criteria`} iconEnd="arrow">Review and sign the criteria</ButtonLink>}>
          <p>Read every criterion. Signing commits {seat.orgName} to these exact terms; the seal changes if anything is edited.</p>
        </Callout>
      );
    return (
      <Callout tone="info" title="Waiting for everyone to sign">
        <ActivationChecklist a={a} />
      </Callout>
    );
  }
  if (a.status !== "active") return null;
  const by = (st: MilestoneRead["status"][]) => a.milestones.find((m) => st.includes(m.status));
  const payeeParty = a.parties.find((p) => p.role === "payee");
  if (seat.role === "payee" && payeeParty?.paypal_connected === false && a.settlement_mode === "escrow_release")
    return (
      <Callout tone="warning" title="Connect PayPal to be paid directly" actions={<ButtonLink variant="primary" href={`/o/${payeeParty.org_id}/settings#paypal`} iconEnd="arrow">Connect PayPal</ButtonLink>}>
        <p>Until {payeeParty.display_name} connects its PayPal account, tranches are paid through the platform with a PayPal Payout to {payeeParty.paypal_email ?? "your PayPal email"}: slower, and with two PayPal fees instead of one. Tranches funded after you connect go straight to you.</p>
      </Callout>
    );
  if (seat.role === "payer" || seat.role === "reviewer") {
    const contested = by(["contested"]);
    if (contested) return <Callout tone="warning" title={`${contested.title} is contested`} actions={<ButtonLink variant="primary" href={`${base}/m/${contested.id}`} iconEnd="arrow">Resolve the contest</ButtonLink>}><p>Its payout is paused until someone resolves it.</p></Callout>;
  }
  if (seat.role === "payer") {
    const failed = by(["payout_failed"]);
    if (failed) return <Callout tone="error" title={`The payout for ${failed.title} failed`} actions={<ButtonLink variant="primary" href={`${base}/m/${failed.id}`} iconEnd="arrow">Retry the payout</ButtonLink>} />;
    const review = by(["needs_review", "disputed"]);
    if (review) return <Callout tone="warning" title={`${review.title} is waiting for a decision`} actions={<ButtonLink variant="primary" href={`${base}/m/${review.id}`} iconEnd="arrow">Review the verdict</ButtonLink>}><p>The verifier couldn't settle it on its own. Read its rationale and the evidence, then approve or reject.</p></Callout>;
    const verified = by(["verified", "settlement_failed"]);
    if (verified) return <Callout tone="success" title={`${verified.title} is ready to release`} actions={<ButtonLink variant="primary" href={`${base}/m/${verified.id}`} iconEnd="arrow">Release payment</ButtonLink>}><p>Every criterion is met and cited.</p></Callout>;
    const lapsed = a.milestones.find((m) => m.funding_lapsed && !m.proposed_amount);
    if (lapsed && seat.lead !== false) return <Callout tone="warning" title={`${lapsed.title} is only partly funded`} actions={<ButtonLink variant="primary" href={`${base}/m/${lapsed.id}`} iconEnd="arrow">Decide the shortfall</ButtonLink>}><p>The funding window has passed and some funders haven't held their share. You can cancel the holds, propose the funded amount to the payee, or ask the others to cover the gap.</p></Callout>;
    const unfunded = a.settlement_mode === "escrow_release" && a.milestones.find((m) => ["pending", "overdue"].includes(m.status) && Number(m.funded_amount) < Number(m.amount) && !m.holds.some((x) => x.party_id === me?.id && x.paypal_authorization_id));
    if (unfunded) return <Callout tone="info" title={`Fund ${unfunded.title}`} actions={<ButtonLink variant="primary" href={`${base}/m/${unfunded.id}`} iconEnd="arrow">{unfunded.shares.length ? "Fund your share" : "Fund this tranche"}</ButtonLink>}><p>Funding places a PayPal hold on your account. It's captured only when the tranche verifies, and you can cancel it before evidence arrives.{unfunded.shares.length ? " Each funder holds its own share; the tranche is funded once every share is held." : ""}</p></Callout>;
  }
  if (seat.role === "reviewer") {
    const review = by(["needs_review", "disputed"]);
    if (review) return <Callout tone="warning" title={`${review.title} needs a reviewer`} actions={<ButtonLink variant="primary" href={`${base}/m/${review.id}`} iconEnd="arrow">Review the verdict</ButtonLink>} />;
  }
  if (seat.role === "payee") {
    const contested = by(["contested"]);
    if (contested) return <Callout tone="warning" title={`${contested.title} is contested`} actions={<ButtonLink variant="primary" href={`${base}/m/${contested.id}`} iconEnd="arrow">See the contest</ButtonLink>}><p>The payer side objected during the dispute window; the payout is paused.</p></Callout>;
    const open = by(["funded", "pending"]);
    if (open) return <Callout tone="info" title={`Next: evidence for ${open.title}`} actions={<ButtonLink variant="primary" href={`${base}/m/${open.id}`} iconEnd="arrow">Pre-check and submit evidence</ButtonLink>}><p>Pre-check first: it tells you exactly what's missing without recording anything.</p></Callout>;
  }
  return null;
}
