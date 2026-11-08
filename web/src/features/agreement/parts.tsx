/** Pieces of the agreement record shared by several pages. */
import type { AgreementRead, Criterion, LintIssue, MilestoneRead } from "@/api/types";
import { date, EVIDENCE_LABEL, measureText, ROLE_LABEL, VERIFICATION_LABEL } from "@/domain/format";
import { issuesFor } from "@/domain/lint-paths";
import { Glyph } from "@/design/components";
import s from "./Agreement.module.css";

/** One line per party, like the signature block at the end of a contract. */
export function SignatureBlock({ a }: { a: AgreementRead }) {
  const order = { payer: 0, payee: 1, reviewer: 2 } as const;
  const parties = [...a.parties].sort((x, y) => order[x.role] - order[y.role] || Number(y.lead) - Number(x.lead));
  const pending = a.pending_invitations ?? [];
  const coFunded = a.parties.filter((p) => p.role === "payer").length > 1;
  return (
    <ul className={s.signatures} aria-label="Signatures">
      {parties.map((p) => (
        <li key={p.id} className={s.sig}>
          <span className={`${s.sigIcon} ${p.confirmed_current ? s.sigOk : s.sigNo}`}><Glyph name={p.confirmed_current ? "signature" : "ring"} size={18} /></span>
          <div>
            <div className={s.sigLine}>
              <span className={`${s.sigName} ${p.confirmed_current ? "" : s.unsigned}`}>{p.confirmed_current ? p.display_name : `${p.display_name}: not signed`}</span>
              <span className={s.sigRole}>{p.role === "payer" && coFunded ? (p.lead ? "Lead funder" : "Co-funder") : ROLE_LABEL[p.role]}</span>
            </div>
            <span className="sr-only">{p.confirmed_current ? `${p.display_name} has signed the current criteria.` : `${p.display_name} hasn't signed the current criteria.`}</span>
            {(p.paypal_email || p.role === "payee") && (
              <span style={{ fontSize: "var(--text-sm)", color: "var(--text-secondary)" }}>
                {p.paypal_email && `PayPal ${p.paypal_email}`}
                {p.role === "payee" && p.paypal_connected !== null && p.paypal_connected !== undefined && (
                  <>{p.paypal_email && " · "}{p.paypal_connected ? "connected: paid directly" : "not connected: paid through the platform"}</>
                )}
              </span>
            )}
          </div>
        </li>
      ))}
      {pending.map((email) => (
        <li key={email} className={s.sig}>
          <span className={`${s.sigIcon} ${s.sigNo}`}><Glyph name="clock" size={18} /></span>
          <div>
            <div className={s.sigLine}>
              <span className={`${s.sigName} ${s.unsigned}`}>{email}: invited, hasn't joined</span>
              <span className={s.sigRole}>Invited</span>
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}

/** What has to be true for the agreement to go live, with what's done ticked. */
export function ActivationChecklist({ a }: { a: AgreementRead }) {
  const hasPayee = a.parties.some((p) => p.role === "payee");
  const pending = a.pending_invitations ?? [];
  const unsigned = a.parties.filter((p) => !p.confirmed_current);
  const items = [
    { ok: a.lint.ok, text: a.lint.ok ? "Criteria lint is clear" : `Fix ${a.lint.issues?.filter((i) => i.level === "error").length} criteria errors (lint blocks signing)` },
    { ok: hasPayee, text: hasPayee ? "The payee has joined" : "The payee hasn't accepted the invitation yet" },
    { ok: pending.length === 0, text: pending.length === 0 ? "Nobody invited is still pending" : `Waiting for ${pending.join(", ")} to join` },
    { ok: unsigned.length === 0 && a.parties.length > 0, text: unsigned.length === 0 ? "Every party signed the same criteria" : `Waiting for ${unsigned.map((p) => p.display_name).join(", ")} to sign` },
  ];
  return (
    <ul className={s.checklist} aria-label="What's needed to go live">
      {items.map((i, n) => (
        <li key={n}>
          <Glyph name={i.ok ? "check" : "ring"} size={16} style={{ color: i.ok ? "var(--text-success)" : "var(--text-secondary)", marginTop: "0.2em" }} />
          <span>{i.text}<span className="sr-only">{i.ok ? " (done)" : " (to do)"}</span></span>
        </li>
      ))}
    </ul>
  );
}

/** A tranche's criteria, read-only, with any lint notes. */
export function CriteriaList({ m, index, issues = [] }: { m: MilestoneRead; index: number; issues?: LintIssue[] }) {
  return (
    <ol className={s.critList} aria-label={`Criteria for ${m.title}`}>
      {m.criteria.map((c: Criterion, j) => {
        const notes = issuesFor(issues, index, j);
        return (
          <li key={c.id ?? j} className={s.crit}>
            <span className={s.critNo}>{c.id ?? `c${j + 1}`}</span>
            <div style={{ display: "grid", gap: "var(--space-1)" }}>
              <p className={s.critStatement}>{c.statement}</p>
              <p className={s.critMeta}>
                <span>Measure: <strong>{c.measure.metric} {measureText(c.measure)}</strong></span>
                <span>Evidence: <strong>{c.evidence_required.map((e) => EVIDENCE_LABEL[e]).join(", ")}</strong></span>
                <span>Decided by: <strong>{c.verification === "check" ? `check · ${c.check_id}` : VERIFICATION_LABEL[c.verification ?? "model"]}</strong></span>
                {c.due_by && <span>By <strong>{date(c.due_by)}</strong></span>}
              </p>
              <p className={s.critMeta}>{c.evidence_description}</p>
              {notes.map((n, k) => (
                <p key={k} className={s.critMeta} style={{ color: n.level === "error" ? "var(--danger)" : undefined }}>
                  <Glyph name="warning" size={12} style={{ display: "inline", marginRight: 4 }} />
                  {n.level === "error" ? "Must fix: " : "Worth a look: "}
                  {n.message}
                </p>
              ))}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
