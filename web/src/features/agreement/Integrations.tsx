import { useMemo, useState, type FormEvent } from "react";
import type { ColDef } from "ag-grid-community";
import { useConnectGitHub, useGitHubRepos, useGitHubStatus, useSubscribe, useSubscriptions, useUnsubscribe } from "@/api/queries";
import type { GitHubIntegration, SubscriptionCreated, SubscriptionRead } from "@/api/types";
import { errorMessage } from "@/api/errors";
import { Link } from "react-router";
import { paths, useAgreementCtx, useOrgCtx } from "@/app/context";
import { useTitle } from "@/app/RouteAnnouncer";
import { date } from "@/domain/format";
import { can } from "@/domain/permissions";
import { Button, Callout, ConfirmDialog, CopyButton, DataGrid, Facts, layout, PermissionNote, RadioGroup, Select, Sheet, TextField, toast } from "@/design/components";

const OTHER = "__other__";

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
  const app = useGitHubStatus(org.orgId);
  const installed = (app.data?.installations.length ?? 0) > 0;
  const repos = useGitHubRepos(org.orgId, installed);
  const choices = repos.data ?? [];
  const [picked, setPicked] = useState<string>("");
  const [repo, setRepo] = useState("");
  const typed = !choices.length || picked === OTHER;
  const chosen = typed ? repo.trim() : picked;
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<GitHubIntegration | null>(null);
  const [handover, setHandover] = useState<"pull_request" | "archive" | "transfer">("pull_request");
  const [clientRepo, setClientRepo] = useState("");
  const [clientError, setClientError] = useState<string | null>(null);
  const allowed = can(seat, "connect_github");
  // Work withheld until capture can't be merged into the client's repo first: it stays in the payee's private repo.
  const contractor = a.deliverable_release === "on_capture";
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!/^[\w.-]+\/[\w.-]+$/.test(chosen)) return setError(typed ? "Enter the repository as owner/name" : "Choose a repository");
    setError(null);
    if (contractor && handover === "pull_request" && !/^[\w.-]+\/[\w.-]+$/.test(clientRepo.trim())) {
      return setClientError("Enter the client's repository as owner/name");
    }
    setClientError(null);
    try {
      const body = contractor
        ? { repo: chosen, mode: "contractor_repo" as const, handover, client_repo: handover === "pull_request" ? clientRepo.trim() : null }
        : chosen;
      setResult((await connect.mutateAsync(body)) as GitHubIntegration);
      toast("GitHub connected", { body: chosen });
    } catch (err) {
      setError(errorMessage(err));
    }
  }
  return (
    <Sheet title={contractor ? "GitHub: your private repo, handed over once paid" : "GitHub: merged pull requests as evidence"}>
      <div className={layout.stack}>
        {contractor ? (
          <p>
            This agreement keeps the work from the payer until the tranche is paid, so the code stays in your private repo. A release or tag named for a tranche, or a merged PR with its label, pins the commit that's verified, with CI results read from GitHub. The payer sees the commit, CI and any preview link, not the source. Once the tranche is paid, the code is handed over and checked against that commit first.
          </p>
        ) : (
          <p>A merged PR carrying a tranche's label becomes evidence for it, pinned to the merge commit, with CI results read from GitHub. It's the only way to satisfy a <code>ci_green</code> check.</p>
        )}
        {!allowed.ok ? (
          <PermissionNote>{allowed.reason}</PermissionNote>
        ) : contractor && !installed ? (
          <Callout tone="note" title="Install the GitHub App first">
            {app.data?.app_available ? (
              <p style={{ margin: 0 }}>
                A private repo is read through the app. <Link to={`${paths.settings(org.orgId)}#github`}>Install the GitHub App</Link> for {org.orgName} on the repo with the work, then come back to connect it.
              </p>
            ) : (
              <p style={{ margin: 0 }}>This server has no GitHub App, which a repo kept private until payment needs.</p>
            )}
          </Callout>
        ) : (
          <>
            {app.data?.app_available && !installed && seat.role === "payee" && (
              <p style={{ fontSize: "var(--text-sm)", margin: 0 }}>
                Skip the webhook setup: <Link to={`${paths.settings(org.orgId)}#github`}>install the GitHub App</Link> for {org.orgName}, then pick the repo here.
              </p>
            )}
            <form onSubmit={submit} noValidate className={layout.row} style={{ alignItems: "flex-end" }}>
              {choices.length > 0 && (
                <Select
                  label="Repository"
                  description="Repos your GitHub App installations can read"
                  value={picked}
                  options={[...choices.map((r) => ({ id: r.repo, label: r.repo })), ...(contractor ? [] : [{ id: OTHER, label: "Another repo (set up a webhook)" }])]}
                  onChange={(v) => setPicked(v)}
                  error={!typed ? error : undefined}
                />
              )}
              {typed && <TextField label={choices.length ? "Other repository" : "Repository"} placeholder="owner/name" value={repo} onChange={setRepo} error={error} figures />}
              {!contractor && <Button type="submit" variant="primary" icon="github" isPending={connect.isPending}>Connect</Button>}
            </form>
            {contractor && (
              <form onSubmit={submit} noValidate className={layout.stack}>
                <RadioGroup
                  label="Once a tranche is paid, hand the code over as"
                  value={handover}
                  onChange={(v) => setHandover(v as "pull_request" | "archive" | "transfer")}
                  options={[
                    { id: "pull_request", label: "A pull request into the client's repo", description: "The client installs the GitHub App on their repo. The PR has exactly the files that were verified." },
                    { id: "archive", label: "A source archive", description: "Kept here for the client to download, checked against the verified commit." },
                    { id: "transfer", label: "Transferring this repository", description: "You transfer the repo to the client's GitHub account yourself; the app only watches for it. The client gets the whole repo, including work for tranches not yet paid." },
                  ]}
                />
                {handover === "pull_request" && (
                  <TextField label="Client's repository" placeholder="owner/name" value={clientRepo} onChange={setClientRepo} error={clientError} figures description="Where the handover PR is opened." />
                )}
                <div><Button type="submit" variant="primary" icon="github" isPending={connect.isPending}>Connect</Button></div>
              </form>
            )}
          </>
        )}
        {result?.mode === "contractor_repo" && (
          <Callout tone="success" title={`${result.repo} is connected; the code stays there until paid`} live>
            <p>Publish a release, or push a tag, named with a tranche's label (a suffix like <code>-v2</code> is fine), or merge a PR carrying the label. Its commit becomes that tranche's evidence. Add a line <code>Preview: https://…</code> to the release notes to give the payer something to try.</p>
            {result.handover === "pull_request" ? (
              <p>Once paid, a PR with exactly the verified files is opened into <strong>{result.client_repo}</strong>.</p>
            ) : result.handover === "transfer" ? (
              <p>Once paid, transfer this repo to {(result.transfer_to ?? []).length ? <strong>{(result.transfer_to ?? []).join(" or ")}</strong> : "the client's GitHub account"} (GitHub: Settings › Transfer ownership), with the verified commit on its default branch. The transfer is picked up automatically.</p>
            ) : (
              <p>Once paid, a source archive of the verified commit is kept here for the client to download.</p>
            )}
            {result.handover !== "transfer" && (result.transfer_to ?? []).length > 0 && (
              <p>Transferring the repo to {(result.transfer_to ?? []).join(" or ")} also completes a handover.</p>
            )}
            {result.handover_ready === false && (
              <p>
                <strong>{result.handover === "transfer" ? "The client has no GitHub account with the app yet." : `${result.client_repo} isn't reachable yet.`}</strong>{" "}
                The client's organization needs the GitHub App installed{result.handover === "transfer" ? " on the account that will receive the repo" : " on it"}; until then the handover waits and is retried.
              </p>
            )}
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
        {result?.via_app && result.mode !== "contractor_repo" && (
          <Callout tone="success" title={`${result.repo} is connected through the GitHub App`} live>
            <p>Nothing to set up on GitHub. Add a tranche's label to a pull request; when it's merged, it becomes that tranche's evidence and the PR shows its progress as a check.</p>
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
        {result && !result.via_app && result.webhook_url && result.secret && (
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
