import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { useGitHubActions, useGitHubRepos, useGitHubStatus } from "@/api/queries";
import type { GitHubInstallationRead } from "@/api/types";
import { errorMessage } from "@/api/errors";
import { useOrgCtx } from "@/app/context";
import { date } from "@/domain/format";
import { atLeast } from "@/domain/permissions";
import { Busy, Button, Callout, ConfirmDialog, layout, PermissionNote, Sheet, Stamp, toast } from "@/design/components";

/**
 * Installing the GitHub App for this org. GitHub sends the installer back to the API, which checks they can access
 * the installation and hands back a claim (?github_claim=); this page confirms it as the admin who started.
 */
export function GitHubApp() {
  const org = useOrgCtx();
  const status = useGitHubStatus(org.orgId);
  const installs = status.data?.installations ?? [];
  const repos = useGitHubRepos(org.orgId, installs.length > 0);
  const acts = useGitHubActions(org.orgId);
  const admin = atLeast(org.role, "admin");
  const [params, setParams] = useSearchParams();
  const [problem, setProblem] = useState<string | null>(null);
  const [removing, setRemoving] = useState<GitHubInstallationRead | null>(null);
  const confirming = useRef(false);

  // Coming back from GitHub: confirm the claim once, then drop it from the address bar.
  const claim = params.get("github_claim");
  useEffect(() => {
    if (!claim || confirming.current) return;
    confirming.current = true;
    acts.confirm
      .mutateAsync(claim)
      .then((row) => toast("GitHub connected", { body: `Installed on ${row.account_login}.`, tone: "success" }))
      .catch((e) => setProblem(errorMessage(e)))
      .finally(() => {
        const next = new URLSearchParams(params);
        next.delete("github_claim");
        setParams(next, { replace: true });
      });
  }, [claim, acts.confirm, params, setParams]);

  async function install() {
    try {
      const { install_url } = await acts.install.mutateAsync();
      window.location.assign(install_url);
    } catch (e) {
      toast("Couldn't start installing", { body: errorMessage(e), tone: "error" });
    }
  }

  async function unlink() {
    if (!removing) return;
    try {
      await acts.unlink.mutateAsync(removing.installation_id);
      toast("Installation unlinked", { body: `Uninstall it on GitHub too if ${org.orgName} no longer uses it.` });
    } catch (e) {
      toast("Couldn't unlink", { body: errorMessage(e), tone: "error" });
    } finally {
      setRemoving(null);
    }
  }

  return (
    <Sheet title="GitHub" id="github">
      <div className={layout.stack}>
        <p>
          Install the GitHub App on the repos {org.orgName} works in. As a payee, merged pull requests labelled for a tranche
          become its evidence with no webhook to set up, and each PR shows the tranche's progress as a check. As a payer, you
          can check handed-over code in your own private repos.
        </p>
        <div role="status" aria-live="polite">
          {acts.confirm.isPending && <Busy label="Confirming the GitHub connection" />}
          {problem && <Callout tone="error" title="GitHub wasn't connected">{problem}</Callout>}
        </div>
        {status.isPending ? (
          <Busy label="Checking GitHub" />
        ) : status.isError ? (
          <Callout tone="error">{status.error.message}</Callout>
        ) : !status.data?.app_available ? (
          <Callout tone="note" title="Not set up on this server">
            This deployment has no GitHub App yet. Repos can still be connected per agreement with a repo webhook.
          </Callout>
        ) : (
          <>
            {installs.length === 0 ? (
              <Callout tone="note" title="Not installed">Install the app to connect repos without webhooks.</Callout>
            ) : (
              <ul style={{ listStyle: "none", padding: 0, margin: 0 }} aria-label="GitHub installations">
                {installs.map((i) => {
                  const names = (repos.data ?? []).filter((r) => r.installation_id === i.installation_id).map((r) => r.repo);
                  return (
                    <li key={i.installation_id} className={layout.row} style={{ justifyContent: "space-between", borderBottom: "1px solid var(--rule-hairline)", padding: "var(--space-2) 0" }}>
                      <span style={{ display: "grid", gap: "var(--space-1)" }}>
                        <span className={layout.row}>
                          <Stamp flat spec={{ label: "Installed", tone: "settled", glyph: "check", meaning: "The GitHub App can read the repos chosen on GitHub." }} />
                          <strong>{i.account_login}</strong>
                          <span style={{ color: "var(--text-secondary)", fontSize: "var(--text-sm)" }}>since {date(i.created_at)}</span>
                        </span>
                        <span style={{ fontSize: "var(--text-sm)", color: "var(--text-secondary)" }}>
                          {repos.isPending ? "Loading repos…" : names.length ? names.join(", ") : "No repos chosen on GitHub yet"}
                        </span>
                      </span>
                      {admin && (
                        <Button size="sm" variant="quiet" icon="trash" onPress={() => setRemoving(i)}>
                          Unlink<span className="sr-only"> {i.account_login}</span>
                        </Button>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
            {admin ? (
              <div className={layout.row}>
                <Button variant="primary" icon="github" onPress={install} isPending={acts.install.isPending}>
                  {installs.length ? "Install on another account or change repos" : "Install the GitHub App"}
                </Button>
                {status.data.app_slug && (
                  <a href={`https://github.com/apps/${status.data.app_slug}`} target="_blank" rel="noreferrer noopener" style={{ fontSize: "var(--text-sm)" }}>
                    About the app<span className="sr-only"> (opens in a new tab)</span>
                  </a>
                )}
              </div>
            ) : (
              <PermissionNote>Installing the GitHub App needs admin or above in {org.orgName}; you're a {org.role}.</PermissionNote>
            )}
          </>
        )}
      </div>
      <ConfirmDialog
        isOpen={!!removing}
        onOpenChange={(o) => !o && setRemoving(null)}
        title="Unlink this installation?"
        summary={<p style={{ margin: 0 }}>{org.orgName} stops using the app on <strong>{removing?.account_login}</strong>. Repos connected through it stop receiving merged PRs. The app stays installed on GitHub until you uninstall it there.</p>}
        confirmLabel="Unlink"
        confirmVariant="danger"
        isPending={acts.unlink.isPending}
        onConfirm={unlink}
      />
    </Sheet>
  );
}
