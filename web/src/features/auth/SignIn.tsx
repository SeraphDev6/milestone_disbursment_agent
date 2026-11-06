import { useState, type FormEvent } from "react";
import { useSearchParams } from "react-router";
import { hasDemo, requestMagicLink, startSSO, useHealth } from "@/api/queries";
import { errorMessage } from "@/api/errors";
import { useTitle } from "@/app/RouteAnnouncer";
import { Button, ButtonLink, Callout, Checkbox, ErrorSummary, layout, PageHeader, TextField } from "@/design/components";

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

export function SignIn() {
  const [params] = useSearchParams();
  const adding = params.get("add") === "1";
  useTitle(adding ? "Sign in another person" : "Sign in");
  const health = useHealth();
  const [email, setEmail] = useState(params.get("email") ?? "");
  const [keep, setKeep] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);
  const [sent, setSent] = useState<{ email: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const returnTo = params.get("returnTo") ?? "/";

  async function submit(e: FormEvent) {
    e.preventDefault();
    setServerError(null);
    if (!EMAIL.test(email.trim())) {
      setError("Enter an email address like name@example.org");
      setAttempt((n) => n + 1);
      return;
    }
    setError(null);
    setBusy(true);
    try {
      // Organizations with SSO sign their people in through their identity provider; everyone else gets a link.
      const sso = await startSSO(email.trim());
      if (sso) {
        try {
          sessionStorage.setItem("mde.sso", JSON.stringify({ keep, returnTo }));
        } catch {
          /* fine: defaults apply */
        }
        window.location.assign(sso);
        return;
      }
      await requestMagicLink(email.trim());
      setSent({ email: email.trim() });
    } catch (err) {
      setServerError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }


  if (sent) {
    return (
      <div className={layout.stack}>
        <PageHeader eyebrow="Sign in" title="Check your email" lede={`We sent a one-time sign-in link to ${sent.email}. It works for 15 minutes.`} />
        <Callout tone="success" title="Link sent" live>
          <p>Open it on this device. If it hasn't arrived in a few minutes, check spam or send another.</p>
          <p>Running the server yourself without an email provider? The link is in the API's log.</p>
        </Callout>
        <div className={layout.row}>
          <Button variant="quiet" icon="back" onPress={() => setSent(null)}>Use a different email</Button>
        </div>
      </div>
    );
  }

  return (
    <div className={layout.stack}>
      <PageHeader
        eyebrow={adding ? "Another person" : "Sign in"}
        title={adding ? "Sign in another person" : "Sign in to Milestone Ledger"}
        lede={adding ? "Keep your current session and add another one. Switch between people from the account menu." : "Funders, grantees, contractors and reviewers all sign in with their email. No password: your organization's single sign-on if it has one, otherwise a one-time link."}
      />
      <ErrorSummary problems={error ? [{ message: error, href: "#email" }] : []} focusKey={attempt} />
      {serverError && <Callout tone="error" title="Couldn't send the link" live>{serverError}</Callout>}
      <form onSubmit={submit} noValidate className={layout.stack}>
        <TextField id="email" label="Email" type="email" autoComplete="email" inputMode="email" value={email} onChange={setEmail} error={error} isRequired autoFocus />
        <Checkbox isSelected={keep} onChange={setKeep} hint="Uncheck on a shared computer: you'll be signed out when this tab closes.">Keep me signed in on this device</Checkbox>
        <div className={layout.row}>
          <Button type="submit" variant="primary" isPending={busy} icon="arrow">Continue</Button>
          {adding && <ButtonLink href="/" variant="quiet">Cancel</ButtonLink>}
        </div>
      </form>
      {hasDemo(health.data) && !adding && (
        <Callout tone="note" title="Trying the demo?" actions={<ButtonLink href="/demo" iconEnd="arrow">Open the demo</ButtonLink>}>
          <p>The demo page loads a complete grant with a funder, a grantee and a reviewer already signed in. No email needed.</p>
        </Callout>
      )}
    </div>
  );
}
