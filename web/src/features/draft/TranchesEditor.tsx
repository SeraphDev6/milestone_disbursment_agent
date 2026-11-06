/**
 * The tranche and criteria editor. Each criterion is written so a reviewer can answer yes or no from the
 * evidence alone: a statement, a measure (what's counted, how it compares, the target), the evidence that
 * proves it, and who decides. Lint notes and the source quote sit in the margin beside it.
 */
import type { LintIssue, LintReport } from "@/api/types";
import { compatibleChecks } from "@/domain/checks";
import { COMPARATOR_LABEL, EVIDENCE_LABEL, measureText, money, VERIFICATION_LABEL } from "@/domain/format";
import { fieldId, issuesFor } from "@/domain/lint-paths";
import {
  Button,
  CheckboxGroup,
  DateField,
  Glyph,
  layout,
  NumberField,
  RadioGroup,
  Select,
  Sheet,
  TextArea,
  TextField,
  type Problem,
} from "@/design/components";
import { blankCriterion, blankTranche, sumAmounts, type CriterionForm, type TrancheForm } from "./model";
import s from "./Editor.module.css";

type Props = {
  tranches: TrancheForm[];
  onChange: (t: TrancheForm[]) => void;
  currency: string;
  total: string;
  lint: LintReport | null;
  lintPending?: boolean;
  problems: Problem[];
  /** path ("milestones[0].criteria[1]") -> quote from the source document */
  provenance?: Record<string, string>;
};

const COMPARATORS = (Object.keys(COMPARATOR_LABEL) as (keyof typeof COMPARATOR_LABEL)[]).map((id) => ({ id, label: COMPARATOR_LABEL[id] }));
const EVIDENCE = (Object.keys(EVIDENCE_LABEL) as (keyof typeof EVIDENCE_LABEL)[]).map((id) => ({ id, label: EVIDENCE_LABEL[id] }));
const VERIFICATION = [
  { id: "check" as const, label: VERIFICATION_LABEL.check, description: "A rule decides it: rows counted, totals summed, CI green, dated in time." },
  { id: "model" as const, label: VERIFICATION_LABEL.model, description: "The verifier judges it and must cite the evidence it relied on." },
  { id: "human" as const, label: VERIFICATION_LABEL.human, description: "Always goes to the payer side; this tranche can never auto-release." },
];

export function TranchesEditor({ tranches, onChange, currency, total, lint, lintPending, problems, provenance = {} }: Props) {
  const err = (path: string) => problems.find((p) => p.path === path)?.message ?? null;
  const issues = lint?.issues ?? [];
  const sum = sumAmounts(tranches);
  const totalN = Number(total) || 0;

  const setT = (i: number, patch: Partial<TrancheForm>) => onChange(tranches.map((t, n) => (n === i ? { ...t, ...patch } : t)));
  const setC = (i: number, j: number, patch: Partial<CriterionForm>) =>
    setT(i, { criteria: tranches[i]!.criteria.map((c, n) => (n === j ? { ...c, ...patch } : c)) });
  const move = <T,>(list: T[], from: number, to: number) => {
    const next = [...list];
    const [x] = next.splice(from, 1);
    next.splice(to, 0, x!);
    return next;
  };

  const amountIssues = issues.filter((i) => i.code === "amounts_mismatch");

  return (
    <div className={layout.stackLg}>
      {tranches.map((t, i) => {
        const base = `milestones[${i}]`;
        const trancheIssues = issuesFor(issues, i);
        return (
          <Sheet
            key={t.key}
            title={<span className="sr-only">Tranche {i + 1}: {t.title || "untitled"}</span>}
            actions={
              <>
                <Button size="sm" variant="quiet" icon="chevronUp" aria-label={`Move tranche ${i + 1} up`} isDisabled={i === 0} onPress={() => onChange(move(tranches, i, i - 1))} />
                <Button size="sm" variant="quiet" icon="chevronDown" aria-label={`Move tranche ${i + 1} down`} isDisabled={i === tranches.length - 1} onPress={() => onChange(move(tranches, i, i + 1))} />
                <Button size="sm" variant="quiet" icon="trash" isDisabled={tranches.length === 1} onPress={() => onChange(tranches.filter((_, n) => n !== i))}>Remove tranche</Button>
              </>
            }
          >
            <div className={s.tranche}>
              <div className={s.trancheHead}>
                <span className={s.numeral} aria-hidden="true">№{i + 1}</span>
                <div className={s.trancheFields}>
                  <TextField id={fieldId(`${base}.title`)} label="Tranche" value={t.title} onChange={(v) => setT(i, { title: v })} error={err(`${base}.title`)} isRequired maxLength={200} />
                  <NumberField id={fieldId(`${base}.amount`)} label="Amount" prefix={currency} fractionDigits={2} minValue={0} value={t.amount === "" ? null : Number(t.amount)} onChange={(v) => setT(i, { amount: v === null ? "" : v.toFixed(2) })} error={err(`${base}.amount`)} isRequired />
                  <DateField id={fieldId(`${base}.due_date`)} label="Due" optional value={t.due_date} onChange={(v) => setT(i, { due_date: v })} description="Passing it with no evidence marks the tranche overdue." />
                </div>
              </div>
              {trancheIssues.length > 0 && <Notes issues={trancheIssues} />}
              <ol className={s.criteria} aria-label={`Criteria for tranche ${i + 1}`}>
                {t.criteria.map((c, j) => (
                  <CriterionEditor
                    key={c.key}
                    c={c}
                    base={`${base}.criteria[${j}]`}
                    label={`Tranche ${i + 1}, criterion ${j + 1}`}
                    err={err}
                    issues={issuesFor(issues, i, j)}
                    quote={provenance[`${base}.criteria[${j}]`]}
                    lintPending={lintPending}
                    onChange={(patch) => setC(i, j, patch)}
                    onRemove={t.criteria.length > 1 ? () => setT(i, { criteria: t.criteria.filter((_, n) => n !== j) }) : undefined}
                    onMove={(d) => setT(i, { criteria: move(t.criteria, j, j + d) })}
                    canUp={j > 0}
                    canDown={j < t.criteria.length - 1}
                  />
                ))}
              </ol>
              <div>
                <Button icon="plus" onPress={() => setT(i, { criteria: [...t.criteria, blankCriterion()] })}>Add a criterion to tranche {i + 1}</Button>
              </div>
            </div>
          </Sheet>
        );
      })}
      <div>
        <Button icon="plus" onPress={() => onChange([...tranches, blankTranche(tranches.length)])}>Add a tranche</Button>
      </div>
      <div className={s.sumLine} role="status" aria-live="polite">
        <Glyph name={Math.abs(sum - totalN) < 0.005 ? "check" : "warning"} />
        <span>
          Tranches add up to <strong className="figures">{money(sum, currency)}</strong>
          {totalN > 0 && <> of <span className="figures">{money(totalN, currency)}</span></>}
        </span>
        {totalN > 0 && Math.abs(sum - totalN) >= 0.005 && <span className={s.sumOff}>{sum > totalN ? "over" : "short"} by {money(Math.abs(sum - totalN), currency)}</span>}
        {totalN > 0 && Math.abs(sum - totalN) < 0.005 && <span className={s.sumOk}>Balanced</span>}
        {amountIssues.length === 0 && lint && <span style={{ marginLeft: "auto", color: "var(--text-secondary)" }}>{lintPending ? "Checking criteria…" : `${lint.issues?.filter((x) => x.level === "error").length ?? 0} errors, ${lint.issues?.filter((x) => x.level === "warning").length ?? 0} warnings`}</span>}
      </div>
    </div>
  );
}

function CriterionEditor({
  c, base, label, err, issues, quote, onChange, onRemove, onMove, canUp, canDown, lintPending,
}: {
  c: CriterionForm;
  base: string;
  label: string;
  err: (p: string) => string | null;
  issues: LintIssue[];
  quote?: string;
  onChange: (p: Partial<CriterionForm>) => void;
  onRemove?: () => void;
  onMove: (d: -1 | 1) => void;
  canUp: boolean;
  canDown: boolean;
  lintPending?: boolean;
}) {
  const checks = compatibleChecks(c.comparator, c.evidence_required);
  const notesId = `${fieldId(base)}-notes`;
  const targetField =
    c.comparator === "exists" ? null : c.comparator === "on_or_before" ? (
      <DateField id={fieldId(`${base}.measure.target`)} label="Target date" value={c.target || null} onChange={(v) => onChange({ target: v ?? "" })} error={err(`${base}.measure.target`)} isRequired />
    ) : c.comparator === "equals" ? (
      <TextField id={fieldId(`${base}.measure.target`)} label="Target value" value={c.target} onChange={(v) => onChange({ target: v })} error={err(`${base}.measure.target`)} isRequired />
    ) : (
      <NumberField id={fieldId(`${base}.measure.target`)} label="Target" value={c.target === "" ? null : Number(c.target)} onChange={(v) => onChange({ target: v === null ? "" : String(v) })} error={err(`${base}.measure.target`)} isRequired />
    );

  return (
    <li className={s.criterion} aria-label={label} aria-describedby={issues.length || quote ? notesId : undefined}>
      <div className={s.critBody}>
        <div className={s.critHead}>
          <span className={s.critLabel}>{label}</span>
          <span className={layout.row}>
            <Button size="sm" variant="quiet" aria-label={`Move ${label} up`} icon="chevronUp" isDisabled={!canUp} onPress={() => onMove(-1)} />
            <Button size="sm" variant="quiet" aria-label={`Move ${label} down`} icon="chevronDown" isDisabled={!canDown} onPress={() => onMove(1)} />
            {onRemove && <Button size="sm" variant="quiet" icon="trash" onPress={onRemove}>Remove</Button>}
          </span>
        </div>
        <TextArea
          id={fieldId(`${base}.statement`)}
          label="Condition"
          description="One condition a reviewer could answer yes or no from the evidence alone."
          value={c.statement}
          onChange={(v) => onChange({ statement: v })}
          error={err(`${base}.statement`)}
          rows={2}
          maxLength={500}
          isRequired
        />
        <fieldset className={s.measure}>
          <legend className={s.measureTitle}>Measure</legend>
          <TextField id={fieldId(`${base}.measure.metric`)} label="What's counted or checked" placeholder="workshop attendees" value={c.metric} onChange={(v) => onChange({ metric: v })} error={err(`${base}.measure.metric`)} isRequired />
          <Select id={fieldId(`${base}.measure.comparator`)} label="Compared as" value={c.comparator} options={COMPARATORS} onChange={(v) => onChange({ comparator: v, target: v === "exists" ? "" : c.target, check_id: null })} />
          {targetField}
          {(c.comparator === "at_least" || c.comparator === "at_most" || c.comparator === "equals") && (
            <TextField id={fieldId(`${base}.measure.unit`)} label="Unit" optional placeholder="people" value={c.unit} onChange={(v) => onChange({ unit: v })} />
          )}
          {c.metric && <p className={s.preview} aria-live="polite">Reads as: {c.metric} {measureText({ comparator: c.comparator, target: c.target, unit: c.unit })}</p>}
        </fieldset>
        <CheckboxGroup
          id={fieldId(`${base}.evidence_required`)}
          label="Evidence that can prove it"
          value={c.evidence_required}
          options={EVIDENCE}
          onChange={(v) => onChange({ evidence_required: v, check_id: c.check_id && compatibleChecks(c.comparator, v).some((k) => k.id === c.check_id) ? c.check_id : null })}
          error={err(`${base}.evidence_required`)}
        />
        <TextField
          id={fieldId(`${base}.evidence_description`)}
          label="The specific artifact"
          placeholder="Signed sign-in sheets for each session, one row per attendee"
          value={c.evidence_description}
          onChange={(v) => onChange({ evidence_description: v })}
          error={err(`${base}.evidence_description`)}
          maxLength={300}
          isRequired
        />
        <NumberField
          id={fieldId(`${base}.weight`)}
          label="Weight"
          minValue={0.01}
          step={0.5}
          value={Number(c.weight) || 1}
          onChange={(v) => onChange({ weight: v === null || v === undefined || Number.isNaN(v) ? "1" : String(v) })}
          description="Relative to the tranche's other criteria. The verdict reports the met share by weight; release itself is still all-or-nothing."
        />
        <RadioGroup id={fieldId(`${base}.verification`)} label="Who decides" value={c.verification} options={VERIFICATION} onChange={(v) => onChange({ verification: v })} />
        {c.verification === "check" && (
          <Select
            id={fieldId(`${base}.check_id`)}
            label="Check"
            value={c.check_id}
            onChange={(v) => onChange({ check_id: v })}
            error={err(`${base}.check_id`)}
            placeholder={checks.length ? "Choose a check" : "No check fits this measure and evidence"}
            options={checks.map((k) => ({ id: k.id, label: k.label, description: k.description }))}
            description={checks.length ? "Only checks that can run on this comparator and evidence are offered." : "Change the comparator or evidence, or let the verifier decide."}
          />
        )}
        <DateField id={fieldId(`${base}.due_by`)} label="Must be met by" optional value={c.due_by} onChange={(v) => onChange({ due_by: v })} description="Only if earlier than the tranche's due date." />
      </div>
      <aside className={s.margin} id={notesId} aria-label={`Notes on ${label}`}>
        {quote && (
          <>
            <p className={s.marginTitle}>From the agreement</p>
            <blockquote className={s.quote}>“{quote}”</blockquote>
          </>
        )}
        <p className={s.marginTitle}>Lint</p>
        {issues.length ? <Notes issues={issues} /> : <p className={s.clear}><Glyph name="check" size={14} />{lintPending ? "Checking…" : "No problems found"}</p>}
      </aside>
    </li>
  );
}

function Notes({ issues }: { issues: LintIssue[] }) {
  return (
    <div style={{ display: "grid", gap: "var(--space-2)" }}>
      {issues.map((i, n) => (
        <div key={n} className={`${s.note} ${i.level === "error" ? s.noteError : s.noteWarning}`}>
          <p>
            <span className={s.noteLevel}>{i.level === "error" ? "Must fix: " : "Worth a look: "}</span>
            {i.message}
          </p>
          {i.suggestion && <p className={s.suggestion}>{i.suggestion}</p>}
        </div>
      ))}
    </div>
  );
}
