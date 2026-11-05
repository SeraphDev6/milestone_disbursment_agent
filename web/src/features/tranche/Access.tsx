import { download } from "@/api/client";
import type { AuditEventRead, Criterion, PayerView } from "@/api/types";
import { actorText, type ActorNames } from "@/domain/actors";
import { dateTime } from "@/domain/format";
import { errorMessage } from "@/api/errors";
import { Busy, Button, layout, toast } from "@/design/components";
import { EvidenceList } from "./Evidence";
import { Results } from "./Verdict";
import s from "./Tranche.module.css";

/** Reads of the tranche's evidence: kept out of the history, listed here. */
export const ACCESS_ACTIONS = new Set(["evidence_viewed", "evidence_downloaded", "deliverable_downloaded"]);

const SIDES: Record<string, string> = { payer: "Payer side", payee: "Payee side", reviewer: "Reviewer" };

export function accessText(e: AuditEventRead): string {
  const d = e.detail ?? {};
  if (e.action === "evidence_viewed") {
    const n = Array.isArray(d.evidence_ids) ? d.evidence_ids.length : 0;
    return `Viewed ${n} item${n === 1 ? "" : "s"}, ${d.view === "filtered" ? "as withheld (verdict and hashes only)" : "in full"}`;
  }
  return d.handover_id ? "Downloaded the handed-over source archive" : "Downloaded the original file";
}

/** Who opened the evidence, and how much of it they saw. */
export function AccessLog({ events, names }: { events: AuditEventRead[]; names: ActorNames }) {
  const rows = events.filter((e) => ACCESS_ACTIONS.has(e.action));
  if (!rows.length) return <p style={{ color: "var(--text-secondary)" }}>No one has opened the evidence yet.</p>;
  return (
    <ol className={s.timeline} aria-label="Who opened the evidence">
      {rows.map((e, i) => {
        const side = String(e.detail?.side ?? (e.action === "deliverable_downloaded" ? "payer" : ""));
        return (
          <li key={i} className={s.tlItem}>
            <time className={s.tlWhen} dateTime={e.at}>{dateTime(e.at)}</time>
            <span className={s.tlWhat}>{accessText(e)}</span>
            <span className={s.tlWho}>{actorText(e.actor, names)}{SIDES[side] ? ` · ${SIDES[side]}` : ""}</span>
          </li>
        );
      })}
    </ol>
  );
}

/** Exactly what the payer side gets of this tranche, for the payee and reviewers to compare with the full view. */
export function PayerSees({ view, criteria, orgId, payerName }: { view: PayerView | undefined; criteria: Criterion[]; orgId: string; payerName: string }) {
  if (!view) return <Busy label="Loading the payer's view" />;
  return (
    <div className={layout.stack}>
      <p style={{ color: "var(--text-secondary)" }}>
        {view.withheld ? `This is all ${payerName} sees until the tranche is paid: the results and the hashes, filtered to each item's visibility.` : `The work is open to ${payerName}; they see everything.`}
      </p>
      <Results criteria={criteria} results={view.verdict?.criteria_results} evidence={view.evidence} />
      <EvidenceList evidence={view.evidence} orgId={orgId} />
    </div>
  );
}

/** The payee's record for answering a chargeback on work already opened to the payer. */
export function DisputePack({ milestoneId, orgId }: { milestoneId: string; orgId: string }) {
  return (
    <div className={layout.stack}>
      <p>If the payer disputes the payment with PayPal, answer with this: the criteria both sides signed, the hashes of what was verified, the verdict with its citations, when the work was opened to the payer and every time they opened or downloaded it.</p>
      <div>
        <Button icon="download" onPress={() => download(`/milestones/${milestoneId}/dispute-pack?format=pdf`, `dispute-pack-${milestoneId}.pdf`, orgId).catch((e) => toast("Download failed", { body: errorMessage(e), tone: "error" }))}>Download dispute pack</Button>
      </div>
    </div>
  );
}
