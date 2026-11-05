import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useLocation } from "react-router";
import type { ColDef } from "ag-grid-community";
import { useApiKeys, useCreateApiKey, useInviteMember, useMembers, useOrg, useRemoveMember, useRevokeApiKey, useUpdateMember, useUpdateOrg } from "@/api/queries";
import type { ApiKeyCreated, ApiKeyRead, MembershipRead, OrgRole } from "@/api/types";
import { errorMessage } from "@/api/errors";
import { useOrgCtx } from "@/app/context";
import { useTitle } from "@/app/RouteAnnouncer";
import { date, relative } from "@/domain/format";
import { atLeast } from "@/domain/permissions";
import { SSOSettings } from "./SSO";
import { GitHubApp } from "./GitHubApp";
import { PayPalAccount } from "./PayPalAccount";
import { Busy, Button, Callout, ConfirmDialog, CopyButton, DataGrid, layout, PageHeader, PermissionNote, Select, Sheet, TextField, toast } from "@/design/components";

const ROLES: { id: OrgRole; label: string; description: string }[] = [
  { id: "owner", label: "Owner", description: "Everything, including managing owners." },
  { id: "admin", label: "Admin", description: "Signs criteria, approves, releases, manages members and keys." },
  { id: "member", label: "Member", description: "Drafts, funds, submits evidence." },
  { id: "viewer", label: "Viewer", description: "Reads everything; changes nothing." },
];
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

export function OrgSettings() {
  const org = useOrgCtx();
  useTitle("Organization", org.orgName);
  const admin = atLeast(org.role, "admin");
  const { hash } = useLocation();
  // Deep links like /settings#paypal: bring that section into view once it has rendered.
  useEffect(() => {
    if (!hash) return;
    const t = setTimeout(() => document.getElementById(hash.slice(1))?.scrollIntoView({ block: "start" }), 300);
    return () => clearTimeout(t);
  }, [hash]);
  return (
    <div className={layout.stackLg}>
      <PageHeader eyebrow={`You're ${org.role}`} title={org.orgName} lede="Your organization's details, the people who act for it, and the API keys integrations and agents use." />
      <Details />
      <PayPalAccount />
      <GitHubApp />
      <Members />
      <SSOSettings />
      {admin ? <ApiKeys /> : <Sheet title="API keys"><PermissionNote>Managing API keys needs admin or above in {org.orgName}; you're a {org.role}.</PermissionNote></Sheet>}
    </div>
  );
}

function Details() {
  const org = useOrgCtx();
  const q = useOrg(org.orgId);
  const update = useUpdateOrg(org.orgId);
  const admin = atLeast(org.role, "admin");
  const [name, setName] = useState<string | null>(null);
  const [paypal, setPaypal] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (q.isPending) return <Busy label="Loading" />;
  if (q.isError) return <Callout tone="error" live>{q.error.message}</Callout>;
  const n = name ?? q.data.name;
  const p = paypal ?? q.data.paypal_email ?? "";
  async function save(e: FormEvent) {
    e.preventDefault();
    if (!n.trim()) return setError("The name can't be empty");
    if (p.trim() && !EMAIL.test(p.trim())) return setError("Enter the PayPal email like name@example.org");
    setError(null);
    try {
      await update.mutateAsync({ name: n.trim(), paypal_email: p.trim() || null });
      toast("Saved");
      setName(null);
      setPaypal(null);
    } catch (err) {
      setError(errorMessage(err));
    }
  }
  return (
    <Sheet title="Details">
      <form onSubmit={save} noValidate className={layout.stack}>
        {error && <Callout tone="error" live>{error}</Callout>}
        <div className={layout.grid2}>
          <TextField label="Name" value={n} onChange={setName} isDisabled={!admin} />
          <TextField label="PayPal account email" type="email" value={p} onChange={setPaypal} isDisabled={!admin} description="You fund from it as a payer and get paid into it as a payee. Agreements snapshot it when they're created or accepted, so a change applies to new agreements only." />
        </div>
        {admin ? <div><Button type="submit" variant="primary" isPending={update.isPending} isDisabled={name === null && paypal === null}>Save details</Button></div> : <PermissionNote>Only admins and owners change these.</PermissionNote>}
      </form>
    </Sheet>
  );
}

function Members() {
  const org = useOrgCtx();
  const q = useMembers(org.orgId);
  const updateM = useUpdateMember(org.orgId);
  const remove = useRemoveMember(org.orgId);
  const invite = useInviteMember(org.orgId);
  const admin = atLeast(org.role, "admin");
  const owner = org.role === "owner";
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<OrgRole>("member");
  const [error, setError] = useState<string | null>(null);
  const [devLink, setDevLink] = useState<string | null>(null);
  const [removing, setRemoving] = useState<MembershipRead | null>(null);

  const cols = useMemo<ColDef<MembershipRead>[]>(
    () => [
      { headerName: "Person", valueGetter: (p) => p.data?.user.name || p.data?.user.email, flex: 1.5, minWidth: 220 },
      { headerName: "Email", valueGetter: (p) => p.data?.user.email, flex: 1.5, minWidth: 220 },
      {
        headerName: "Role",
        field: "role",
        width: 200,
        cellRenderer: (p: { data: MembershipRead }) => {
          const self = p.data.user.id === org.me.user.id;
          const editable = admin && !self && (owner || p.data.role !== "owner");
          if (!editable) return <span style={{ textTransform: "capitalize" }}>{p.data.role}{self && " (you)"}</span>;
          return (
            <select
              aria-label={`Role for ${p.data.user.email}`}
              value={p.data.role}
              onChange={(e) =>
                updateM.mutate(
                  { userId: p.data.user.id, role: e.target.value as OrgRole },
                  { onSuccess: () => toast("Role changed", { body: `${p.data.user.email} is now ${e.target.value}.` }), onError: (err) => toast("Couldn't change the role", { body: errorMessage(err), tone: "error" }) },
                )
              }
              style={{ minHeight: 32, border: "1px solid var(--border-control)", background: "var(--surface-raised)", borderRadius: 2, padding: "0 var(--space-2)" }}
            >
              {ROLES.filter((r) => owner || r.id !== "owner").map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
            </select>
          );
        },
      },
      {
        headerName: "",
        width: 120,
        sortable: false,
        cellRenderer: (p: { data: MembershipRead }) =>
          admin && p.data.user.id !== org.me.user.id && (owner || p.data.role !== "owner") ? (
            <Button size="sm" variant="quiet" icon="trash" onPress={() => setRemoving(p.data)}>Remove<span className="sr-only"> {p.data.user.email}</span></Button>
          ) : null,
      },
    ],
    [admin, owner, org.me.user.id, updateM],
  );

  async function send(e: FormEvent) {
    e.preventDefault();
    if (!EMAIL.test(email.trim())) return setError("Enter an email like name@example.org");
    setError(null);
    try {
      const r = await invite.mutateAsync({ email: email.trim(), role });
      setDevLink(r.dev_link ?? null);
      toast("Invitation sent", { body: `${email.trim()} as ${role}` });
      setEmail("");
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <Sheet title="Members">
      <div className={layout.stack}>
        <DataGrid<MembershipRead> caption="People in this organization" summary={`${q.data?.length ?? 0} members.`} rows={q.data} columns={cols} rowId={(r) => r.user.id} loading={q.isPending} />
        {admin ? (
          <form onSubmit={send} noValidate className={layout.stack}>
            <h3 style={{ fontSize: "var(--text-lg)", margin: 0 }}>Invite a member</h3>
            <div className={layout.grid2}>
              <TextField label="Email" type="email" value={email} onChange={setEmail} error={error} />
              <Select label="Role" value={role} onChange={setRole} options={ROLES.filter((r) => owner || r.id !== "owner")} />
            </div>
            <div><Button type="submit" variant="primary" isPending={invite.isPending}>Send invitation</Button></div>
            {devLink && (
              <Callout tone="info" title="Mock mode: invitation link" live>
                <CopyButton value={devLink} label="Copy invitation link">Copy link</CopyButton>
              </Callout>
            )}
          </form>
        ) : (
          <PermissionNote>Inviting and managing members needs admin or above.</PermissionNote>
        )}
      </div>
      <ConfirmDialog
        isOpen={!!removing}
        onOpenChange={(o) => !o && setRemoving(null)}
        title="Remove this member?"
        summary={<p style={{ margin: 0 }}>{removing?.user.email} will lose access to everything {org.orgName} is party to.</p>}
        confirmLabel="Remove"
        confirmVariant="danger"
        isPending={remove.isPending}
        onConfirm={async () => {
          if (!removing) return;
          try {
            await remove.mutateAsync(removing.user.id);
            toast("Member removed", { body: removing.user.email });
          } catch (e) {
            toast("Couldn't remove them", { body: errorMessage(e), tone: "error" });
          }
          setRemoving(null);
        }}
      />
    </Sheet>
  );
}

function ApiKeys() {
  const org = useOrgCtx();
  const q = useApiKeys(org.orgId, true);
  const create = useCreateApiKey(org.orgId);
  const revoke = useRevokeApiKey(org.orgId);
  const [name, setName] = useState("");
  const [role, setRole] = useState<OrgRole>("member");
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);
  const [revoking, setRevoking] = useState<ApiKeyRead | null>(null);
  const cols = useMemo<ColDef<ApiKeyRead>[]>(
    () => [
      { headerName: "Name", field: "name", flex: 1.5, minWidth: 200 },
      { headerName: "Acts as", field: "org_role", width: 120 },
      { headerName: "Created", field: "created_at", width: 140, valueFormatter: (p) => date(p.value) },
      { headerName: "Last used", field: "last_used_at", width: 150, valueFormatter: (p) => (p.value ? relative(p.value) : "Never") },
      { headerName: "State", field: "revoked_at", width: 120, valueFormatter: (p) => (p.value ? "Revoked" : "Active") },
      { headerName: "", width: 120, sortable: false, cellRenderer: (p: { data: ApiKeyRead }) => (!p.data.revoked_at ? <Button size="sm" variant="quiet" icon="close" onPress={() => setRevoking(p.data)}>Revoke<span className="sr-only"> {p.data.name}</span></Button> : null) },
    ],
    [],
  );
  const roles = ROLES.filter((r) => r.id !== "owner" && atLeast(org.role, r.id));
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return setError("Say what the key is for");
    setError(null);
    try {
      setCreated(await create.mutateAsync({ name: name.trim(), org_role: role }));
      setName("");
    } catch (err) {
      setError(errorMessage(err));
    }
  }
  return (
    <Sheet title="API keys">
      <div className={layout.stack}>
        <p>Keys act as {org.orgName} with the role you give them, for integrations and MCP agents. Send as <code>Authorization: Bearer &lt;key&gt;</code>. A key can't have more rights than you, and never owner.</p>
        <form onSubmit={submit} noValidate className={layout.row} style={{ alignItems: "flex-end" }}>
          <TextField label="What it's for" placeholder="grant ops agent" value={name} onChange={setName} error={error} />
          <Select label="Acts as" value={role} onChange={setRole} options={roles} />
          <Button type="submit" variant="primary" icon="key" isPending={create.isPending}>Create key</Button>
        </form>
        {created && (
          <Callout tone="warning" title="Copy the key now" live>
            <p>It's shown once. Store it in your secrets manager.</p>
            <span className={layout.row}><code style={{ overflowWrap: "anywhere" }}>{created.token}</code><CopyButton value={created.token} label="Copy API key" /></span>
          </Callout>
        )}
        <DataGrid<ApiKeyRead> caption="Keys" summary={`${q.data?.length ?? 0} keys.`} rows={q.data} columns={cols} rowId={(r) => r.id} loading={q.isPending} emptyText="No keys yet." />
      </div>
      <ConfirmDialog
        isOpen={!!revoking}
        onOpenChange={(o) => !o && setRevoking(null)}
        title="Revoke this key?"
        summary={<p style={{ margin: 0 }}>Anything using “{revoking?.name}” stops working immediately.</p>}
        confirmLabel="Revoke"
        confirmVariant="danger"
        isPending={revoke.isPending}
        onConfirm={async () => {
          if (!revoking) return;
          try {
            await revoke.mutateAsync(revoking.id);
            toast("Key revoked");
          } catch (e) {
            toast("Couldn't revoke it", { body: errorMessage(e), tone: "error" });
          }
          setRevoking(null);
        }}
      />
    </Sheet>
  );
}
