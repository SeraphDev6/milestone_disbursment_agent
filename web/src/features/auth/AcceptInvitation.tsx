import { useMemo, useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate, useSearchParams } from "react-router";
import { useAcceptInvitation, useMe } from "@/api/queries";
import { errorMessage, isApiError } from "@/api/errors";
import { sessions, useActiveSession, useSessions } from "@/auth/session";
import { paths } from "@/app/context";
import { useTitle } from "@/app/RouteAnnouncer";
import { atLeast } from "@/domain/permissions";
import { Busy, Button, ButtonLink, Callout, ErrorSummary, layout, PageHeader, RadioGroup, Sheet, TextField, toast, type Problem } from "@/design/components";

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

/**
 * /invitations/accept?token=…[&email=…&kind=…&title=…]
 * The extra parameters are added by in-app links (the inviter knows them); emailed links carry only the
 * token, and /me fills in what it can.
 */
export function AcceptInvitation() {
  useTitle("Accept invitation");
  const [params] = useSearchParams();
  const loc = useLocation();
  const navigate = useNavigate();
  const session = useActiveSession();
  const { sessions: all } = useSessions();
  const token = params.get("token") ?? "";
  const invitationId = params.get("id") ?? "";
  const invitedEmail = params.get("email")?.toLowerCase() ?? null;
  const here = loc.pathname + loc.search;

  if (!token && !invitationId) return <Callout tone="error" title="This invitation link is incomplete">Open the full link from the invitation email.</Callout>;
  if (!session) return <Navigate to={`/sign-in?returnTo=${encodeURIComponent(here)}${invitedEmail ? `&email=${encodeURIComponent(invitedEmail)}` : ""}`} replace />;

  if (invitedEmail && session.user.email.toLowerCase() !== invitedEmail) {
    const match = all.find((s) => s.user.email.toLowerCase() === invitedEmail);
    return (
      <div className={layout.stack}>
        <PageHeader eyebrow="Invitation" title={`This invitation is for ${invitedEmail}`} lede={`You're signed in as ${session.user.email}. Invitations can only be accepted by the email they were sent to.`} />
        <div className={layout.row}>
          {match ? (
            <Button variant="primary" onPress={() => { sessions.switchTo(match.user.id); navigate(here, { replace: true }); }}>Switch to {invitedEmail}</Button>
          ) : (
            <ButtonLink variant="primary" href={`/sign-in?add=1&email=${encodeURIComponent(invitedEmail)}&returnTo=${encodeURIComponent(here)}`}>Sign in as {invitedEmail}</ButtonLink>
          )}
        </div>
      </div>
    );
  }
  return <AcceptForm token={token} invitationId={invitationId} hintKind={params.get("kind")} hintTitle={params.get("title")} />;
}

function AcceptForm({ token, invitationId, hintKind, hintTitle }: { token: string; invitationId: string; hintKind: string | null; hintTitle: string | null }) {
  const me = useMe();
  const accept = useAcceptInvitation();
  const navigate = useNavigate();
  const [choice, setChoice] = useState<string>("__new");
  const [name, setName] = useState("");
  const [paypal, setPaypal] = useState("");
  const [problems, setProblems] = useState<Problem[]>([]);
  const [attempt, setAttempt] = useState(0);

  const pending = useMemo(() => me.data?.invitations ?? [], [me.data]);
  const guess = useMemo(
    () => pending.find((i) => i.id === invitationId) ?? (pending.length === 1 ? pending[0] : pending.find((i) => i.agreement_title === hintTitle)),
    [pending, hintTitle, invitationId],
  );
  const kind = (hintKind ?? guess?.kind ?? null) as "counterparty" | "reviewer" | "member" | null;
  const title = hintTitle ?? guess?.agreement_title ?? null;
  const eligible = (me.data?.memberships ?? []).filter((m) => atLeast(m.role, "admin"));
  const needsPaypal = kind === "counterparty";

  if (me.isPending) return <Busy label="Loading your organizations" />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    const p: Problem[] = [];
    if (kind !== "member" && choice === "__new") {
      if (!name.trim()) p.push({ message: "Enter your organization's name", href: "#acc-name" });
      if (needsPaypal && !EMAIL.test(paypal.trim())) p.push({ message: "Enter the PayPal email you'll be paid into", href: "#acc-paypal" });
    }
    setProblems(p);
    setAttempt((n) => n + 1);
    if (p.length) return;
    try {
      const r = await accept.mutateAsync(
        kind === "member"
          ? { token, id: invitationId || undefined }
          : choice === "__new"
            ? { token, id: invitationId || undefined, new_org: { name: name.trim(), paypal_email: paypal.trim() || null } }
            : { token, id: invitationId || undefined, org_id: choice },
      );
      toast("Invitation accepted", { body: `You're in as ${r.organization.name}.` });
      const aid = r.invitation.agreement_id;
      navigate(aid ? paths.agreement(r.organization.id, aid) : paths.org(r.organization.id), { replace: true });
    } catch (err) {
      const msg = errorMessage(err);
      const paypalMissing = isApiError(err) && err.status === 422 && /paypal/i.test(msg);
      setProblems([{ message: paypalMissing ? `${msg}. Choose "A new organization" and give its PayPal email, or add one in that organization's settings.` : msg }]);
    }
  }

  const what = kind === "counterparty" ? "receive payments under" : kind === "reviewer" ? "review" : kind === "member" ? "join" : "join";
  return (
    <div className={layout.stackLg}>
      <PageHeader
        eyebrow="Invitation"
        title={title ? `You're invited to ${what} “${title}”` : "Accept your invitation"}
        lede={
          kind === "counterparty"
            ? "You'll be the payee: you'll review the criteria, submit evidence, and get paid through PayPal when each tranche verifies."
            : kind === "reviewer"
              ? "As a reviewer you sign off on the criteria and can approve or reject verdicts. Reviewers never move money."
              : "Choose which of your organizations accepts it."
        }
      />
      <Sheet>
        <form onSubmit={submit} noValidate className={layout.stack}>
          <ErrorSummary problems={problems} focusKey={attempt} />
          {kind !== "member" && (
            <RadioGroup
              label="Accept for"
              description="Agreements bind an organization, so you need to be an admin or owner of the one you choose."
              value={choice}
              onChange={setChoice}
              options={[
                ...eligible.map((m) => ({ id: m.org_id, label: m.org_name, description: `You're ${m.role}` })),
                { id: "__new", label: "A new organization", description: "Create it now; you become its owner." },
              ]}
            />
          )}
          {kind !== "member" && choice === "__new" && (
            <div className={layout.grid2}>
              <TextField id="acc-name" label="Organization name" value={name} onChange={setName} error={problems.find((p) => p.href === "#acc-name")?.message} isRequired />
              <TextField
                id="acc-paypal"
                label="PayPal account email"
                optional={!needsPaypal}
                isRequired={needsPaypal}
                type="email"
                description={needsPaypal ? "Tranches are paid into this account." : "Only needed if this organization pays or gets paid."}
                value={paypal}
                onChange={setPaypal}
                error={problems.find((p) => p.href === "#acc-paypal")?.message}
              />
            </div>
          )}
          <div className={layout.row}>
            <Button type="submit" variant="primary" isPending={accept.isPending} icon="check">Accept invitation</Button>
            <ButtonLink href="/" variant="quiet">Not now</ButtonLink>
          </div>
        </form>
      </Sheet>
    </div>
  );
}
