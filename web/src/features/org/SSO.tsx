import { useState, type FormEvent } from "react";
import { useSSO, useSSOActions } from "@/api/queries";
import type { OrgRole } from "@/api/types";
import { errorMessage } from "@/api/errors";
import { useOrgCtx } from "@/app/context";
import { atLeast } from "@/domain/permissions";
import { Busy, Button, Callout, ConfirmDialog, CopyButton, Facts, layout, PermissionNote, Select, Sheet, Stamp, Switch, TextField, toast } from "@/design/components";

const ROLE_OPTIONS: { id: OrgRole; label: string }[] = [
  { id: "viewer", label: "Viewer" },
  { id: "member", label: "Member" },
  { id: "admin", label: "Admin" },
];

/** Org-configured OIDC single sign-on: provider, domains (proven by DNS), default role, enforcement. */
export function SSOSettings() {
  const org = useOrgCtx();
  const admin = atLeast(org.role, "admin");
  const q = useSSO(org.orgId, admin);
  const acts = useSSOActions(org.orgId);
  const [editing, setEditing] = useState(false);
  const [removing, setRemoving] = useState(false);

  if (!admin) return <Sheet title="Single sign-on"><PermissionNote>SSO settings need admin or above in {org.orgName}.</PermissionNote></Sheet>;
  if (q.isPending) return <Busy label="Loading SSO" />;
  if (q.isError) return <Callout tone="error" live>{q.error.message}</Callout>;
  const conn = q.data;

  return (
    <Sheet
      title="Single sign-on"
      actions={conn && !editing ? (
        <>
          <Button size="sm" icon="edit" onPress={() => setEditing(true)}>Change</Button>
          {org.role === "owner" && <Button size="sm" variant="quiet" icon="trash" onPress={() => setRemoving(true)}>Remove</Button>}
        </>
      ) : undefined}
    >
      <div className={layout.stack}>
        <p>People with your email domains sign in through your identity provider (OIDC) and join {org.orgName} on first sign-in. Domains count only after a DNS TXT record proves you own them.</p>
        {!conn || editing ? (
          <SSOForm initial={conn ?? null} onDone={() => setEditing(false)} save={acts.save} />
        ) : (
          <>
            <Facts
              items={[
                ["Issuer", <code key="i">{conn.issuer}</code>],
                ["Client ID", <code key="c">{conn.client_id}</code>],
                ["Redirect URI", <span key="r" className={layout.row}><code>{conn.redirect_uri}</code><CopyButton value={conn.redirect_uri} label="Copy redirect URI" /></span>],
                ["New people join as", conn.default_role],
                ["Sign-in links", conn.enforce ? "Refused for these domains (SSO enforced)" : "Still allowed"],
              ]}
            />
            <h3 style={{ fontSize: "var(--text-lg)", margin: 0 }}>Domains</h3>
            <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: "var(--space-3)" }}>
              {conn.domains.map((d) => (
                <li key={d.domain} style={{ borderTop: "1px solid var(--rule-hairline)", paddingTop: "var(--space-2)", display: "grid", gap: "var(--space-2)" }}>
                  <span className={layout.row}>
                    <strong>{d.domain}</strong>
                    <Stamp flat spec={d.verified ? { label: "Verified", tone: "settled", glyph: "check", meaning: "DNS proves you own this domain." } : { label: "Unverified", tone: "review", glyph: "ring", meaning: "Add the TXT record, then check it." }} />
                  </span>
                  {!d.verified && (
                    <>
                      <span className={layout.row} style={{ fontSize: "var(--text-sm)" }}>
                        Add this TXT record at <code>{d.domain}</code>: <code style={{ overflowWrap: "anywhere" }}>{d.dns_record}</code>
                        <CopyButton value={d.dns_record} label={`Copy TXT record for ${d.domain}`} />
                      </span>
                      <div>
                        <Button
                          size="sm"
                          icon="refresh"
                          isPending={acts.verify.isPending && acts.verify.variables === d.domain}
                          onPress={() =>
                            acts.verify.mutate(d.domain, {
                              onSuccess: () => toast(`${d.domain} verified`),
                              onError: (e) => toast(`${d.domain} isn't verified yet`, { body: errorMessage(e), tone: "error" }),
                            })
                          }
                        >
                          Check DNS for {d.domain}
                        </Button>
                      </div>
                    </>
                  )}
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
      <ConfirmDialog
        isOpen={removing}
        onOpenChange={setRemoving}
        title="Remove single sign-on?"
        summary={<p style={{ margin: 0 }}>People at your domains go back to email sign-in links.</p>}
        confirmLabel="Remove SSO"
        confirmVariant="danger"
        isPending={acts.remove.isPending}
        onConfirm={async () => {
          try {
            await acts.remove.mutateAsync();
            toast("SSO removed");
          } catch (e) {
            toast("Couldn't remove SSO", { body: errorMessage(e), tone: "error" });
          }
          setRemoving(false);
        }}
      />
    </Sheet>
  );
}

function SSOForm({ initial, onDone, save }: { initial: import("@/api/queries").SSOConnection | null; onDone: () => void; save: ReturnType<typeof useSSOActions>["save"] }) {
  const [issuer, setIssuer] = useState(initial?.issuer ?? "");
  const [clientId, setClientId] = useState(initial?.client_id ?? "");
  const [secret, setSecret] = useState("");
  const [domains, setDomains] = useState(initial?.domains.map((d) => d.domain).join(", ") ?? "");
  const [role, setRole] = useState<OrgRole>(initial?.default_role ?? "member");
  const [enforce, setEnforce] = useState(initial?.enforce ?? false);
  const [error, setError] = useState<string | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    const list = domains.split(/[\s,]+/).map((d) => d.trim().toLowerCase()).filter(Boolean);
    if (!/^https:\/\//.test(issuer.trim())) return setError("The issuer is an https URL, e.g. https://login.microsoftonline.com/<tenant>/v2.0");
    if (!clientId.trim()) return setError("Enter the client ID from your identity provider");
    if (!initial && !secret.trim()) return setError("Enter the client secret");
    if (!list.length) return setError("List at least one email domain, e.g. harbor.org");
    setError(null);
    try {
      await save.mutateAsync({ issuer: issuer.trim(), client_id: clientId.trim(), client_secret: secret.trim() || null, domains: list, default_role: role, enforce });
      toast("SSO saved", { body: "Verify each domain with its DNS record next." });
      onDone();
    } catch (err) {
      setError(errorMessage(err));
    }
  }
  return (
    <form onSubmit={submit} noValidate className={layout.stack}>
      {error && <Callout tone="error" live>{error}</Callout>}
      <div className={layout.grid2}>
        <TextField label="Issuer URL" type="url" value={issuer} onChange={setIssuer} description="Your provider's OIDC issuer; we read its discovery document." isRequired />
        <TextField label="Client ID" value={clientId} onChange={setClientId} isRequired />
        <TextField label="Client secret" type="password" value={secret} onChange={setSecret} optional={!!initial} description={initial ? "Leave empty to keep the current secret. Stored encrypted." : "Stored encrypted."} isRequired={!initial} />
        <TextField label="Email domains" value={domains} onChange={setDomains} description="Comma-separated, e.g. harbor.org, harborfoundation.org. Public mail domains can't be claimed." isRequired />
        <Select label="New people join as" value={role} onChange={setRole} options={ROLE_OPTIONS} description="Never owner." />
      </div>
      <Switch isSelected={enforce} onChange={setEnforce} hint="Email sign-in links are refused for your verified domains; everyone goes through your identity provider.">Enforce SSO</Switch>
      <div className={layout.row}>
        <Button type="submit" variant="primary" isPending={save.isPending}>Save SSO</Button>
        {initial && <Button variant="quiet" onPress={onDone}>Cancel</Button>}
      </div>
    </form>
  );
}
