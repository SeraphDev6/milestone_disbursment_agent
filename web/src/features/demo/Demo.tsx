import { useEffect, useState, useSyncExternalStore } from "react";
import { Link, useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { api, call } from "@/api/client";
import { errorMessage } from "@/api/errors";
import { hasDemo, useDemoFiles, useDemoInfo, useHealth } from "@/api/queries";
import type { AgreementRead, DemoFile } from "@/api/types";
import { paths } from "@/app/context";
import { useTitle } from "@/app/RouteAnnouncer";
import { sessions, useSessions } from "@/auth/session";
import { money } from "@/domain/format";
import { MILESTONE_STAMPS } from "@/domain/status";
import { Busy, Button, Callout, clearToasts, CopyButton, Facts, Glyph, layout, NumberField, PageHeader, RadioGroup, Sheet, Stamp, TextField, toast } from "@/design/components";
import { demoStore, fetchDemoFile, seedDemo, SIGN_IN_DATE, type DemoAgent, type DemoKind, type DemoPerson, type DemoRecord } from "./seed";
import s from "./Demo.module.css";

/**
 * The demo, in one place, on sandbox deployments: load a realistic grant with three people already signed in,
 * jump between them, grab sample evidence files, and take shortcuts the real product doesn't offer.
 * Everything here drives the real API against PayPal's sandbox; nothing in the product depends on this page.
 */
export function Demo() {
  useTitle("Demo");
  const health = useHealth();
  const [record, setRecord] = useState<DemoRecord | null>(() => demoStore.get());

  if (health.isPending) return <Busy label="Checking the server" />;
  if (!hasDemo(health.data))
    return (
      <div className={layout.stack}>
        <PageHeader eyebrow="Demo" title="This server has no demo" lede={health.data?.mode === "live" ? "This is the live service: it moves real money through PayPal, so there is no one-click demo here. The demo runs on the sandbox deployment." : "The demo is turned off on this server. Sign in and create an agreement as you normally would."} />
        <Callout tone="note" title="Looking for the demo?">
          <p>The demo lives on a sandbox deployment, at its <code>/demo</code> page: a ready-made grant, three people to play, and PayPal's sandbox for the money.</p>
        </Callout>
      </div>
    );

  return (
    <div className={layout.stackLg}>
      <PageHeader
        eyebrow="Demo · sandbox"
        title="Try the whole loop in three minutes"
        lede="A literacy grant between a funder and a grantee with an independent reviewer, or a contractor's SOW paid by invoice when pull requests merge. The agreements come from real documents, the evidence is real files, and the money moves in PayPal's sandbox. You play every person in this browser; nothing real is charged."
      />
      <div className={layout.split}>
        <div className={layout.stackLg}>
          <LoadDemo onLoaded={setRecord} hasRecord={!!record} />
          {record && <Cast record={record} />}
          {record?.extraction && <Proposed record={record} />}
          {record?.kind === "sow" && <GitHubSheet record={record} />}
          {record && <Shortcuts record={record} />}
          {record && <AgentSheet record={record} onChange={setRecord} />}
        </div>
        <div className={layout.stack}>
          <Script />
          <PayPalAccounts />
          <Documents />
          {record && (
            <Sheet title="Start over" titleLevel={2} tight>
              <p>Forget this demo's people and shortcuts. The agreement stays on the server.</p>
              <Button
                variant="quiet"
                icon="trash"
                onPress={() => {
                  for (const p of record.people) sessions.remove(p.userId);
                  demoStore.clear();
                  setRecord(null);
                  clearToasts();
                  toast("Demo cleared");
                }}
              >
                Clear the demo
              </Button>
            </Sheet>
          )}
        </div>
      </div>
    </div>
  );
}

function LoadDemo({ onLoaded, hasRecord }: { onLoaded: (r: DemoRecord) => void; hasRecord: boolean }) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [source, setSource] = useState<"document" | "ready">("document");
  const [signed, setSigned] = useState<"signed" | "unsigned">("signed");
  const [windowDays, setWindowDays] = useState<number>(0);
  const [kind, setKind] = useState<DemoKind>("grant");
  const [repo, setRepo] = useState("");
  const [repoError, setRepoError] = useState<string | null>(null);
  const [step, setStep] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function run() {
    setError(null);
    if (kind === "sow" && repo.trim() && !/^[\w.-]+\/[\w.-]+$/.test(repo.trim())) return setRepoError("Enter the repository as owner/name, or leave it empty.");
    setRepoError(null);
    try {
      const r = await seedDemo({ source, signed: signed === "signed", disputeWindowDays: windowDays, kind, repo: kind === "sow" ? repo : undefined }, setStep);
      qc.clear();
      clearToasts();
      onLoaded(r.record);
      toast(kind === "sow" ? "Demo SOW ready" : "Demo grant ready", {
        body: r.record.extraction
          ? `${r.record.extraction.tranches} tranches and ${r.record.extraction.criteria} criteria proposed from the agreement document. You're the funder.`
          : "You're the funder. Switch people below or from the account menu.",
      });
      navigate(paths.agreement(r.orgId, r.agreementId));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setStep(null);
    }
  }
  return (
    <Sheet title={hasRecord ? "Load a fresh demo" : "Load the demo"}>
      <div className={layout.stack}>
        <p>
          {kind === "sow"
            ? "Creates Acme Retail (client) and Dev Co (contractor), the USD 6,000 checkout redesign SOW between them, and signs both people into this browser. Invoice on verify: a milestone's invoice goes out when its labelled pull request merges with CI green."
            : "Creates Harbor Foundation (funder), Riverside Reads (grantee) and Sponsor Audit Partners (reviewer), the USD 30,000 Riverside literacy grant between them, and signs all three people into this browser."}{" "}
          No email involved: the people are the demo's own.
        </p>
        <div className={layout.grid2}>
          <RadioGroup
            label="The grant comes from"
            value={source}
            onChange={setSource}
            options={[
              { id: "document", label: "The agreement document", description: "The real grant agreement PDF goes through extraction: the model proposes the tranches and criteria, with lint and open questions. Up to a minute." },
              { id: "ready", label: "Ready-made", description: "The same grant typed in, with clean criteria. Instant." },
            ]}
          />
          <RadioGroup
            label="Start"
            value={signed}
            onChange={setSigned}
            options={[
              { id: "signed", label: "Signed and active", description: "Ready to fund and submit evidence." },
              { id: "unsigned", label: "Before signing", description: "Walk through reviewing and signing the criteria." },
            ]}
          />
          <RadioGroup
            label="The agreement"
            value={kind}
            onChange={(v) => {
              setKind(v);
              if (v === "withheld" && windowDays < 3) setWindowDays(3);
            }}
            options={[
              { id: "grant", label: "Grant: funder sees evidence as it arrives", description: "Escrow; auto-release off, the funder or reviewer approves." },
              { id: "withheld", label: "Grant, SOW-style: funder sees the work once paid", description: "Escrow; auto-release on with the skeptic's second opinion, and at least a 3-day dispute window after the work opens." },
              { id: "sow", label: "SOW: invoice when a pull request merges green", description: "Invoice on verify; evidence comes from GitHub, not uploads. The same engine, a different trade." },
            ]}
          />
          {kind === "sow" ? (
            <TextField
              label="GitHub repository (optional)"
              placeholder="owner/name"
              value={repo}
              onChange={setRepo}
              error={repoError}
              figures
              description="A repo you can merge a pull request in. The contractor connects it and the page shows the webhook and labels to add. Leave empty to connect one later under Integrations."
            />
          ) : (
            <NumberField
              label="Dispute window"
              minValue={kind === "withheld" ? 3 : 0}
              maxValue={14}
              value={windowDays}
              onChange={(v) => setWindowDays(v ?? 0)}
              description="Days a released payment waits before reaching the grantee, so the funder or reviewer can contest it. 0 pays at once."
            />
          )}
        </div>
        {error && <Callout tone="error" title="The demo didn't load" live>{error}</Callout>}
        <div role="status" aria-live="polite" className={s.progress}>{step ? `${step}…` : ""}</div>
        <div><Button variant="primary" icon="play" onPress={run} isPending={!!step}>{kind === "sow" ? "Load demo SOW" : "Load demo grant"}</Button></div>
      </div>
    </Sheet>
  );
}

/** The three people, with one-click "act as". */
function Cast({ record }: { record: DemoRecord }) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { sessions: all, activeId } = useSessions();
  function actAs(p: DemoPerson) {
    sessions.switchTo(p.userId);
    qc.clear();
    clearToasts();
    navigate(paths.agreement(p.orgId, record.agreementId));
  }
  return (
    <Sheet title="The cast">
      <ul className={s.cast}>
        {record.people.map((p) => {
          const signedIn = all.some((x) => x.user.id === p.userId);
          const active = activeId === p.userId;
          return (
            <li key={p.key} className={s.person}>
              <div className={s.personHead}>
                <span className={s.personRole}>{p.role}</span>
                {active && <Stamp flat spec={{ label: "You now", tone: "held", glyph: "dot", meaning: "This is the person you're acting as." }} />}
              </div>
              <p className={s.personOrg}>{p.orgName}</p>
              <p className={s.personEmail}>{p.email}</p>
              <p className={s.personDoes}>{p.does}</p>
              {signedIn ? (
                <Button variant={active ? "secondary" : "primary"} icon="person" onPress={() => actAs(p)}>
                  {active ? "Open the grant" : `Act as the ${p.role.toLowerCase()}`}
                  <span className="sr-only"> ({p.orgName})</span>
                </Button>
              ) : (
                <p className={s.muted}>Signed out. Load a fresh demo to bring them back.</p>
              )}
            </li>
          );
        })}
      </ul>
    </Sheet>
  );
}

const tokenFor = (userId: string) => sessions.get().sessions.find((x) => x.user.id === userId)?.token;

/** Shortcuts the product deliberately doesn't have. */
function Shortcuts({ record }: { record: DemoRecord }) {
  const funder = record.people.find((p) => p.key === "funder")!;
  const grantee = record.people.find((p) => p.key === "grantee")!;
  const [a, setA] = useState<AgreementRead | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const version = useSyncExternalStore(sessions.subscribe, () => sessions.get().sessions.length);

  async function load() {
    const token = tokenFor(funder.userId);
    if (!token) return setError("The funder isn't signed in any more; load a fresh demo.");
    try {
      setA(
        (await call(
          api.GET("/agreements/{agreement_id}", { params: { path: { agreement_id: record.agreementId } }, headers: { Authorization: `Bearer ${token}`, "X-Org-Id": funder.orgId } }),
        )) as AgreementRead,
      );
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
    }
  }
  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [record.agreementId, version]);

  async function markVerified(mid: string, title: string) {
    const token = tokenFor(funder.userId);
    setBusy(mid);
    try {
      await call(api.POST("/demo/milestones/{milestone_id}/mark-verified", { params: { path: { milestone_id: mid } }, headers: { Authorization: `Bearer ${token}`, "X-Org-Id": funder.orgId } }));
      toast(`${title} marked verified`, { body: "Skipped evidence and the verifier. Release it from the tranche page." });
      await load();
    } catch (e) {
      toast("Couldn't mark it verified", { body: errorMessage(e), tone: "error" });
    } finally {
      setBusy(null);
    }
  }

  async function connectGranteePayPal() {
    const token = tokenFor(grantee.userId);
    setBusy("paypal");
    try {
      const r = await call(api.POST("/orgs/{org_id}/paypal/connect", { params: { path: { org_id: grantee.orgId } }, headers: { Authorization: `Bearer ${token}` } }));
      window.open(r.onboarding_url, "_blank", "noopener");
      toast("PayPal sandbox onboarding opened", { body: `Sign in there as the payee's sandbox business account and finish connecting. Payments from then on go to ${grantee.orgName} directly.` });
    } catch (e) {
      toast("Couldn't start PayPal onboarding", { body: errorMessage(e), tone: "error" });
    } finally {
      setBusy(null);
    }
  }

  const payee = a?.parties.find((p) => p.role === "payee");
  return (
    <Sheet title="Shortcuts" actions={<Button size="sm" variant="quiet" icon="refresh" onPress={load}>Refresh</Button>}>
      <div className={layout.stack}>
        <p className={s.muted}>The real product has no buttons like these: they skip steps so you can get to the interesting part. They only work on demo grants.</p>
        {error && <Callout tone="error" live>{error}</Callout>}
        {!a && !error && <Busy label="Loading the demo grant" />}
        {a && (
          <>
            <Facts items={[["Grant", <Link key="g" to={paths.agreement(funder.orgId, a.id)}>{a.title}</Link>], ["Status", a.status.replace(/_/g, " ")], ["Dispute window", a.dispute_window_days ? `${a.dispute_window_days} days` : "None"]]} />
            <ul className={s.shortcuts} aria-label="Tranches">
              {a.milestones.map((m, i) => (
                <li key={m.id} className={s.shortcut}>
                  <span className={s.shortcutTitle}>
                    <Link to={paths.tranche(funder.orgId, a.id, m.id)}>№{i + 1} {m.title}</Link>
                    <span className={s.muted}>{money(m.amount, a.currency)}</span>
                  </span>
                  <Stamp flat spec={MILESTONE_STAMPS[m.status]} />
                  <Button
                    size="sm"
                    icon="check"
                    isPending={busy === m.id}
                    isDisabled={a.status !== "active" || !["pending", "funded", "overdue", "evidence_received", "needs_review"].includes(m.status)}
                    onPress={() => markVerified(m.id, m.title)}
                  >
                    Mark verified<span className="sr-only">: {m.title}</span>
                  </Button>
                </li>
              ))}
            </ul>
            <div className={s.shortcut}>
              <span className={s.shortcutTitle}>
                <strong>{grantee.orgName}'s PayPal</strong>
                <span className={s.muted}>{payee?.paypal_connected ? "Connected: paid directly." : "Not connected: paid through the platform with a Payout."}</span>
              </span>
              <span />
              <Button size="sm" icon="external" isPending={busy === "paypal"} isDisabled={!!payee?.paypal_connected} onPress={connectGranteePayPal}>Connect PayPal (sandbox)</Button>
            </div>
          </>
        )}
      </div>
    </Sheet>
  );
}

function Script() {
  const steps = [
    ["Ingest", "Load the demo from the agreement document: the engine proposes the tranches, lints the criteria and raises the vague clause as an open question. Or upload the PDF yourself under New agreement."],
    ["Sign", "Load “Before signing”. As each person, open Criteria and sign. The agreement goes live when all three have."],
    ["Fund", "As the funder, open tranche 1 and fund it. PayPal's sandbox opens: sign in with the payer account below and approve the hold."],
    ["Pre-check", "As the grantee, upload the thin note and pre-check it: the verifier lists what's missing, and nothing is recorded."],
    ["Submit", "Upload the signed sign-in sheets, their register and the budget report, dated 2027-05-22, and submit. The checks count 214 rows and total USD 9,800.50."],
    ["Review", "As the funder or reviewer, read the verdict and its citations, then approve. The hold is captured. For tranche 2, the finished report is met and cited; the draft with its TODOs lands in review."],
    ["Window", "With a dispute window, the payment waits there: contest it as the reviewer, or pay now as the funder."],
    ["Same engine", "Load the SOW with a repo of yours. As the contractor, merge a pull request carrying the milestone's label; CI green verifies it and the invoice goes out. As the client, open the pay link and pay it in PayPal's sandbox."],
    ["Agent", "Create an agent key for the payee and connect Claude Code to /mcp with it. Ask it to call open_work and submit evidence: the tranche verifies and pays the same way."],
    ["Record", "Open the ledger, the activity log and the compliance packet: every step, cited and timestamped."],
  ];
  return (
    <Sheet title="A three-minute run" titleLevel={2} tight>
      <ol className={s.script}>
        {steps.map(([k, v]) => (
          <li key={k}><strong>{k}.</strong> {v}</li>
        ))}
      </ol>
    </Sheet>
  );
}

/** The sandbox accounts to use on PayPal's pages, when the deployment publishes them. */
function PayPalAccounts() {
  const info = useDemoInfo(true);
  const payer = info.data?.paypal_payer;
  const payee = info.data?.paypal_payee;
  return (
    <Sheet title="PayPal sandbox accounts" titleLevel={2} tight>
      <p className={s.muted}>
        Funding a tranche and paying a pay-now link happen on <a href={info.data?.paypal_web_url ?? "https://www.sandbox.paypal.com"} target="_blank" rel="noopener">PayPal's sandbox</a>, which has its own accounts. Sign in there with these; your real PayPal won't work and isn't touched.
      </p>
      {info.isPending && <Busy label="Loading the sandbox accounts" />}
      {info.data && !payer && !payee && (
        <p className={s.muted}>This deployment doesn't publish sandbox accounts. Use your own from <a href="https://developer.paypal.com/dashboard/accounts" target="_blank" rel="noopener">developer.paypal.com</a>: a personal account to approve holds, a business account to connect as the grantee.</p>
      )}
      {(payer || payee) && (
        <dl className={s.accounts}>
          {payer && <Account label="Payer (approves holds, pays links)" email={payer.email} password={payer.password} />}
          {payee && <Account label="Payee (connects as Riverside Reads)" email={payee.email} password={payee.password} />}
        </dl>
      )}
    </Sheet>
  );
}

function Account({ label, email, password }: { label: string; email: string; password: string }) {
  return (
    <div className={s.account}>
      <dt>{label}</dt>
      <dd>
        <span className={layout.row}><code>{email}</code><CopyButton value={email} label={`Copy the ${label} email`} /></span>
        <span className={layout.row}><code>{password}</code><CopyButton value={password} label={`Copy the ${label} password`} /></span>
      </dd>
    </div>
  );
}

/** Beat 7: an AI agent does the payee's work over MCP. Mints an org API key for the payee (the real product's
 * settings page does the same) and shows how to connect an MCP client with it. */
function AgentSheet({ record, onChange }: { record: DemoRecord; onChange: (r: DemoRecord) => void }) {
  const payee = record.people.find((p) => p.key === "grantee")!;
  const [busy, setBusy] = useState(false);
  const mcpUrl = `${window.location.origin}/mcp`;
  async function mint() {
    const token = tokenFor(payee.userId);
    if (!token) return toast("The payee isn't signed in any more; load a fresh demo.", { tone: "error" });
    setBusy(true);
    try {
      const key = await call(api.POST("/orgs/{org_id}/api-keys", { params: { path: { org_id: payee.orgId } }, body: { name: "demo agent", org_role: "member" }, headers: { Authorization: `Bearer ${token}` } }));
      const agent: DemoAgent = { token: key.token, orgId: payee.orgId, orgName: payee.orgName, createdAt: new Date().toISOString() };
      const next = { ...record, agent };
      demoStore.set(next);
      onChange(next);
      toast(`Agent key for ${payee.orgName} created`, { body: "Connect an MCP client with it; the tools act as the payee." });
    } catch (e) {
      toast("Couldn't create the key", { body: errorMessage(e), tone: "error" });
    } finally {
      setBusy(false);
    }
  }
  const a = record.agent;
  const snippet = a ? `claude mcp add --transport http milestone-ledger ${mcpUrl} --header "Authorization: Bearer ${a.token}"` : "";
  const prompt = `You are ${payee.orgName}'s agent on Milestone Ledger. Call open_work for org ${payee.orgId}, pick the first tranche, precheck_evidence with a draft that meets each criterion, then submit_evidence. Report the verdict with get_verdict.`;
  return (
    <Sheet title={`Let an agent be the ${payee.role.toLowerCase()}`}>
      <div className={layout.stack}>
        <p className={s.muted}>
          The same engine, driven by an AI agent over MCP: it asks <code>open_work</code> what {payee.orgName} can earn and what each tranche needs, drafts and pre-checks evidence, submits it, and gets paid when it verifies. People still fund, sign and approve.
        </p>
        {!a ? (
          <div><Button icon="key" isPending={busy} onPress={mint}>Create an agent key for {payee.orgName}</Button></div>
        ) : (
          <dl className={s.accounts}>
            <div className={s.account}>
              <dt>Connect Claude Code (or any MCP client that sends a bearer header)</dt>
              <dd><span className={layout.row}><code>{snippet}</code><CopyButton value={snippet} label="Copy the MCP connection command" /></span></dd>
            </div>
            <div className={s.account}>
              <dt>Then ask it</dt>
              <dd><span className={layout.row}><code>{prompt}</code><CopyButton value={prompt} label="Copy the prompt" /></span></dd>
            </div>
            <div className={s.account}>
              <dt>The key</dt>
              <dd><span className={layout.row}><code>{a.token}</code><CopyButton value={a.token} label="Copy the agent key" /></span><span className={s.muted}>Acts as a member of {a.orgName}: it can read and submit evidence, never fund, sign or approve. Revoke it from the organization's settings.</span></dd>
            </div>
          </dl>
        )}
      </div>
    </Sheet>
  );
}

/** The SOW's evidence source: the repo the contractor connected, and what to add on GitHub so a merge arrives. */
function GitHubSheet({ record }: { record: DemoRecord }) {
  const g = record.github;
  const contractor = record.people.find((p) => p.key === "grantee")!;
  return (
    <Sheet title="GitHub: where the SOW's evidence comes from">
      <div className={layout.stack}>
        {!g ? (
          <p>
            No repository connected yet. As the contractor, open the SOW's <Link to={`${paths.agreement(contractor.orgId, record.agreementId)}/integrations`}>Integrations</Link> tab and connect one (through the GitHub App, or a webhook on any repo you can merge in). Without a repo, use "Mark verified" below to see the invoice and the pay link.
          </p>
        ) : (
          <>
            <Facts items={[["Repository", <code key="r">{g.repo}</code>], ["Connected", g.via_app ? "Through the GitHub App: nothing to configure on the repo." : "By webhook: add it on the repo (Settings → Webhooks) with the values below."]]} />
            {!g.via_app && g.webhook_url && (
              <dl className={s.accounts}>
                <div className={s.account}>
                  <dt>Payload URL</dt>
                  <dd><span className={layout.row}><code>{g.webhook_url}</code><CopyButton value={g.webhook_url} label="Copy the payload URL" /></span></dd>
                </div>
                {g.secret && (
                  <div className={s.account}>
                    <dt>Secret</dt>
                    <dd><span className={layout.row}><code>{g.secret}</code><CopyButton value={g.secret} label="Copy the webhook secret" /></span></dd>
                  </div>
                )}
                <div className={s.account}>
                  <dt>Content type and events</dt>
                  <dd><code>application/json</code>, "Pull requests" only.</dd>
                </div>
              </dl>
            )}
            <p className={s.muted}>Then open a pull request with a milestone's label, let CI finish, and merge it. The merge commit, its CI runs and the changed files arrive as that milestone's evidence; green CI verifies it and the invoice goes out.</p>
            <dl className={s.accounts}>
              {g.labels.map((l) => (
                <div key={l.milestone_id} className={s.account}>
                  <dt>{l.title}</dt>
                  <dd><span className={layout.row}><code>{l.label}</code><CopyButton value={l.label} label={`Copy the label for ${l.title}`} /></span></dd>
                </div>
              ))}
            </dl>
          </>
        )}
      </div>
    </Sheet>
  );
}

/** What the engine made of the agreement document: the model's proposal, as the funder would see it on the draft. */
function Proposed({ record }: { record: DemoRecord }) {
  const x = record.extraction!;
  const errors = x.lint.filter((i) => i.level === "error");
  const warnings = x.lint.filter((i) => i.level !== "error");
  return (
    <Sheet title="What the engine proposed from the agreement">
      <div className={layout.stack}>
        <Facts items={[["Document", x.file], ["Model", x.model], ["Proposed", `${x.tranches} tranches, ${x.criteria} criteria`], ["Lint", errors.length ? `${errors.length} errors, ${warnings.length} warnings` : warnings.length ? `${warnings.length} warnings` : "Clean"]]} />
        {x.open_questions.length > 0 && (
          <div>
            <p className={s.muted}>Open questions the model raised for a person to settle before signing (the agreement's vaguer clauses end up here, not as criteria):</p>
            <ul className={s.questions}>
              {x.open_questions.map((q) => (
                <li key={q}>{q}</li>
              ))}
            </ul>
          </div>
        )}
        {x.lint.length > 0 && (
          <ul className={s.questions} aria-label="Lint findings">
            {x.lint.map((i) => (
              <li key={i.path + i.code}><strong>{i.level}</strong> {i.message}{i.suggestion ? ` ${i.suggestion}` : ""}</li>
            ))}
          </ul>
        )}
        {x.problems.length > 0 && (
          <Callout tone="note" title="Left out of the draft">
            <ul className={s.questions}>{x.problems.map((m) => <li key={m}>{m}</li>)}</ul>
          </Callout>
        )}
      </div>
    </Sheet>
  );
}

/** The demo's documents, served by the API from demo/: download, then upload as the grantee (or as the agreement). */
function Documents() {
  const files = useDemoFiles(true);
  const [busy, setBusy] = useState<string | null>(null);
  async function save(f: DemoFile) {
    setBusy(f.key);
    try {
      const file = await fetchDemoFile(f);
      const url = URL.createObjectURL(file);
      const el = document.createElement("a");
      el.href = url;
      el.download = file.name;
      el.click();
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
    } catch (e) {
      toast("Couldn't download it", { body: errorMessage(e), tone: "error" });
    } finally {
      setBusy(null);
    }
  }
  const agreement = files.data?.filter((f) => f.role === "agreement") ?? [];
  const evidence = files.data?.filter((f) => f.role === "evidence") ?? [];
  return (
    <Sheet title="The documents" titleLevel={2} tight>
      <p className={s.muted}>Real files, not stand-ins: a grant agreement for the engine to read, and the grantee's evidence for it to check. Download them here, then upload them in the product like anyone would.</p>
      {files.isPending && <Busy label="Loading the documents" />}
      {files.data && (
        <>
          <h3 className={s.docsHead}>The agreement</h3>
          <ul className={s.samples}>
            {agreement.map((f) => (
              <li key={f.key}>
                <Button size="sm" variant="quiet" icon="download" isPending={busy === f.key} onPress={() => save(f)}>{f.name}</Button>
                <span className={s.muted}>{f.what} Upload it under New agreement as the funder to watch the extraction.</span>
              </li>
            ))}
          </ul>
          <h3 className={s.docsHead}>The grantee's evidence</h3>
          <ul className={s.samples}>
            {evidence.map((f) => (
              <li key={f.key}>
                <Button size="sm" variant="quiet" icon="download" isPending={busy === f.key} onPress={() => save(f)}>{f.name}</Button>
                <span className={s.muted}>{f.what}</span>
              </li>
            ))}
          </ul>
          <p className={s.muted}>The PDFs carry their own date. For the two sheets, enter {SIGN_IN_DATE} (the last workshop) as the evidence date when a criterion asks for one.</p>
        </>
      )}
      <p className={s.muted}><Glyph name="info" size={12} style={{ display: "inline" }} /> The verifier treats every document as untrusted data; nothing in them is an instruction.</p>
    </Sheet>
  );
}
