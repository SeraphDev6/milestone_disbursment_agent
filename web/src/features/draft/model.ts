/**
 * The draft agreement as the editor holds it, and its conversion to and from the API.
 * Money stays a decimal string end to end; targets are typed by comparator only on the way out.
 */
import type {
  AgreementCreate,
  Comparator,
  CriterionDraft,
  DeliverableRelease,
  EvidenceVisibility,
  EvidenceType,
  ExtractionResult,
  MilestoneDraft,
  MilestoneRead,
  SettlementMode,
  VerificationMethod,
} from "@/api/types";
import type { Problem } from "@/design/components";
import { fieldId } from "@/domain/lint-paths";
import { compatibleChecks } from "@/domain/checks";

export type CriterionForm = {
  key: string;
  id?: string | null;
  statement: string;
  metric: string;
  comparator: Comparator;
  target: string;
  unit: string;
  evidence_required: EvidenceType[];
  evidence_description: string;
  verification: VerificationMethod;
  check_id: string | null;
  due_by: string | null;
};

export type TrancheForm = { key: string; title: string; amount: string; due_date: string | null; criteria: CriterionForm[] };

export type DraftForm = {
  title: string;
  total_amount: string;
  currency: string;
  settlement_mode: SettlementMode;
  auto_release_threshold: number;
  require_reviewer_approval: boolean;
  reviewer_decides_withheld: boolean;
  dispute_window_days: number;
  deliverable_release: DeliverableRelease;
  min_evidence_visibility: EvidenceVisibility;
  counterparty_email: string;
  reviewer_emails: string[];
  source_document_uri: string | null;
  milestones: TrancheForm[];
};

let n = 0;
export const newKey = () => `k${Date.now().toString(36)}${(n++).toString(36)}`;

export function blankCriterion(): CriterionForm {
  return {
    key: newKey(),
    statement: "",
    metric: "",
    comparator: "at_least",
    target: "",
    unit: "",
    evidence_required: ["document"],
    evidence_description: "",
    verification: "model",
    check_id: null,
    due_by: null,
  };
}

export function blankTranche(i = 0): TrancheForm {
  return { key: newKey(), title: `Tranche ${i + 1}`, amount: "", due_date: null, criteria: [blankCriterion()] };
}

export function blankDraft(): DraftForm {
  return {
    title: "",
    total_amount: "",
    currency: "USD",
    settlement_mode: "escrow_release",
    auto_release_threshold: 0.85,
    require_reviewer_approval: false,
    reviewer_decides_withheld: true,
    dispute_window_days: 0,
    deliverable_release: "on_submit",
    min_evidence_visibility: "hash_only",
    counterparty_email: "",
    reviewer_emails: [],
    source_document_uri: null,
    milestones: [blankTranche(0)],
  };
}

function criterionFrom(c: CriterionDraft): CriterionForm {
  return {
    key: newKey(),
    id: c.id ?? null,
    statement: c.statement,
    metric: c.measure.metric,
    comparator: c.measure.comparator,
    target: c.measure.target === null || c.measure.target === undefined ? "" : String(c.measure.target),
    unit: c.measure.unit ?? "",
    evidence_required: c.evidence_required,
    evidence_description: c.evidence_description,
    verification: c.verification ?? "model",
    check_id: c.check_id ?? null,
    due_by: c.due_by ?? null,
  };
}

export function tranchesFrom(ms: (MilestoneDraft | MilestoneRead)[]): TrancheForm[] {
  return ms.map((m) => ({
    key: newKey(),
    title: m.title,
    amount: String(m.amount),
    due_date: m.due_date ?? null,
    criteria: m.criteria.map((c) => criterionFrom(c as CriterionDraft)),
  }));
}

export function draftFromExtraction(x: ExtractionResult, fileName: string): DraftForm {
  return {
    ...blankDraft(),
    title: x.title,
    total_amount: x.total_amount ? String(x.total_amount) : "",
    currency: x.currency || "USD",
    source_document_uri: fileName,
    milestones: x.milestones.length ? tranchesFrom(x.milestones) : [blankTranche(0)],
  };
}

function targetOut(c: CriterionForm): number | string | null {
  if (c.comparator === "exists") return null;
  if (c.comparator === "at_least" || c.comparator === "at_most") return c.target.trim() === "" ? null : Number(c.target.replace(/,/g, ""));
  return c.target.trim() || null;
}

export function criterionOut(c: CriterionForm): CriterionDraft {
  return {
    ...(c.id ? { id: c.id } : {}),
    statement: c.statement.trim(),
    measure: { metric: c.metric.trim(), comparator: c.comparator, target: targetOut(c), unit: c.unit.trim() || null },
    evidence_required: c.evidence_required,
    evidence_description: c.evidence_description.trim(),
    verification: c.verification,
    check_id: c.verification === "check" ? c.check_id : null,
    due_by: c.due_by || null,
  };
}

export function tranchesOut(ms: TrancheForm[]): MilestoneDraft[] {
  return ms.map((m) => ({ title: m.title.trim(), amount: m.amount.trim() || "0", due_date: m.due_date || null, criteria: m.criteria.map(criterionOut) }));
}

export function agreementOut(d: DraftForm): AgreementCreate {
  return {
    title: d.title.trim(),
    total_amount: d.total_amount.trim(),
    currency: d.currency,
    settlement_mode: d.settlement_mode,
    auto_release_threshold: d.auto_release_threshold,
    require_reviewer_approval: d.require_reviewer_approval,
    reviewer_decides_withheld: d.reviewer_decides_withheld,
    dispute_window_days: d.dispute_window_days,
    deliverable_release: d.deliverable_release ?? "on_submit",
    min_evidence_visibility: d.min_evidence_visibility ?? "hash_only",
    source_document_uri: d.source_document_uri,
    counterparty_email: d.counterparty_email.trim(),
    reviewer_emails: d.reviewer_emails.map((e) => e.trim()).filter(Boolean),
    milestones: tranchesOut(d.milestones),
  };
}

export const sumAmounts = (ms: TrancheForm[]) => ms.reduce((n, m) => n + (Number(m.amount) || 0), 0);

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const isNum = (v: string) => v.trim() !== "" && Number.isFinite(Number(v.replace(/,/g, "")));
const isDate = (v: string) => /^\d{4}-\d{2}-\d{2}$/.test(v);

/**
 * What must be fixed before the server will even accept the request (shape, not judgement). The server's
 * lint handles judgement ("vague term", "check can't run on that evidence") and is shown alongside.
 */
export function tranchesProblems(ms: TrancheForm[]): Problem[] {
  const p: Problem[] = [];
  const add = (path: string, message: string) => p.push({ path, message, href: `#${fieldId(path)}` });
  ms.forEach((m, i) => {
    const base = `milestones[${i}]`;
    if (!m.title.trim()) add(`${base}.title`, "Give the tranche a name");
    if (!isNum(m.amount) || Number(m.amount) <= 0) add(`${base}.amount`, "Enter an amount greater than zero");
    if (!m.criteria.length) add(`${base}.title`, "Add at least one criterion");
    m.criteria.forEach((c, j) => {
      const cb = `${base}.criteria[${j}]`;
      if (!c.statement.trim()) add(`${cb}.statement`, "Write the condition a reviewer will check");
      if (c.metric.trim().length < 2) add(`${cb}.measure.metric`, "Name what is counted or checked");
      if ((c.comparator === "at_least" || c.comparator === "at_most") && !isNum(c.target)) add(`${cb}.measure.target`, "Enter the number to compare against");
      if (c.comparator === "on_or_before" && !isDate(c.target)) add(`${cb}.measure.target`, "Enter the date it must happen by");
      if (c.comparator === "equals" && !c.target.trim()) add(`${cb}.measure.target`, "Enter the value it must equal");
      if (!c.evidence_required.length) add(`${cb}.evidence_required`, "Choose at least one kind of evidence");
      if (!c.evidence_description.trim()) add(`${cb}.evidence_description`, "Describe the artifact that proves it");
      if (c.verification === "check") {
        if (!c.check_id) add(`${cb}.check_id`, "Choose which check decides it");
        else if (!compatibleChecks(c.comparator, c.evidence_required).some((k) => k.id === c.check_id))
          add(`${cb}.check_id`, "That check can't run on this comparator and evidence");
      }
    });
  });
  return p;
}

export function agreementProblems(d: DraftForm, step: "terms" | "parties" | "all"): Problem[] {
  const p: Problem[] = [];
  const add = (id: string, message: string) => p.push({ message, href: `#${id}` });
  if (step !== "parties") {
    if (!d.title.trim()) add("ag-title", "Give the agreement a title");
    if (!isNum(d.total_amount) || Number(d.total_amount) <= 0) add("ag-total", "Enter the agreement total");
    p.push(...tranchesProblems(d.milestones));
  }
  if (step !== "terms") {
    if (!EMAIL.test(d.counterparty_email.trim())) add("ag-counterparty", "Enter the payee's email like name@example.org");
    const emails = [d.counterparty_email, ...d.reviewer_emails].map((e) => e.trim().toLowerCase()).filter(Boolean);
    d.reviewer_emails.forEach((e, i) => {
      if (e.trim() && !EMAIL.test(e.trim())) add(`ag-reviewer-${i}`, `Reviewer ${i + 1}: enter an email like name@example.org`);
    });
    if (new Set(emails).size !== emails.length) add("ag-counterparty", "The payee and reviewers must be different people");
  }
  return p;
}
