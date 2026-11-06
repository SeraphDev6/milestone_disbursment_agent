import { useEffect, useState, useSyncExternalStore } from "react";
import { Link, useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { api, call } from "@/api/client";
import { errorMessage } from "@/api/errors";
import { hasDemo, useDemoInfo, useHealth } from "@/api/queries";
import type { AgreementRead } from "@/api/types";
import { paths } from "@/app/context";
import { useTitle } from "@/app/RouteAnnouncer";
import { sessions, useSessions } from "@/auth/session";
import { money } from "@/domain/format";
import { MILESTONE_STAMPS } from "@/domain/status";
import { Busy, Button, Callout, clearToasts, CopyButton, Facts, Glyph, layout, NumberField, PageHeader, RadioGroup, Sheet, Stamp, toast } from "@/design/components";
import { demoStore, sampleFile, SAMPLES, seedDemo, type DemoPerson, type DemoRecord } from "./seed";
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
        lede="A literacy grant between a funder and a grantee, with an independent reviewer. You play all three people in this browser. Money moves in PayPal's sandbox, with sandbox accounts; nothing real is charged."
      />
      <div className={layout.split}>
        <div className={layout.stackLg}>
          <LoadDemo onLoaded={setRecord} hasRecord={!!record} />
          {record && <Cast record={record} />}
          {record && <Shortcuts record={record} />}
        </div>
        <div className={layout.stack}>
          <Script />
          <PayPalAccounts />
          <Samples />
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
  const [signed, setSigned] = useState<"signed" | "unsigned">("signed");
  const [windowDays, setWindowDays] = useState<number>(0);
  const [kind, setKind] = useState<"grant" | "withheld">("grant");
  const [step, setStep] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function run() {
    setError(null);
    try {
      const r = await seedDemo({ signed: signed === "signed", disputeWindowDays: windowDays, withheld: kind === "withheld" }, setStep);
      qc.clear();
      clearToasts();
      onLoaded(r.record);
      toast("Demo grant ready", { body: "You're the funder. Switch people below or from the account menu." });
      navigate(paths.agreement(r.orgId, r.agreementId));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setStep(null);
    }
  }
  return (
    <Sheet title={hasRecord ? "Load a fresh demo grant" : "Load the demo grant"}>
      <div className={layout.stack}>
        <p>Creates Harbor Foundation (funder), Riverside Reads (grantee) and Sponsor Audit Partners (reviewer), a two-tranche USD 30,000 grant between them, and signs all three people into this browser. No email involved: the people are the demo's own.</p>
        <div className={layout.grid2}>
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
            label="Who sees the work when"
            value={kind}
            onChange={(v) => {
              setKind(v);
              if (v === "withheld" && windowDays < 3) setWindowDays(3);
            }}
            options={[
              { id: "grant", label: "Grant: funder sees evidence as it arrives", description: "Auto-release off; the funder or reviewer approves." },
              { id: "withheld", label: "SOW-style: funder sees the work once paid", description: "Auto-release on with the skeptic's second opinion, and at least a 3-day dispute window after the work opens." },
            ]}
          />
          <NumberField
            label="Dispute window"
            minValue={kind === "withheld" ? 3 : 0}
            maxValue={14}
            value={windowDays}
            onChange={(v) => setWindowDays(v ?? 0)}
            description="Days a released payment waits before reaching the grantee, so the funder or reviewer can contest it. 0 pays at once."
          />
        </div>
        {error && <Callout tone="error" title="The demo didn't load" live>{error}</Callout>}
        <div role="status" aria-live="polite" className={s.progress}>{step ? `${step}…` : ""}</div>
        <div><Button variant="primary" icon="play" onPress={run} isPending={!!step}>Load demo grant</Button></div>
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
      toast("PayPal sandbox onboarding opened", { body: "Sign in there as the payee's sandbox business account and finish connecting. Tranches funded from then on pay Riverside Reads directly." });
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
                    isDisabled={a.status !== "active" || !["pending", "funded", "evidence_received", "needs_review"].includes(m.status)}
                    onPress={() => markVerified(m.id, m.title)}
                  >
                    Mark verified<span className="sr-only">: {m.title}</span>
                  </Button>
                </li>
              ))}
            </ul>
            <div className={s.shortcut}>
              <span className={s.shortcutTitle}>
                <strong>Riverside Reads' PayPal</strong>
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
    ["Sign", "Load “Before signing”. As each person, open Criteria and sign. The agreement goes live when all three have."],
    ["Fund", "As the funder, open tranche 1 and fund it. PayPal's sandbox opens: sign in with the payer account below and approve the hold."],
    ["Pre-check", "As the grantee, upload the thin note and pre-check it: the verifier lists what's missing, and nothing is recorded."],
    ["Submit", "Upload the sign-in sheet and the budget, and submit. The checks count 214 rows and total the budget."],
    ["Review", "As the funder or reviewer, read the verdict and its citations, then approve. The hold is captured."],
    ["Window", "With a dispute window, the payment waits there: contest it as the reviewer, or pay now as the funder."],
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

function Samples() {
  const items: { key: keyof typeof SAMPLES; what: string }[] = [
    { key: "signIn", what: "214 attendees: passes the ≥ 200 check" },
    { key: "budget", what: "USD 9,800.50 spent: passes the ≤ 10,000 check" },
    { key: "report", what: "Tranche 2: final report dated before the deadline" },
    { key: "unfinished", what: "SOW-style demo, tranche 2: fund it, upload with evidence date 2027-09-12; the verifier finds it met, the second opinion spots the TODO and sends it to review" },
    { key: "thin", what: "Thin evidence: lands in review" },
  ];
  function save(key: keyof typeof SAMPLES) {
    const f = sampleFile(key);
    const url = URL.createObjectURL(f);
    const el = document.createElement("a");
    el.href = url;
    el.download = f.name;
    el.click();
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  }
  return (
    <Sheet title="Sample evidence" titleLevel={2} tight>
      <p className={s.muted}>Download these, then upload them on a tranche as the grantee.</p>
      <ul className={s.samples}>
        {items.map((i) => (
          <li key={i.key}>
            <Button size="sm" variant="quiet" icon="download" onPress={() => save(i.key)}>{SAMPLES[i.key].title}</Button>
            <span className={s.muted}>{i.what}</span>
          </li>
        ))}
      </ul>
      <p className={s.muted}><Glyph name="info" size={12} style={{ display: "inline" }} /> Sample text is plain data; the verifier treats evidence as untrusted.</p>
    </Sheet>
  );
}
