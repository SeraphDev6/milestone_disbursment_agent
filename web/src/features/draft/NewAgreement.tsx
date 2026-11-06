import { useEffect, useMemo, useRef, useState } from "react";
import { extractAgreement, hasDemo, useCreateAgreement, useHealth, useOrg } from "@/api/queries";
import type { AgreementCreated, ExtractionResult } from "@/api/types";
import { errorMessage, isApiError } from "@/api/errors";
import { paths, useOrgCtx } from "@/app/context";
import { useTitle } from "@/app/RouteAnnouncer";
import { atLeast } from "@/domain/permissions";
import { MODE_LABEL, money, percent, RELEASE_LABEL, VISIBILITY_LABEL, VISIBILITY_ORDER } from "@/domain/format";
import { fieldId } from "@/domain/lint-paths";
import {
  Button,
  ButtonLink,
  Callout,
  Checkbox,
  ErrorSummary,
  Facts,
  FileDropZone,
  layout,
  NumberField,
  PageHeader,
  PermissionNote,
  RadioGroup,
  Select,
  Sheet,
  Stamp,
  Switch,
  TextField,
  toast,
  type Problem,
} from "@/design/components";
import { DEMO_GRANT } from "@/features/demo/seed";
import { agreementOut, agreementProblems, blankDraft, draftFromExtraction, tranchesFrom, type DraftForm } from "./model";
import { Steps } from "./Steps";
import { TranchesEditor } from "./TranchesEditor";
import { useLiveLint } from "./useLiveLint";
import { OrgPaypalFix } from "./OrgPaypalFix";

const STEPS = ["Source", "Terms and criteria", "People", "Review and send"];
const CURRENCIES = ["USD", "EUR", "GBP", "CAD", "AUD"].map((c) => ({ id: c, label: c }));
const DOC_TYPES = ["application/pdf", "text/plain", "text/markdown", ".md"];

type Saved = { draft: DraftForm; step: number; extraction: ExtractionResult | null; answered: number[] };

function loadSaved(orgId: string): Saved | null {
  try {
    const raw = localStorage.getItem(`mde.draft.${orgId}`);
    return raw ? (JSON.parse(raw) as Saved) : null;
  } catch {
    return null;
  }
}

export function NewAgreement() {
  const org = useOrgCtx();
  useTitle("New agreement", org.orgName);
  const saved = useMemo(() => loadSaved(org.orgId), [org.orgId]);
  const [draft, setDraft] = useState<DraftForm>(saved?.draft ?? blankDraft());
  const [step, setStep] = useState(saved?.step ?? 0);
  const [extraction, setExtraction] = useState<ExtractionResult | null>(saved?.extraction ?? null);
  const [answered, setAnswered] = useState<number[]>(saved?.answered ?? []);
  const [problems, setProblems] = useState<Problem[]>([]);
  const [attempt, setAttempt] = useState(0);
  const [created, setCreated] = useState<AgreementCreated | null>(null);
  const create = useCreateAgreement(org.orgId);
  const orgQ = useOrg(org.orgId);
  const canCreate = atLeast(org.role, "member");
  const { report, pending } = useLiveLint(draft.total_amount, draft.milestones, step >= 1 && !created);

  // Autosave the unsent draft in this browser (per org), so a reload or a detour doesn't lose work.
  useEffect(() => {
    if (created) return;
    try {
      localStorage.setItem(`mde.draft.${org.orgId}`, JSON.stringify({ draft, step, extraction, answered } satisfies Saved));
    } catch {
      /* storage full or blocked: fine, the draft just isn't kept */
    }
  }, [draft, step, extraction, answered, org.orgId, created]);

  const provenance = useMemo(() => Object.fromEntries((extraction?.provenance ?? []).map((p) => [p.path, p.source_quote])), [extraction]);
  const set = (patch: Partial<DraftForm>) => setDraft((d) => ({ ...d, ...patch }));

  function go(next: number) {
    const scope = step === 1 ? "terms" : step === 2 ? "parties" : step === 3 ? "all" : null;
    if (next > step && scope) {
      const p = agreementProblems(draft, scope);
      setProblems(p);
      setAttempt((n) => n + 1);
      if (p.length) return;
    } else setProblems([]);
    setStep(next);
    window.scrollTo({ top: 0 });
  }

  function discard() {
    try {
      localStorage.removeItem(`mde.draft.${org.orgId}`);
    } catch {
      /* ignore */
    }
    setDraft(blankDraft());
    setExtraction(null);
    setAnswered([]);
    setStep(0);
    setProblems([]);
  }

  async function send() {
    const p = agreementProblems(draft, "all");
    const unanswered = (extraction?.open_questions ?? []).filter((_, i) => !answered.includes(i));
    if (unanswered.length) p.push({ message: `Resolve the ${unanswered.length} open question${unanswered.length === 1 ? "" : "s"} from the document`, href: "#open-questions" });
    setProblems(p);
    setAttempt((n) => n + 1);
    if (p.length) return;
    try {
      const r = await create.mutateAsync(agreementOut(draft));
      setCreated(r);
      try {
        localStorage.removeItem(`mde.draft.${org.orgId}`);
      } catch {
        /* ignore */
      }
      toast("Draft agreement created", { body: `Invitations sent to ${r.invitations.map((i) => i.email).join(", ")}.` });
    } catch (e) {
      if (isApiError(e) && e.fields.length) setProblems(e.fields.map((f) => ({ message: f.message, path: f.path, href: `#${fieldId(f.path)}` })));
      else setProblems([{ message: errorMessage(e) }]);
    }
  }

  if (created) return <Created created={created} orgId={org.orgId} />;

  return (
    <div className={layout.stack}>
      <PageHeader
        eyebrow={`${org.orgName} pays`}
        title="New agreement"
        lede="Turn a grant or statement of work into tranches with criteria both sides can check. Your organization is the payer; the payee and any reviewers are invited by email."
        actions={step > 0 && <Button variant="quiet" icon="trash" onPress={discard}>Discard draft</Button>}
      />
      {!canCreate && <PermissionNote>Creating agreements needs member or above in {org.orgName}; you're a {org.role}.</PermissionNote>}
      {orgQ.data && !orgQ.data.paypal_email && <OrgPaypalFix org={orgQ.data} role={org.role} />}
      <Steps steps={STEPS} current={step} onGo={go} label="Agreement steps" />
      <ErrorSummary problems={problems} focusKey={attempt} />

      {step === 0 && (
        <SourceStep
          onStart={(d, x) => {
            setDraft(d);
            setExtraction(x);
            setAnswered([]);
            setStep(1);
          }}
        />
      )}

      {step === 1 && (
        <div className={layout.stackLg}>
          {extraction && <ExtractionNotes x={extraction} answered={answered} setAnswered={setAnswered} />}
          <Sheet title="Terms">
            <div className={layout.grid2}>
              <TextField id="ag-title" label="Title" description="The grant or SOW name both sides will recognize." value={draft.title} onChange={(v) => set({ title: v })} error={problems.find((p) => p.href === "#ag-title")?.message} isRequired maxLength={200} />
              <NumberField id="ag-total" label="Agreement total" prefix={draft.currency} fractionDigits={2} minValue={0} value={draft.total_amount === "" ? null : Number(draft.total_amount)} onChange={(v) => set({ total_amount: v === null ? "" : v.toFixed(2) })} error={problems.find((p) => p.href === "#ag-total")?.message} isRequired description="Tranches must add up to this." />
              <Select id="ag-currency" label="Currency" value={draft.currency} options={CURRENCIES} onChange={(v) => set({ currency: v })} />
            </div>
            <div className={layout.grid2} style={{ marginTop: "var(--space-4)" }}>
              <RadioGroup
                label="How tranches are paid"
                value={draft.settlement_mode}
                onChange={(v) => set({ settlement_mode: v })}
                options={[
                  { id: "escrow_release", label: MODE_LABEL.escrow_release, description: "You fund each tranche as a PayPal hold; it's captured when it verifies. Funds stay in your account until then." },
                  { id: "invoice_on_verify", label: MODE_LABEL.invoice_on_verify, description: "Nothing is funded up front; a verified tranche sends you a PayPal invoice." },
                ]}
              />
              <div className={layout.stack}>
                <NumberField label="Auto-release threshold" percent minValue={0.5} maxValue={1} step={0.01} value={draft.auto_release_threshold} onChange={(v) => set({ auto_release_threshold: v ?? 0.85 })} description="If you turn on auto-release when signing, verdicts at or above this confidence settle without a person. Anything below waits for review." />
                <NumberField
                  label="Dispute window"
                  minValue={draft.deliverable_release === "on_capture" && draft.settlement_mode === "escrow_release" ? 1 : 0}
                  maxValue={14}
                  step={1}
                  value={draft.dispute_window_days ?? 0}
                  onChange={(v) => set({ dispute_window_days: v ?? 0 })}
                  description="Days a released payment waits before it reaches the payee, during which you or a reviewer can contest it. 0 pays at once."
                  isDisabled={draft.settlement_mode !== "escrow_release"}
                />
                <RadioGroup
                  label="When you see the work"
                  value={draft.deliverable_release}
                  onChange={(v) =>
                    set({
                      deliverable_release: v,
                      // You see the work at capture, so you need time to check it before the payee is paid.
                      ...(v === "on_capture" && draft.settlement_mode === "escrow_release" && !draft.dispute_window_days ? { dispute_window_days: 3 } : {}),
                    })
                  }
                  options={[
                    { id: "on_submit", label: RELEASE_LABEL.on_submit, description: "You can open the payee's evidence as it arrives. Usual for grants." },
                    { id: "on_capture", label: RELEASE_LABEL.on_capture, description: "Full evidence opens once the tranche is paid, so you pay on the verdict. Usual for SOWs, where the work is the product." },
                  ]}
                />
                {draft.deliverable_release === "on_capture" && (
                  <Select
                    label="Before paying, you see at least"
                    description="The payee picks per item and can show more. A dispute window of at least a day lets you check the work after it opens."
                    value={draft.min_evidence_visibility}
                    options={VISIBILITY_ORDER.map((id) => ({ id, label: VISIBILITY_LABEL[id].label }))}
                    onChange={(v) => set({ min_evidence_visibility: v })}
                  />
                )}
                {draft.deliverable_release === "on_capture" && (
                  <p style={{ fontSize: "var(--text-sm)", color: "var(--text-secondary)", margin: 0 }}>
                    With auto-release on, anything the model judges gets a second, skeptical check that looks for reasons it isn't met. Both must agree before money moves without a person.
                  </p>
                )}
                {draft.deliverable_release === "on_capture" && draft.reviewer_emails.filter(Boolean).length === 0 && (
                  <Callout tone="warning" title="Consider a reviewer">
                    <p>If you also turn on auto-release when signing, you'd pay on the model's verdict alone. A reviewer can see the evidence for you.</p>
                  </Callout>
                )}
                <Switch isSelected={draft.require_reviewer_approval} onChange={(v) => set({ require_reviewer_approval: v })} hint="Only an invited reviewer's approval can release a tranche in review. You can still reject.">
                  Require a reviewer's approval
                </Switch>
              </div>
            </div>
          </Sheet>
          <section aria-labelledby="tranches-h" className={layout.stack}>
            <h2 id="tranches-h">Tranches and criteria</h2>
            <p style={{ color: "var(--text-secondary)" }}>Every criterion names what's counted and the target, so “done” is never a judgement call. The lint beside each one is the same check that blocks signing.</p>
            <TranchesEditor tranches={draft.milestones} onChange={(ms) => set({ milestones: ms })} currency={draft.currency} total={draft.total_amount} lint={report} lintPending={pending} problems={problems} provenance={provenance} />
          </section>
          <Nav onBack={() => go(0)} onNext={() => go(2)} nextLabel="Next: people" />
        </div>
      )}

      {step === 2 && (
        <div className={layout.stackLg}>
          <Sheet title="Who's on the other side">
            <div className={layout.stack}>
              <TextField
                id="ag-counterparty"
                label="Payee's email"
                type="email"
                description="The person who'll accept for the payee organization. They sign in with this email, choose or create their organization, and give its PayPal account."
                value={draft.counterparty_email}
                onChange={(v) => set({ counterparty_email: v })}
                error={problems.find((p) => p.href === "#ag-counterparty")?.message}
                isRequired
              />
              <fieldset style={{ border: 0, padding: 0, margin: 0 }} className={layout.stack}>
                <legend style={{ fontWeight: 700, marginBottom: "var(--space-1)" }}>Reviewers <span style={{ fontWeight: 400, color: "var(--text-secondary)" }}>(optional)</span></legend>
                <p style={{ color: "var(--text-secondary)", fontSize: "var(--text-sm)", margin: 0 }}>Third parties who sign off on the criteria and can approve or reject verdicts. They never move money. The agreement activates only once every reviewer has joined and signed.</p>
                {draft.reviewer_emails.map((e, i) => (
                  <div key={i} className={layout.row} style={{ alignItems: "flex-end" }}>
                    <TextField id={`ag-reviewer-${i}`} className="grow" label={`Reviewer ${i + 1} email`} type="email" value={e} onChange={(v) => set({ reviewer_emails: draft.reviewer_emails.map((x, n) => (n === i ? v : x)) })} error={problems.find((p) => p.href === `#ag-reviewer-${i}`)?.message} />
                    <Button variant="quiet" icon="trash" onPress={() => set({ reviewer_emails: draft.reviewer_emails.filter((_, n) => n !== i) })}>Remove</Button>
                  </div>
                ))}
                <div><Button icon="plus" onPress={() => set({ reviewer_emails: [...draft.reviewer_emails, ""] })}>Add a reviewer</Button></div>
              </fieldset>
            </div>
          </Sheet>
          <Nav onBack={() => go(1)} onNext={() => go(3)} nextLabel="Next: review" />
        </div>
      )}

      {step === 3 && (
        <div className={layout.stackLg}>
          <Sheet title="Before you send">
            <Facts
              items={[
                ["Title", draft.title],
                ["Total", money(draft.total_amount, draft.currency)],
                ["Paid by", MODE_LABEL[draft.settlement_mode]],
                ["Tranches", `${draft.milestones.length} with ${draft.milestones.reduce((n, m) => n + m.criteria.length, 0)} criteria`],
                ["Auto-release", `Off until you sign; threshold ${percent(draft.auto_release_threshold)}`],
                ["Dispute window", draft.settlement_mode === "escrow_release" && draft.dispute_window_days ? `${draft.dispute_window_days} day${draft.dispute_window_days === 1 ? "" : "s"} after each release` : "None: released payments go straight to the payee"],
                ["Seeing the work", draft.deliverable_release === "on_capture" ? `Once each tranche is paid: you pay on the verdict (before that: ${VISIBILITY_LABEL[draft.min_evidence_visibility].label.toLowerCase()})` : "As the payee submits it"],
                ["Payee invite", draft.counterparty_email],
                ["Reviewers", draft.reviewer_emails.filter(Boolean).join(", ") || "None"],
              ]}
            />
          </Sheet>
          {report && (
            <Callout tone={report.ok ? "success" : "warning"} title={report.ok ? "Criteria lint is clear" : "Lint errors will block signing"}>
              <p>{report.ok ? "Nothing would stop either side signing these criteria." : `You can send this draft now, but it can't be signed until the ${report.issues?.filter((i) => i.level === "error").length} errors are fixed. Fix them now so the payee sees a clean draft.`}</p>
            </Callout>
          )}
          <Callout tone="note" title="What happens next">
            <ol style={{ margin: 0 }}>
              <li>The payee and reviewers get an email invitation.</li>
              <li>Everyone reviews the criteria. Only your organization can edit them, and any edit clears all signatures.</li>
              <li>When every party has signed the same criteria, they lock and the agreement goes live.</li>
            </ol>
          </Callout>
          <Nav onBack={() => go(2)} onNext={send} nextLabel="Create and send invitations" pending={create.isPending} disabled={!canCreate || !orgQ.data?.paypal_email} />
        </div>
      )}
    </div>
  );
}

function Nav({ onBack, onNext, nextLabel, pending, disabled }: { onBack: () => void; onNext: () => void; nextLabel: string; pending?: boolean; disabled?: boolean }) {
  return (
    <div className={layout.row} style={{ justifyContent: "space-between" }}>
      <Button variant="quiet" icon="back" onPress={onBack}>Back</Button>
      <Button variant="primary" iconEnd="arrow" onPress={onNext} isPending={pending} isDisabled={disabled}>{nextLabel}</Button>
    </div>
  );
}

function SourceStep({ onStart }: { onStart: (d: DraftForm, x: ExtractionResult | null) => void }) {
  const health = useHealth();
  const [files, setFiles] = useState<File[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const ctrl = useRef<AbortController | null>(null);

  async function run() {
    const f = files[0];
    if (!f) return;
    if (f.size > 20 * 1024 * 1024) {
      setError("Agreements can be up to 20 MB.");
      return;
    }
    setError(null);
    setBusy(true);
    ctrl.current = new AbortController();
    try {
      const x = await extractAgreement(f, ctrl.current.signal);
      toast("Draft proposed", { body: `${x.milestones.length} tranches from ${f.name}. Review every criterion before sending.` });
      onStart(draftFromExtraction(x, f.name), x);
    } catch (e) {
      if (!(e instanceof DOMException && e.name === "AbortError")) setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={layout.split}>
      <Sheet title="Start from the agreement document">
        <div className={layout.stack}>
          <p>Upload the grant agreement or SOW. The engine proposes tranches and checkable criteria, quotes the text each came from, and flags anything a reviewer couldn't verify.</p>
          <FileDropZone label="Drop the agreement here" hint="PDF, plain text or Markdown, up to 20 MB." accept={DOC_TYPES} files={files} onFiles={setFiles} onRemove={() => setFiles([])} isDisabled={busy} />
          {error && <Callout tone="error" title="Couldn't read the document" live>{error}</Callout>}
          {busy && <Callout tone="info" live title="Reading the agreement">This can take up to a minute with a long document.</Callout>}
          <div className={layout.row}>
            <Button variant="primary" icon="arrow" onPress={run} isDisabled={!files.length} isPending={busy}>Propose tranches and criteria</Button>
            {busy && <Button variant="quiet" onPress={() => ctrl.current?.abort()}>Cancel</Button>}
          </div>
        </div>
      </Sheet>
      <div className={layout.stack}>
        <Sheet title="Or write it yourself" titleLevel={3}>
          <p>Start with one empty tranche and add criteria as you go.</p>
          <Button onPress={() => onStart(blankDraft(), null)}>Start a blank draft</Button>
        </Sheet>
        {hasDemo(health.data) && (
          <Sheet title="Sample grant" titleLevel={3}>
            <p>A two-tranche literacy grant with clean criteria, for trying the flow.</p>
            <Button
              onPress={() =>
                onStart({ ...blankDraft(), title: DEMO_GRANT.title, total_amount: String(DEMO_GRANT.total_amount), currency: DEMO_GRANT.currency ?? "USD", milestones: tranchesFrom(DEMO_GRANT.milestones) }, null)
              }
            >
              Use the sample grant
            </Button>
          </Sheet>
        )}
      </div>
    </div>
  );
}

function ExtractionNotes({ x, answered, setAnswered }: { x: ExtractionResult; answered: number[]; setAnswered: (a: number[]) => void }) {
  return (
    <Sheet title="What the document left open" id="open-questions">
      <div className={layout.stack}>
        <p style={{ color: "var(--text-secondary)", margin: 0 }}>
          Proposed by <code>{x.model}</code>. Quotes from the document sit beside each criterion. Nothing is final until both sides sign.
        </p>
        {x.open_questions.length > 0 ? (
          <fieldset style={{ border: 0, padding: 0, margin: 0 }} className={layout.stack}>
            <legend style={{ fontWeight: 700 }}>Open questions: tick each once it's settled in the criteria</legend>
            {x.open_questions.map((q, i) => (
              <Checkbox key={i} isSelected={answered.includes(i)} onChange={(v) => setAnswered(v ? [...answered, i] : answered.filter((n) => n !== i))}>{q}</Checkbox>
            ))}
          </fieldset>
        ) : (
          <p>No open questions.</p>
        )}
        {x.problems.length > 0 && (
          <Callout tone="warning" title="Proposals that were left out">
            <ul style={{ margin: 0 }}>{x.problems.map((p, i) => <li key={i}><code>{p.path}</code>: {p.message}</li>)}</ul>
          </Callout>
        )}
      </div>
    </Sheet>
  );
}

function Created({ created, orgId }: { created: AgreementCreated; orgId: string }) {
  const a = created.agreement;
  return (
    <div className={layout.stackLg}>
      <PageHeader eyebrow="Draft created" title={a.title} stamp={<Stamp spec={{ label: "Draft", tone: "neutral", glyph: "ring", meaning: "Criteria can still change." }} size="lg" />} lede="Invitations are out. The agreement goes live when the payee and every reviewer have joined and everyone has signed the same criteria." />
      <Sheet title="Invitations sent">
        <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: "var(--space-3)" }}>
          {created.invitations.map((i) => (
            <li key={i.id} className={layout.row} style={{ justifyContent: "space-between", borderBottom: "1px solid var(--rule-hairline)", paddingBottom: "var(--space-2)" }}>
              <span><strong>{i.email}</strong> <span style={{ color: "var(--text-secondary)" }}>· {i.kind === "counterparty" ? "payee" : "reviewer"}</span></span>
            </li>
          ))}
        </ul>
        <p style={{ color: "var(--text-secondary)", fontSize: "var(--text-sm)", marginTop: "var(--space-3)" }}>Each person gets an email with their link. They sign in with that address and accept for their organization, or create one. Pending invitations also wait on their home page.</p>
      </Sheet>
      <div className={layout.row}>
        <ButtonLink variant="primary" href={paths.agreement(orgId, a.id)} iconEnd="arrow">Open the agreement</ButtonLink>
        <ButtonLink href={paths.org(orgId)}>Back to the ledger</ButtonLink>
      </div>
    </div>
  );
}
