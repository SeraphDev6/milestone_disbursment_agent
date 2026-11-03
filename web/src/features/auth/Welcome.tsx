import { useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { useCreateOrg, useMe } from "@/api/queries";
import { errorMessage } from "@/api/errors";
import { paths } from "@/app/context";
import { useTitle } from "@/app/RouteAnnouncer";
import { Button, Callout, ErrorSummary, layout, PageHeader, Sheet, TextField, toast, type Problem } from "@/design/components";
import { InvitationInbox } from "@/features/home/InvitationInbox";
import { DemoLink } from "@/features/demo/DemoLink";

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

/** Create an organization (you become its owner). Shared by onboarding and the org menu. */
export function CreateOrgForm({ onCreated, submitLabel = "Create organization" }: { onCreated: (id: string) => void; submitLabel?: string }) {
  const create = useCreateOrg();
  const [name, setName] = useState("");
  const [paypal, setPaypal] = useState("");
  const [problems, setProblems] = useState<Problem[]>([]);
  const [attempt, setAttempt] = useState(0);
  const nameErr = problems.find((p) => p.href === "#org-name")?.message;
  const paypalErr = problems.find((p) => p.href === "#org-paypal")?.message;

  async function submit(e: FormEvent) {
    e.preventDefault();
    const p: Problem[] = [];
    if (!name.trim()) p.push({ message: "Enter your organization's name", href: "#org-name" });
    if (paypal.trim() && !EMAIL.test(paypal.trim())) p.push({ message: "Enter the PayPal email like name@example.org", href: "#org-paypal" });
    setProblems(p);
    setAttempt((n) => n + 1);
    if (p.length) return;
    try {
      const org = await create.mutateAsync({ name: name.trim(), paypal_email: paypal.trim() || null });
      toast(`${org.name} created`, { body: "You're its owner." });
      onCreated(org.id);
    } catch (err) {
      setProblems([{ message: errorMessage(err) }]);
    }
  }
  return (
    <form onSubmit={submit} noValidate className={layout.stack}>
      <ErrorSummary problems={problems} focusKey={attempt} />
      <TextField id="org-name" label="Organization name" description="As it should appear on agreements and in the audit record." value={name} onChange={setName} error={nameErr} isRequired autoComplete="organization" />
      <TextField
        id="org-paypal"
        label="PayPal account email"
        optional
        type="email"
        description="Where you fund holds from as a payer, and get paid into as a payee. You'll need it before creating or accepting an agreement; you can add it later."
        value={paypal}
        onChange={setPaypal}
        error={paypalErr}
      />
      <div><Button type="submit" variant="primary" isPending={create.isPending}>{submitLabel}</Button></div>
    </form>
  );
}

export function Welcome() {
  const [params] = useSearchParams();
  const creatingAnother = params.get("new") === "1";
  useTitle(creatingAnother ? "Create an organization" : "Welcome");
  const navigate = useNavigate();
  const me = useMe();
  const invitations = me.data?.invitations ?? [];
  return (
    <div className={layout.stackLg}>
      <PageHeader
        eyebrow={me.data?.user.email}
        title={creatingAnother ? "Create an organization" : "Set up where you work"}
        lede={
          creatingAnother
            ? "Agreements are between organizations. A solo contractor is a one-person organization."
            : "Everyone acts for an organization: a funder, a grantee, a contractor, a client or a reviewer. Create yours, or accept an invitation you've been sent."
        }
      />
      <div className={layout.split}>
        <Sheet title="Create an organization">
          <CreateOrgForm onCreated={(id) => navigate(paths.org(id))} />
        </Sheet>
        <div className={layout.stack}>
          {invitations.length > 0 ? (
            <Sheet title="Invitations waiting for you" titleLevel={2}>
              <InvitationInbox invitations={invitations} />
            </Sheet>
          ) : (
            <Callout tone="note" title="Expecting an invitation?">
              <p>Open the link in the invitation email while signed in as {me.data?.user.email ?? "the invited address"}. Invitations are tied to the email they were sent to.</p>
            </Callout>
          )}
          {!creatingAnother && <DemoLink />}
        </div>
      </div>
    </div>
  );
}
