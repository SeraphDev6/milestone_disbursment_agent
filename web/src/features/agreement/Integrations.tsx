import { useMemo, useState, type FormEvent } from "react";
import type { ColDef } from "ag-grid-community";
import { useConnectGitHub, useSubscribe, useSubscriptions, useUnsubscribe } from "@/api/queries";
import type { GitHubIntegration, SubscriptionCreated, SubscriptionRead } from "@/api/types";
import { errorMessage } from "@/api/errors";
import { useAgreementCtx, useOrgCtx } from "@/app/context";
import { useTitle } from "@/app/RouteAnnouncer";
import { date } from "@/domain/format";
import { can } from "@/domain/permissions";
import { Button, Callout, ConfirmDialog, CopyButton, DataGrid, Facts, layout, PermissionNote, Sheet, TextField, toast } from "@/design/components";

export function Integrations() {
  const { agreement: a } = useAgreementCtx();
  useTitle("Integrations", a.title);
  return (
    <div className={layout.stackLg}>
      <GitHub />
      <Webhooks />
    </div>
  );
}

function GitHub() {
  const { agreement: a, seat } = useAgreementCtx();
  const org = useOrgCtx();
  const connect = useConnectGitHub(a.id, org.orgId);
  const [repo, setRepo] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<GitHubIntegration | null>(null);
  const allowed = can(seat, "connect_github");
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!/^[\w.-]+\/[\w.-]+$/.test(repo.trim())) return setError("Enter the repository as owner/name");
    setError(null);
    try {
      setResult((await connect.mutateAsync(repo.trim())) as GitHubIntegration);
      toast("GitHub connected", { body: repo.trim() });
    } catch (err) {
      setError(errorMessage(err));
    }
  }
  return (
    <Sheet title="GitHub: merged pull requests as evidence">
      <div className={layout.stack}>
        <p>A merged PR carrying a tranche's label becomes evidence for it, pinned to the merge commit, with CI results read from GitHub. It's the only way to satisfy a <code>ci_green</code> check.</p>
        {!allowed.ok ? (
          <PermissionNote>{allowed.reason}</PermissionNote>
        ) : (
          <form onSubmit={submit} noValidate className={layout.row} style={{ alignItems: "flex-end" }}>
            <TextField label="Repository" placeholder="owner/name" value={repo} onChange={setRepo} error={error} figures />
            <Button type="submit" variant="primary" icon="github" isPending={connect.isPending}>Connect</Button>
          </form>
        )}
        {result && (
          <Callout tone="success" title={`Add this webhook to ${result.repo}`} live>
            <Facts
              items={[
                ["Payload URL", <span key="u" className={layout.row}><code>{result.webhook_url}</code><CopyButton value={result.webhook_url} label="Copy payload URL" /></span>],
                ["Content type", <code key="c">{result.content_type}</code>],
                ["Secret", <span key="s" className={layout.row}><code>••••••••{result.secret.slice(-4)}</code><CopyButton value={result.secret} label="Copy webhook secret" /></span>],
                ["Events", result.events.join(", ")],
              ]}
            />
            <h3 style={{ fontSize: "var(--text-lg)", marginTop: "var(--space-4)" }}>Labels</h3>
            <ul style={{ margin: 0 }}>
              {result.labels.map((l) => (
                <li key={l.milestone_id}>
                  <code>{l.label}</code> → {l.title} <CopyButton value={l.label} label={`Copy label for ${l.title}`} />
                </li>
              ))}
            </ul>
          </Callout>
        )}
      </div>
    </Sheet>
  );
}

function Webhooks() {
  const { agreement: a, seat } = useAgreementCtx();
  const org = useOrgCtx();
  const subs = useSubscriptions(a.id, org.orgId);
  const subscribe = useSubscribe(a.id, org.orgId);
  const unsubscribe = useUnsubscribe(a.id, org.orgId);
  const [url, setUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<SubscriptionCreated | null>(null);
  const [removing, setRemoving] = useState<SubscriptionRead | null>(null);
  const allowed = can(seat, "manage_subscriptions");
  const cols = useMemo<ColDef<SubscriptionRead>[]>(
    () => [
      { headerName: "URL", field: "url", flex: 2, minWidth: 260, cellClass: "figures" },
      { headerName: "Events", valueGetter: (p) => (p.data?.events?.length ? p.data.events.join(", ") : "All"), flex: 1, minWidth: 140 },
      { headerName: "Since", field: "created_at", width: 140, valueFormatter: (p) => date(p.value) },
      { headerName: "Active", field: "active", width: 100, valueFormatter: (p) => (p.value ? "Yes" : "Removed") },
      { headerName: "", width: 120, sortable: false, cellRenderer: (p: { data: SubscriptionRead }) => (p.data.active && allowed.ok ? <Button size="sm" variant="quiet" icon="trash" onPress={() => setRemoving(p.data)}>Remove<span className="sr-only"> {p.data.url}</span></Button> : null) },
    ],
    [allowed.ok],
  );
  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      new URL(url);
    } catch {
      return setError("Enter a full URL, starting with https://");
    }
    setError(null);
    try {
      setCreated(await subscribe.mutateAsync({ url: url.trim() }));
      setUrl("");
    } catch (err) {
      setError(errorMessage(err));
    }
  }
  return (
    <Sheet title="Webhooks: send this agreement's events to your systems">
      <div className={layout.stack}>
        <p>Every event in the activity log is delivered as signed JSON, retried with backoff. Verify <code>X-MDE-Signature</code> as <code>sha256=HMAC(secret, "{"{X-MDE-Timestamp}"}.{"{body}"}")</code>.</p>
        {allowed.ok ? (
          <form onSubmit={submit} noValidate className={layout.row} style={{ alignItems: "flex-end" }}>
            <TextField label="Endpoint URL" type="url" placeholder="https://example.org/hooks/mde" value={url} onChange={setUrl} error={error} description="HTTPS, on a public address." />
            <Button type="submit" variant="primary" isPending={subscribe.isPending}>Subscribe</Button>
          </form>
        ) : (
          <PermissionNote>{allowed.reason}</PermissionNote>
        )}
        {created && (
          <Callout tone="warning" title="Copy the signing secret now" live>
            <p>It isn't shown again.</p>
            <span className={layout.row}><code>{created.secret}</code><CopyButton value={created.secret} label="Copy signing secret" /></span>
          </Callout>
        )}
        <DataGrid<SubscriptionRead> caption="Subscriptions" summary={`${subs.data?.length ?? 0} subscriptions.`} rows={subs.data} columns={cols} rowId={(r) => r.id} loading={subs.isPending} emptyText="No subscriptions." />
      </div>
      <ConfirmDialog
        isOpen={!!removing}
        onOpenChange={(o) => !o && setRemoving(null)}
        title="Stop sending events here?"
        summary={<p style={{ margin: 0 }}><code>{removing?.url}</code> stops receiving this agreement's events.</p>}
        confirmLabel="Remove"
        confirmVariant="danger"
        isPending={unsubscribe.isPending}
        onConfirm={async () => {
          if (!removing) return;
          try {
            await unsubscribe.mutateAsync(removing.id);
            toast("Subscription removed");
          } catch (e) {
            toast("Couldn't remove it", { body: errorMessage(e), tone: "error" });
          }
          setRemoving(null);
        }}
      />
    </Sheet>
  );
}
