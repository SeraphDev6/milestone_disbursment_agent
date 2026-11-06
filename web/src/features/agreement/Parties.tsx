import { useMemo, useState, type FormEvent } from "react";
import type { ColDef } from "ag-grid-community";
import { useInvitations, useInvite, useRevokeInvitation } from "@/api/queries";
import type { InvitationRead } from "@/api/types";
import { errorMessage } from "@/api/errors";
import { useAgreementCtx, useOrgCtx } from "@/app/context";
import { useTitle } from "@/app/RouteAnnouncer";
import { date, ROLE_LABEL } from "@/domain/format";
import { can } from "@/domain/permissions";
import { INVITATION_STAMPS, invitationState } from "@/domain/status";
import { Button, Callout, ConfirmDialog, DataGrid, ErrorSummary, layout, PermissionNote, RadioGroup, Sheet, Stamp, TextField, toast } from "@/design/components";
import { SignatureBlock } from "./parts";

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

export function Parties() {
  const { agreement: a, seat } = useAgreementCtx();
  const org = useOrgCtx();
  useTitle("Parties", a.title);
  const invitations = useInvitations(a.id, org.orgId);
  const revoke = useRevokeInvitation(a.id, org.orgId);
  const [revoking, setRevoking] = useState<InvitationRead | null>(null);
  const mayInvite = can(seat, "invite");
  const closed = a.status === "completed" || a.status === "cancelled";

  const columns = useMemo<ColDef<InvitationRead>[]>(
    () => [
      { headerName: "Email", field: "email", flex: 1.5, minWidth: 200 },
      { headerName: "As", field: "kind", width: 120, valueFormatter: (p) => (p.value === "counterparty" ? "Payee" : "Reviewer") },
      { headerName: "Status", width: 140, cellRenderer: (p: { data: InvitationRead }) => <Stamp spec={INVITATION_STAMPS[invitationState(p.data)]} flat /> },
      { headerName: "Expires", field: "expires_at", width: 140, valueFormatter: (p) => date(p.value) },
      {
        headerName: "",
        width: 130,
        sortable: false,
        cellRenderer: (p: { data: InvitationRead }) =>
          mayInvite.ok && invitationState(p.data) === "pending" ? (
            <Button size="sm" variant="quiet" icon="close" onPress={() => setRevoking(p.data)}>Revoke<span className="sr-only"> invitation for {p.data.email}</span></Button>
          ) : null,
      },
    ],
    [mayInvite.ok],
  );

  return (
    <div className={layout.split}>
      <div className={layout.stackLg}>
        <Sheet title="Parties">
          <SignatureBlock a={a} />
        </Sheet>
        <DataGrid<InvitationRead>
          caption="Invitations"
          summary={`${invitations.data?.length ?? 0} invitations on this agreement.`}
          rows={invitations.data}
          columns={columns}
          rowId={(r) => r.id}
          loading={invitations.isPending}
          emptyText="No invitations."
        />
      </div>
      <div className={layout.stack}>
        {closed ? (
          <Callout tone="note">This agreement is {a.status}; no one else can join.</Callout>
        ) : mayInvite.ok ? (
          <InviteForm />
        ) : (
          <Sheet title="Invite someone" titleLevel={2} tight><PermissionNote>{mayInvite.reason}</PermissionNote></Sheet>
        )}
        <Callout tone="note" title="Roles on this agreement">
          <ul style={{ margin: 0 }}>
            <li><strong>{ROLE_LABEL.payer}</strong> drafts the criteria, funds, reviews and releases.</li>
            <li><strong>{ROLE_LABEL.payee}</strong> submits evidence and gets paid; can never approve their own tranche.</li>
            <li><strong>{ROLE_LABEL.reviewer}</strong> signs the criteria and reviews verdicts; never moves money.</li>
          </ul>
        </Callout>
      </div>
      <ConfirmDialog
        isOpen={!!revoking}
        onOpenChange={(o) => !o && setRevoking(null)}
        title="Revoke this invitation?"
        summary={<p style={{ margin: 0 }}>{revoking?.email} won't be able to join with the link they were sent.</p>}
        confirmLabel="Revoke"
        confirmVariant="danger"
        isPending={revoke.isPending}
        onConfirm={async () => {
          if (!revoking) return;
          try {
            await revoke.mutateAsync(revoking.id);
            toast("Invitation revoked", { body: revoking.email });
          } catch (e) {
            toast("Couldn't revoke", { body: errorMessage(e), tone: "error" });
          }
          setRevoking(null);
        }}
      />
    </div>
  );
}

function InviteForm() {
  const { agreement: a } = useAgreementCtx();
  const org = useOrgCtx();
  const invite = useInvite(a.id, org.orgId);
  const hasPayee = a.parties.some((p) => p.role === "payee");
  const [email, setEmail] = useState("");
  const [kind, setKind] = useState<"reviewer" | "counterparty">("reviewer");
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!EMAIL.test(email.trim())) return setError("Enter an email like name@example.org");
    setError(null);
    try {
      const r = await invite.mutateAsync({ email: email.trim(), kind });
      setEmail("");
      toast("Invitation sent", { body: r.invitation.email });
    } catch (err) {
      setError(errorMessage(err));
    }
  }
  return (
    <Sheet title="Invite someone" titleLevel={2} tight>
      <form onSubmit={submit} noValidate className={layout.stack}>
        <ErrorSummary problems={error ? [{ message: error, href: "#invite-email" }] : []} />
        <RadioGroup
          label="As"
          value={kind}
          onChange={setKind}
          options={[
            { id: "reviewer", label: "Reviewer", description: "Signs the criteria; can approve or reject verdicts." },
            { id: "counterparty", label: "Payee", description: hasPayee ? "This agreement already has a payee." : "Accepts for the organization that gets paid.", isDisabled: hasPayee },
          ]}
        />
        <TextField id="invite-email" label="Email" type="email" value={email} onChange={setEmail} error={error} />
        <p style={{ fontSize: "var(--text-sm)", color: "var(--text-secondary)", margin: 0 }}>A new party has to sign too, so inviting someone holds activation until they join and sign.</p>
        <div><Button type="submit" variant="primary" isPending={invite.isPending}>Send invitation</Button></div>
      </form>
    </Sheet>
  );
}
