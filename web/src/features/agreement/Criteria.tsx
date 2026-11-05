import { useEffect, useState } from "react";
import { useConfirm, useReplaceMilestones } from "@/api/queries";
import { errorMessage, isApiError } from "@/api/errors";
import type { LintReport } from "@/api/types";
import { useAgreementCtx, useOrgCtx } from "@/app/context";
import { useTitle } from "@/app/RouteAnnouncer";
import { can } from "@/domain/permissions";
import { date, money, percent } from "@/domain/format";
import { fieldId } from "@/domain/lint-paths";
import { Button, Callout, Checkbox, ConfirmDialog, ErrorSummary, layout, PermissionNote, Seal, Sheet, Switch, toast, type Problem } from "@/design/components";
import { TranchesEditor } from "@/features/draft/TranchesEditor";
import { tranchesFrom, tranchesOut, tranchesProblems, sumAmounts, type TrancheForm } from "@/features/draft/model";
import { useLiveLint } from "@/features/draft/useLiveLint";
import { ActivationChecklist, CriteriaList, SignatureBlock } from "./parts";

export function Criteria() {
  const { agreement: a, seat, party } = useAgreementCtx();
  const org = useOrgCtx();
  useTitle("Criteria", a.title);
  const [editing, setEditing] = useState(false);
  // Lives here, not in SignPanel: the panel remounts when the criteria hash changes, which is exactly when this shows.
  const [staleNotice, setStaleNotice] = useState(false);
  const editable = (a.status === "draft" || a.status === "awaiting_confirmation") && !a.locked;
  const mayEdit = can(seat, "edit_tranches");

  if (editing) return <EditCriteria onDone={() => setEditing(false)} />;

  return (
    <div className={layout.split}>
      <div className={layout.stackLg}>
        {a.locked && (
          <Callout tone="success" title="Criteria are locked">
            <p>Every party signed this exact version. Changing it would need a new agreement.</p>
          </Callout>
        )}
        {!a.lint.ok && !a.locked && (
          <Callout tone="error" title="These criteria can't be signed yet">
            <p>The lint found {a.lint.issues.filter((i) => i.level === "error").length} problems that would make a tranche impossible to verify. {mayEdit.ok ? "Edit the criteria to fix them." : "The payer needs to fix them."}</p>
          </Callout>
        )}
        {a.milestones.map((m, i) => (
          <Sheet key={m.id} title={`№${i + 1} · ${m.title}`} actions={<span className="figures">{money(m.amount, a.currency)}{m.due_date && ` · due ${date(m.due_date)}`}</span>}>
            <CriteriaList m={m} index={i} issues={a.lint.issues} />
          </Sheet>
        ))}
        {editable && (
          <div className={layout.stack}>
            {mayEdit.ok ? (
              <div><Button icon="edit" onPress={() => setEditing(true)}>Edit tranches and criteria</Button></div>
            ) : (
              <PermissionNote>{mayEdit.reason} Ask the payer for changes before you sign.</PermissionNote>
            )}
          </div>
        )}
      </div>
      <div className={layout.stack}>
        {editable && <SignPanel key={a.criteria_hash} stale={staleNotice} onStale={() => setStaleNotice(true)} onSigned={() => setStaleNotice(false)} />}
        <Sheet title="Signatures" titleLevel={2} tight>
          <SignatureBlock a={a} />
        </Sheet>
        {!a.locked && (
          <Sheet title="To go live" titleLevel={2} tight>
            <ActivationChecklist a={a} />
          </Sheet>
        )}
        {party.confirmed_current && editable && <Callout tone="success" title={`${org.orgName} has signed`}>Your signature stands until someone edits the criteria.</Callout>}
      </div>
    </div>
  );
}

function SignPanel({ stale, onStale, onSigned }: { stale: boolean; onStale: () => void; onSigned: () => void }) {
  const { agreement: a, seat, party } = useAgreementCtx();
  const org = useOrgCtx();
  const confirm = useConfirm(a.id, org.orgId);
  const [reviewed, setReviewed] = useState(false);
  const [autoRelease, setAutoRelease] = useState(a.auto_release_enabled);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mayConfirm = can(seat, "confirm_criteria");
  const mayAuto = can(seat, "set_auto_release");
  if (party.confirmed_current) return null;

  async function sign() {
    setError(null);
    try {
      const r = await confirm.mutateAsync({ criteria_hash: a.criteria_hash, auto_release_enabled: mayAuto.ok ? autoRelease : undefined });
      setOpen(false);
      onSigned();
      toast("Criteria signed", { body: r.status === "active" ? "Everyone has signed. The agreement is live." : `Signed for ${org.orgName}. Waiting for the others.` });
    } catch (e) {
      setOpen(false);
      if (isApiError(e) && e.status === 409 && e.currentCriteriaHash) onStale();
      else setError(errorMessage(e));
    }
  }

  return (
    <Sheet title="Sign for your organization" titleLevel={2}>
      <div className={layout.stack}>
        {stale && (
          <Callout tone="warning" title="The criteria changed while you were reading" live>
            <p>Someone edited them since this page loaded. The new version is shown now; review it again before signing.</p>
          </Callout>
        )}
        {error && <Callout tone="error" title="Couldn't sign" live>{error}</Callout>}
        <Seal hash={a.criteria_hash} label="You're signing" />
        {mayConfirm.ok ? (
          <>
            <Checkbox isSelected={reviewed} onChange={setReviewed}>
              I've read every criterion and {org.orgName} agrees to be bound by them
            </Checkbox>
            {mayAuto.ok && (
              <Switch isSelected={autoRelease} onChange={setAutoRelease} hint={`Verdicts that pass every check and reach ${percent(a.auto_release_threshold)} confidence settle without a person. Everything else waits for review.`}>
                Allow auto-release
              </Switch>
            )}
            <Button variant="primary" icon="signature" isDisabled={!reviewed || !a.lint.ok} onPress={() => setOpen(true)}>Sign the criteria</Button>
            {!a.lint.ok && <PermissionNote>Signing opens once the lint errors are fixed.</PermissionNote>}
          </>
        ) : (
          <PermissionNote>{mayConfirm.reason}</PermissionNote>
        )}
      </div>
      <ConfirmDialog
        isOpen={open}
        onOpenChange={setOpen}
        title="Sign these criteria?"
        summary={
          <>
            <p style={{ margin: 0 }}>You're signing for <strong>{org.orgName}</strong> as the {seat.role}: {a.milestones.length} tranches, {money(a.total_amount, a.currency)}.</p>
            {a.deliverable_release === "on_capture" && <p style={{ margin: "var(--space-2) 0 0" }}>The payer sees full evidence <strong>only after each tranche is paid</strong>, so payment follows the verdict.</p>}
            {mayAuto.ok && <p style={{ margin: "var(--space-2) 0 0" }}>Auto-release: <strong>{autoRelease ? `on, at ${percent(a.auto_release_threshold)}` : "off"}</strong></p>}
          </>
        }
        confirmLabel="Sign"
        onConfirm={sign}
        isPending={confirm.isPending}
      >
        <p>Once everyone has signed the same version, the criteria lock and money can move against them.</p>
      </ConfirmDialog>
    </Sheet>
  );
}

function EditCriteria({ onDone }: { onDone: () => void }) {
  const { agreement: a } = useAgreementCtx();
  const org = useOrgCtx();
  const replace = useReplaceMilestones(a.id, org.orgId);
  const [tranches, setTranches] = useState<TrancheForm[]>(() => tranchesFrom(a.milestones));
  const [total, setTotal] = useState(String(a.total_amount));
  const [problems, setProblems] = useState<Problem[]>([]);
  const [attempt, setAttempt] = useState(0);
  const [open, setOpen] = useState(false);
  const { report, pending } = useLiveLint(total, tranches);
  const signed = a.parties.filter((p) => p.confirmed_current).length;
  const sum = sumAmounts(tranches);
  useEffect(() => setTotal(String(a.total_amount)), [a.total_amount]);

  function review() {
    const p = tranchesProblems(tranches);
    setProblems(p);
    setAttempt((n) => n + 1);
    if (!p.length) setOpen(true);
  }
  async function save() {
    try {
      const newTotal = Math.abs(sum - Number(total)) >= 0.005 ? sum.toFixed(2) : null;
      await replace.mutateAsync({ milestones: tranchesOut(tranches), total_amount: newTotal });
      setOpen(false);
      toast("Criteria saved", { body: signed ? "All signatures were cleared; everyone signs again." : undefined });
      onDone();
    } catch (e) {
      setOpen(false);
      if (isApiError(e) && e.fields.length) setProblems(e.fields.map((f) => ({ message: f.message, path: f.path, href: `#${fieldId(f.path)}` })));
      else setProblems([{ message: errorMessage(e) }]);
      setAttempt((n) => n + 1);
    }
  }

  return (
    <div className={layout.stackLg}>
      <Callout tone="warning" title="Editing clears every signature">
        <p>{signed ? `${signed} part${signed === 1 ? "y has" : "ies have"} signed the current version. Saving changes the seal and asks everyone to sign again.` : "Nobody has signed yet."}</p>
      </Callout>
      <ErrorSummary problems={problems} focusKey={attempt} />
      <TranchesEditor tranches={tranches} onChange={setTranches} currency={a.currency} total={total} lint={report as LintReport | null} lintPending={pending} problems={problems} />
      {Math.abs(sum - Number(total)) >= 0.005 && <Callout tone="info">Saving will also change the agreement total to {money(sum, a.currency)}, the sum of the tranches.</Callout>}
      <div className={layout.row} style={{ justifyContent: "space-between" }}>
        <Button variant="quiet" onPress={onDone}>Cancel</Button>
        <Button variant="primary" onPress={review}>Save criteria</Button>
      </div>
      <ConfirmDialog
        isOpen={open}
        onOpenChange={setOpen}
        title="Replace the criteria?"
        summary={<p style={{ margin: 0 }}>{tranches.length} tranches, {tranches.reduce((n, t) => n + t.criteria.length, 0)} criteria, {money(sum, a.currency)}.{signed > 0 && <> <strong>{signed} signature{signed === 1 ? "" : "s"} will be cleared.</strong></>}</p>}
        confirmLabel="Save and clear signatures"
        confirmVariant={signed ? "danger" : "primary"}
        onConfirm={save}
        isPending={replace.isPending}
      />
    </div>
  );
}
