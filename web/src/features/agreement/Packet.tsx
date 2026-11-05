import { useMemo } from "react";
import { usePacket } from "@/api/queries";
import { download } from "@/api/client";
import { errorMessage } from "@/api/errors";
import { useAgreementCtx, useOrgCtx } from "@/app/context";
import { useTitle } from "@/app/RouteAnnouncer";
import { useSessions } from "@/auth/session";
import { actionText, actorText } from "@/domain/actors";
import { date, dateTime, measureText, money, percent, ROLE_LABEL, shortHash, VISIBILITY_LABEL } from "@/domain/format";
import { can } from "@/domain/permissions";
import { Busy, Button, Callout, layout, PermissionNote, toast } from "@/design/components";
import s from "./Packet.module.css";

/**
 * The compliance packet as a document: what was agreed, who signed, what evidence came in, how each
 * verdict was reached and cited, who reviewed, and every payment. Prints cleanly; the PDF is the
 * server's rendering of the same record.
 */
export function Packet() {
  const { agreement: a, seat } = useAgreementCtx();
  const org = useOrgCtx();
  useTitle("Compliance packet", a.title);
  const allowed = can(seat, "compliance_packet");
  const q = usePacket(a.id, org.orgId, allowed.ok);
  const { sessions } = useSessions();
  const names = useMemo(() => ({ orgs: Object.fromEntries(a.parties.map((p) => [p.org_id, p.display_name])), users: Object.fromEntries(sessions.map((x) => [x.user.id, x.user.email])) }), [a.parties, sessions]);

  if (!allowed.ok) return <PermissionNote>{allowed.reason}</PermissionNote>;
  if (q.isPending) return <Busy label="Assembling the packet" lines={8} />;
  if (q.isError) return <Callout tone="error" title="Couldn't build the packet" live>{q.error.message}</Callout>;
  const p = q.data;
  const cur = p.agreement.currency;

  return (
    <div className={layout.stack}>
      <div className={`${layout.row} no-print`}>
        <Button variant="primary" icon="download" onPress={() => download(`/agreements/${a.id}/compliance-packet?format=pdf`, `compliance-${a.id}.pdf`, org.orgId).catch((e) => toast("Download failed", { body: errorMessage(e), tone: "error" }))}>Download PDF</Button>
        <Button icon="file" onPress={() => window.print()}>Print</Button>
        <Button
          variant="quiet"
          icon="download"
          onPress={() => {
            const url = URL.createObjectURL(new Blob([JSON.stringify(p, null, 2)], { type: "application/json" }));
            const el = document.createElement("a");
            el.href = url;
            el.download = `compliance-${a.id}.json`;
            el.click();
            setTimeout(() => URL.revokeObjectURL(url), 10_000);
          }}
        >
          JSON
        </Button>
      </div>
      <article className={s.doc} aria-labelledby="packet-title">
        <header className={s.head}>
          <p className={s.kicker}>Compliance packet · generated {dateTime(p.generated_at)}</p>
          <h2 id="packet-title" className={s.title}>{p.agreement.title}</h2>
          <dl className={s.facts}>
            <div><dt>Total</dt><dd>{money(p.agreement.total_amount, cur)}</dd></div>
            <div><dt>Released</dt><dd>{money(p.agreement.released, cur)}</dd></div>
            <div><dt>Settlement</dt><dd>{p.agreement.settlement_mode === "escrow_release" ? "Escrow release" : "Invoice on verify"}</dd></div>
            <div><dt>Dispute window</dt><dd>{p.agreement.dispute_window_days ? `${p.agreement.dispute_window_days} days` : "None"}</dd></div>
            <div><dt>Evidence visible</dt><dd>{p.agreement.deliverable_release === "on_capture" ? `Once paid (before that, at least: ${VISIBILITY_LABEL[(p.agreement.min_evidence_visibility ?? "hash_only") as keyof typeof VISIBILITY_LABEL]?.label.toLowerCase()})` : "On submit"}</dd></div>
            <div><dt>Auto-release</dt><dd>{p.agreement.auto_release_enabled ? `On at ${percent(p.agreement.auto_release_threshold)}` : "Off"}</dd></div>
            <div><dt>Criteria hash</dt><dd className="figures">{p.agreement.criteria_hash ?? "not locked"}</dd></div>
          </dl>
        </header>

        <section aria-labelledby="pk-parties">
          <h3 id="pk-parties" className={s.h}>1. Parties and signatures</h3>
          <table className={s.table}>
            <thead><tr><th scope="col">Party</th><th scope="col">Role</th><th scope="col">PayPal</th><th scope="col">Signed</th><th scope="col">Hash signed</th></tr></thead>
            <tbody>
              {p.parties.map((x, i) => (
                <tr key={i}><td>{x.name}</td><td>{ROLE_LABEL[x.role as keyof typeof ROLE_LABEL] ?? x.role}</td><td>{x.paypal_email ?? "—"}</td><td>{x.confirmed_at ? dateTime(x.confirmed_at) : "Not signed"}</td><td className="figures">{x.confirmed_criteria_hash ? shortHash(x.confirmed_criteria_hash, 8, 4) : "—"}</td></tr>
              ))}
            </tbody>
          </table>
        </section>

        {p.milestones.map((m, i) => (
          <section key={m.id} aria-labelledby={`pk-m-${m.id}`} className={s.tranche}>
            <h3 id={`pk-m-${m.id}`} className={s.h}>{i + 2}. Tranche {i + 1}: {m.title}</h3>
            <p className={s.sub}>{money(m.amount, cur)} · {m.due_date ? `due ${date(m.due_date)}` : "no due date"} · status {m.status.replace(/_/g, " ")}{m.payout_route && ` · paid ${m.payout_route === "direct" ? "directly to the payee's PayPal" : "through the platform"}`}</p>
            <h4 className={s.h4}>Criteria</h4>
            <ol className={s.list}>
              {m.criteria.map((c, j) => (
                <li key={j}><strong>{c.statement}</strong> <span className={s.muted}>({c.measure.metric} {measureText({ comparator: c.measure.comparator as never, target: c.measure.target as string, unit: c.measure.unit })}; {c.verification === "check" ? `check ${c.check_id}` : c.verification})</span></li>
              ))}
            </ol>
            <h4 className={s.h4}>Evidence</h4>
            {m.evidence_withheld && <p className={s.muted}>The payee's evidence opens to you once this tranche is paid. Until then this packet shows the verdict and each item's fingerprint.</p>}
            {m.evidence.length ? (
              <table className={s.table}>
                <thead><tr><th scope="col">#</th><th scope="col">Title</th><th scope="col">Source</th><th scope="col">Pinned version</th><th scope="col">Received</th></tr></thead>
                <tbody>{m.evidence.map((e, k) => <tr key={e.id}><td>e{k + 1}</td><td>{e.title}</td><td>{e.source}</td><td className="figures">{shortHash(e.file_sha256 ? `sha256:${e.file_sha256}` : e.pinned_ref, 8, 4)}</td><td>{dateTime(e.received_at)}</td></tr>)}</tbody>
              </table>
            ) : <p className={s.muted}>None.</p>}
            <h4 className={s.h4}>Verdicts</h4>
            {m.verdicts.length ? m.verdicts.map((v) => (
              <div key={v.id} className={s.verdict}>
                <p><strong>{v.met ? "Met" : "Not met"}</strong> · {dateTime(v.created_at)} · {v.model} ({v.prompt_version}) · confidence {percent(v.model_confidence)} · gate {v.gate.replace(/_/g, " ")}</p>
                <p className={s.rationale}>{v.rationale}</p>
                <ul className={s.list}>
                  {v.results.map((r) => (
                    <li key={r.criterion_id}>
                      {r.met ? "✓" : "✕"} <code>{r.criterion_id}</code> {r.observed ? `observed ${r.observed}` : ""} · {r.decided_by} · cites {r.evidence_ids.map((id) => `e${m.evidence.findIndex((e) => e.id === id) + 1}`).join(", ") || "nothing"}
                      {r.quote && <> · “{r.quote}”</>}
                    </li>
                  ))}
                </ul>
                {v.skeptic && (
                  <p className={s.muted}>
                    Second opinion ({v.skeptic.model}, {v.skeptic.prompt_version}):{" "}
                    {v.skeptic.error ? `couldn't run (${v.skeptic.error}), so a person decides` : v.skeptic.disagrees_on.length ? `disagreed on ${v.skeptic.disagrees_on.join(", ")}` : "agreed"}. {v.skeptic.rationale}
                  </p>
                )}
              </div>
            )) : <p className={s.muted}>None.</p>}
            {(m.delivery ?? []).length > 0 && (
              <>
                <h4 className={s.h4}>Delivery</h4>
                <ul className={s.list}>
                  {(m.delivery ?? []).map((d, k) => (
                    <li key={k}>
                      {dateTime(d.at)}: {d.action === "deliverable_released" ? "Evidence opened to the payer at capture" : d.action === "handover_checked" ? `Handover checked: ${d.matches ? (d.how === "same_tree" ? "same files as verified" : "same commit as verified") : "does not match what was verified"}` : d.matches ? "Downloaded; matches what was verified" : "Download refused: file doesn't match what was verified"} · {actorText(d.actor, names)}
                    </li>
                  ))}
                </ul>
              </>
            )}
            {m.reviews.length > 0 && (
              <>
                <h4 className={s.h4}>Reviews</h4>
                <ul className={s.list}>{m.reviews.map((r, k) => <li key={k}>{dateTime(r.at)}: {actionText(r.action)} by {actorText(r.actor, names)}{r.note && ` · “${r.note}”`}</li>)}</ul>
              </>
            )}
            {m.settlements.length > 0 && (
              <>
                <h4 className={s.h4}>Payments</h4>
                <ul className={s.list}>{m.settlements.map((x) => <li key={x.idempotency_key}>{x.mode === "invoice_on_verify" ? "Invoice" : "Capture"}, attempt {x.attempt} · {x.status} · {x.paypal_ref ?? "no reference"} · key <code>{x.idempotency_key}</code>{x.error && ` · ${x.error}`}</li>)}
                  {(m.disbursements ?? []).map((x) => <li key={x.idempotency_key}>{x.kind === "refund" ? "Refund to payer" : "Payout to payee"}, attempt {x.attempt} · {x.status} · {x.paypal_ref ?? "no reference"} · key <code>{x.idempotency_key}</code>{x.error && ` · ${x.error}`}</li>)}
                </ul>
              </>
            )}
          </section>
        ))}

        <section aria-labelledby="pk-ledger">
          <h3 id="pk-ledger" className={s.h}>{p.milestones.length + 2}. Ledger</h3>
          {p.ledger.length ? (
            <table className={s.table}>
              <thead><tr><th scope="col">When</th><th scope="col">Movement</th><th scope="col" style={{ textAlign: "right" }}>Amount</th><th scope="col">PayPal reference</th></tr></thead>
              <tbody>{p.ledger.map((e, i) => <tr key={i}><td>{dateTime(e.at)}</td><td>{e.kind}</td><td className="figures" style={{ textAlign: "right" }}>{money(e.amount, cur)}</td><td className="figures">{e.paypal_ref}</td></tr>)}</tbody>
            </table>
          ) : <p className={s.muted}>No money has moved.</p>}
        </section>
      </article>
    </div>
  );
}
