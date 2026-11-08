import type { Criterion, CriterionResult, EvidenceRead, VerdictRead } from "@/api/types";
import { dateTime, measureText, percent } from "@/domain/format";
import { Callout, Glyph, ThresholdMeter, Facts } from "@/design/components";
import s from "./Tranche.module.css";

const GATE: Record<string, string> = {
  auto_release: "Passed the gate: released without a person",
  needs_review: "Sent to a person for review",
  precheck: "Pre-check only: nothing was recorded or moved",
};

/** Footnote-style citations linking each result to the evidence it rests on. */
export function Citations({ ids, evidence }: { ids: string[]; evidence: EvidenceRead[] }) {
  if (!ids.length) return null;
  return (
    <span className={s.cite}>
      {ids.map((id) => {
        const n = evidence.findIndex((e) => e.id === id);
        const label = n >= 0 ? `e${n + 1}` : id.startsWith("draft-") ? `d${Number(id.slice(6)) + 1}` : "e?";
        return n >= 0 ? (
          <a key={id} href={`#ev-${id}`} aria-label={`Cites evidence ${n + 1}: ${evidence[n]!.title}`}>[{label}]</a>
        ) : (
          <span key={id}>[{label}]</span>
        );
      })}
    </span>
  );
}

/** Each criterion with its result: met or not, who decided, what was observed, the quote, what's missing. */
export function Results({ criteria, results, evidence }: { criteria: Criterion[]; results?: CriterionResult[]; evidence: EvidenceRead[] }) {
  return (
    <ol className={s.results} aria-label="Criteria and results">
      {criteria.map((c, i) => {
        const r = results?.find((x) => x.criterion_id === c.id);
        const met = r ? r.met && r.evidence_ids.length > 0 : null;
        return (
          <li key={c.id ?? i} className={s.result}>
            <span className={`${s.verdictMark} ${met === null ? s.unjudged : met ? s.met : s.unmet}`} aria-hidden="true">
              <Glyph name={met === null ? "ring" : met ? "check" : "cross"} />
            </span>
            <div className={s.resultBody}>
              <p className={s.resultStatement}>
                <span className="sr-only">{met === null ? "Not judged yet: " : met ? "Met: " : "Not met: "}</span>
                {c.statement}
                {r && <Citations ids={r.evidence_ids} evidence={evidence} />}
              </p>
              <p className={s.resultMeta}>
                <span>Needs <strong>{c.measure.metric} {measureText(c.measure)}</strong></span>
                {r?.observed && <span>Found <strong>{r.observed}</strong></span>}
                {r && <span>Decided by <strong>{r.decided_by.replace(/^check:/, "check · ").replace(/^human:.*/, "a person")}</strong></span>}
                {r && r.met && !r.evidence_ids.length && <span><strong>Uncited, so counted as not met</strong></span>}
              </p>
              {r?.quote && <blockquote className={s.quote}>“{r.quote}”</blockquote>}
              {r && !met && r.missing && (
                <p className={s.missing}><strong>What would satisfy it: </strong>{r.missing}</p>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function SecondOpinion({ v }: { v: VerdictRead }) {
  const k = v.skeptic;
  if (!k) return null;
  const tone = k.error || k.disagrees_on?.length ? "warning" : "success";
  const title = k.error
    ? "The second opinion couldn't run, so a person decides"
    : k.disagrees_on?.length
      ? `A second, skeptical check disagreed on ${k.disagrees_on.join(", ")}`
      : "A second, skeptical check agreed";
  return (
    <Callout tone={tone} title={title}>
      {k.error ? <p>{k.error}</p> : <p>{k.rationale}</p>}
      {k.results.filter((r) => !r.met && r.missing).map((r) => (
        <p key={r.criterion_id}><code>{r.criterion_id}</code>: {r.missing}</p>
      ))}
      <p style={{ fontSize: "var(--text-sm)", color: "var(--text-secondary)" }}>
        Because the payer pays before seeing the work, auto-release needs both checks to agree. <code>{k.model}</code> · <code>{k.prompt_version}</code>
      </p>
    </Callout>
  );
}

export function VerdictSummary({ v, threshold }: { v: VerdictRead; threshold: number }) {
  return (
    <div style={{ display: "grid", gap: "var(--space-4)" }}>
      <p className={s.rationale}>{v.rationale}</p>
      <SecondOpinion v={v} />
      <ThresholdMeter label="Verifier confidence" value={v.model_confidence} threshold={threshold} />
      <Facts
        items={[
          ["Outcome", v.met ? "Every criterion met and cited" : "Not every criterion is met"],
          ["Gate", GATE[v.gate] ?? v.gate],
          ["Judged", dateTime(v.created_at)],
          ["Model", <code key="m">{v.model}</code>],
          ["Prompt", <code key="p">{v.prompt_version}</code>],
          ["Confidence", percent(v.model_confidence)],
          ...(v.weighted_share !== null && v.weighted_share !== undefined && v.weighted_share < 1 ? [["Met by weight", `${percent(v.weighted_share)} of the tranche's criteria weight is met`] as [string, string]] : []),
        ]}
      />
    </div>
  );
}
