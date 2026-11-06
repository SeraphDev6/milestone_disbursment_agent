import { useMemo } from "react";
import { useParams } from "react-router";
import { hasDemo, useEvidence, useGitHubRepos, useHandovers, useHealth, useMilestoneAudit, usePayerView, useRetryHandover, useTrancheActions, useVerdict } from "@/api/queries";
import { useAgreementCtx, useOrgCtx, paths } from "@/app/context";
import { useTitle } from "@/app/RouteAnnouncer";
import { useSessions } from "@/auth/session";
import type { ActorNames } from "@/domain/actors";
import { date, dateTime, money } from "@/domain/format";
import { canOnTranche } from "@/domain/permissions";
import { IN_FLIGHT, MILESTONE_STAMPS } from "@/domain/status";
import { Busy, Callout, Crumbs, layout, PageHeader, Sheet, Stamp, Tabs } from "@/design/components";
import { ACCESS_ACTIONS, AccessLog, DisputePack, PayerSees } from "./Access";
import { Actions } from "./Actions";
import { AddEvidence, EvidenceList } from "./Evidence";
import { HandedOver, latestHandovers } from "./Handover";
import { StateTrack } from "./StateTrack";
import { Timeline } from "./Timeline";
import { Results, VerdictSummary } from "./Verdict";

export function Tranche() {
  const { milestoneId = "" } = useParams();
  const { agreement: a, seat, base } = useAgreementCtx();
  const org = useOrgCtx();
  const health = useHealth();
  const demo = hasDemo(health.data);
  const { sessions } = useSessions();
  const index = a.milestones.findIndex((x) => x.id === milestoneId);
  const m = a.milestones[index];
  useTitle(m ? `Tranche ${index + 1}: ${m.title}` : "Tranche", a.title);

  const status = m?.status;
  const evidence = useEvidence(milestoneId, org.orgId, status);
  const verdict = useVerdict(milestoneId, org.orgId, status);
  const audit = useMilestoneAudit(milestoneId, org.orgId, status);
  const acts = useTrancheActions(a.id, milestoneId, org.orgId);
  const hasCode = (evidence.data ?? []).some((e) => /^[0-9a-f]{40}$/i.test(e.pinned_ref));
  const repos = useGitHubRepos(org.orgId, hasCode);
  const handovers = useHandovers(milestoneId, org.orgId, !!m?.revealed_at && hasCode);
  const retryHandover = useRetryHandover(milestoneId, org.orgId);
  const onCapture = a.deliverable_release === "on_capture";
  const comparePayer = onCapture && seat.role !== "payer";
  const payerView = usePayerView(milestoneId, org.orgId, comparePayer && (evidence.data?.length ?? 0) > 0, status);

  const names: ActorNames = useMemo(
    () => ({
      orgs: Object.fromEntries(a.parties.map((p) => [p.org_id, p.display_name])),
      users: Object.fromEntries(sessions.map((x) => [x.user.id, x.user.name || x.user.email])),
    }),
    [a.parties, sessions],
  );

  if (!m) return <Callout tone="error" title="No such tranche on this agreement" />;

  const events = audit.data ?? [];
  const lastReview = [...events].reverse().find((e) => e.action === "review_approved" || e.action === "review_rejected");
  const rejected = lastReview?.action === "review_rejected";
  const rejectNote = rejected ? ((lastReview?.detail?.note as string | undefined) ?? null) : null;
  const v = verdict.data ?? null;
  const submit = canOnTranche(seat, "submit_evidence", { status: m.status, mode: a.settlement_mode, agreementActive: a.status === "active", hasHold: !!m.paypal_authorization_id });
  const isPayee = seat.role === "payee";
  const working = IN_FLIGHT.has(m.status);
  const payeeName = a.parties.find((p) => p.role === "payee")?.display_name ?? "the payee";
  const contestEvent = [...events].reverse().find((e) => e.action === "contested");
  const payerName = a.parties.find((p) => p.role === "payer")?.display_name ?? "the payer";
  const fullList = evidence.isPending ? <Busy label="Loading evidence" /> : <EvidenceList evidence={evidence.data ?? []} orgId={org.orgId} handovers={latestHandovers(events)} checkHandover={acts.handover} repos={(repos.data ?? []).map((r) => r.repo)} />;

  return (
    <div className={layout.stackLg}>
      <PageHeader
        crumbs={<Crumbs items={[{ label: org.orgName, href: paths.org(org.orgId) }, { label: a.title, href: base }, { label: `Tranche ${index + 1}` }]} />}
        eyebrow={`Tranche ${index + 1} of ${a.milestones.length}`}
        title={m.title}
        stamp={<Stamp spec={MILESTONE_STAMPS[m.status]} size="lg" context="Tranche status" />}
        meta={
          <>
            <span className="figures">{money(m.amount, a.currency)}</span>
            {m.due_date && <span>Due {date(m.due_date)}</span>}
            {m.funded_at && !m.captured_at && <span>Hold placed {date(m.funded_at)}</span>}
            {m.captured_at && <span>Captured {date(m.captured_at)}</span>}
            {m.amount_released && Number(m.amount_released) > 0 && Number(m.amount_released) < Number(m.amount) && <span>Released so far {money(m.amount_released, a.currency)} for {(m.criteria_paid ?? []).join(", ")}</span>}
            {a.deliverable_release === "on_capture" && <span>{m.revealed_at ? `Work opened to the payer ${date(m.revealed_at)}` : "Work opens to the payer once paid"}</span>}
            {m.payout_route && <span>{m.payout_route === "direct" ? `Paid straight to ${payeeName}'s PayPal` : `Paid through the platform to ${payeeName}`}</span>}
          </>
        }
      />
      <StateTrack status={m.status} mode={a.settlement_mode} />
      <div role="status" aria-live="polite" className={working ? undefined : "sr-only"}>
        {working && <Callout tone="info" title={MILESTONE_STAMPS[m.status].label}>{MILESTONE_STAMPS[m.status].meaning} This page updates on its own.</Callout>}
      </div>
      {m.status === "held" && m.release_due_at && (
        <Callout tone="info" title={`In the dispute window until ${dateTime(m.release_due_at)}`}>
          <p>The money is captured from the payer. It's paid to {payeeName} when the window closes, unless the payer or a reviewer contests it first. The payer can also pay now.</p>
        </Callout>
      )}
      {m.status === "contested" && (
        <Callout tone="warning" title="This payment is contested">
          {typeof contestEvent?.detail?.reason === "string" && <p>“{contestEvent.detail.reason}”</p>}
          <p>The payout is paused. The payer can withdraw the contest and pay, {payeeName} can accept it and refund the payer, or a reviewer can decide. Unresolved, PayPal pays {payeeName} 28 days after capture.</p>
        </Callout>
      )}
      {m.status === "payout_failed" && (
        <Callout tone="error" title="The payout to the payee failed">
          <p>The money is still captured. The payer can retry the payout{m.payout_route === "platform" ? `; if ${payeeName} has no PayPal account for its email, connecting PayPal fixes this` : ""}.</p>
        </Callout>
      )}
      {rejected && ["pending", "funded"].includes(m.status) && (
        <Callout tone="warning" title="The last submission was rejected">
          {rejectNote && <p>“{rejectNote}”</p>}
          <p>{isPayee ? "Each unmet criterion below says what would satisfy it. Resubmit, or dispute if you think the rejection is wrong." : "The payee has been told what's missing."}</p>
        </Callout>
      )}

      <div className={layout.split}>
        <div className={layout.stackLg}>
          <Sheet title={v ? "Verdict" : "Criteria"}>
            {verdict.isPending ? (
              <Busy label="Loading verdict" />
            ) : (
              <div className={layout.stack}>
                {v && <VerdictSummary v={v} threshold={a.auto_release_threshold} />}
                {!v && <p style={{ color: "var(--text-secondary)" }}>No verdict yet. Each criterion below is judged once evidence is submitted.</p>}
                <Results criteria={m.criteria} results={v?.criteria_results} evidence={evidence.data ?? []} />
              </div>
            )}
          </Sheet>
          {(handovers.data?.length ?? 0) > 0 && (
            <Sheet title="Handover">
              <HandedOver handovers={handovers.data ?? []} orgId={org.orgId} retry={retryHandover} />
            </Sheet>
          )}
          <Sheet title="Evidence on file">
            {comparePayer && (evidence.data?.length ?? 0) > 0 ? (
              <Tabs
                label="Whose view of the evidence"
                defaultSelected="full"
                tabs={[
                  { id: "full", label: "Full evidence", content: fullList },
                  { id: "payer", label: `What ${payerName} sees`, content: <PayerSees view={payerView.data} criteria={m.criteria} orgId={org.orgId} payerName={payerName} /> },
                ]}
              />
            ) : (
              fullList
            )}
          </Sheet>
          {isPayee && (
            <AddEvidence
              criteria={m.criteria}
              demo={demo}
              canSubmit={submit.ok}
              submitReason={submit.ok ? undefined : submit.reason}
              onSubmitted={() => document.getElementById("main")?.scrollIntoView({ behavior: "smooth" })}
              actions={acts}
              minVisibility={a.deliverable_release === "on_capture" ? a.min_evidence_visibility : undefined}
            />
          )}
        </div>
        <div className={layout.stack}>
          <Actions a={a} m={m} seat={seat} verdict={v} acts={acts} rejected={rejected} />
          <Sheet title="History" titleLevel={2} tight>
            {audit.isPending ? <Busy label="Loading history" /> : <Timeline events={events.filter((e) => !ACCESS_ACTIONS.has(e.action))} names={names} />}
          </Sheet>
          {onCapture && (
            <Sheet title="Who opened the evidence" titleLevel={2} tight>
              {audit.isPending ? <Busy label="Loading" /> : <AccessLog events={events} names={names} />}
            </Sheet>
          )}
          {m.revealed_at && seat.role !== "payer" && (
            <Sheet title="Dispute pack" titleLevel={2} tight>
              <DisputePack milestoneId={m.id} orgId={org.orgId} />
            </Sheet>
          )}
        </div>
      </div>
    </div>
  );
}
