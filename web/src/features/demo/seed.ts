/**
 * Seed a complete grant by driving the real API, the same way tests/helpers.py does. The server signs in the
 * three people (funder officer, grantee director, reviewer) without email (POST /demo/cast); from there
 * everything is the ordinary product: the funder creates the organization and the grant, the grantee and
 * reviewer accept the invitations waiting in their inbox (GET /me) with new orgs. All three sessions are kept
 * so "Switch person" can play every side.
 *
 * Two ways to get the grant:
 * - from the agreement document: the real grant agreement PDF (GET /demo/files) goes through POST
 *   /agreements/extract, and the grant is created from whatever the model proposed, lint and open questions
 *   included. This is the product doing its job; it takes the model up to a minute.
 * - ready-made: the same grant typed in (DEMO_GRANT), instantly.
 */
import { API_BASE, api, call } from "@/api/client";
import { extractAgreement } from "@/api/queries";
import type { AgreementCreate, DemoFile, ExtractionResult, LintIssue } from "@/api/types";
import { sessions } from "@/auth/session";
import { tranchesFrom, tranchesOut } from "@/features/draft/model";

export const DEMO_GRANT: Omit<AgreementCreate, "counterparty_email"> = {
  title: "Riverside Literacy Grant 2027",
  total_amount: "30000.00",
  currency: "USD",
  settlement_mode: "escrow_release",
  auto_release_threshold: 0.85,
  milestones: [
    {
      title: "Tranche 1: spring workshops",
      amount: "10000.00",
      due_date: "2027-05-31",
      criteria: [
        {
          statement: "At least 200 people attend the spring reading workshops",
          measure: { metric: "workshop attendees", comparator: "at_least", target: 200, unit: "people" },
          evidence_required: ["spreadsheet"],
          evidence_description: "Signed sign-in sheets for each session, one row per attendee",
          verification: "check",
          check_id: "row_count",
        },
        {
          statement: "Workshop spending stays within the $10,000 tranche budget",
          measure: { metric: "workshop spend", comparator: "at_most", target: 10000, unit: "USD" },
          evidence_required: ["spreadsheet"],
          evidence_description: "Budget report with actual spend per line item",
          verification: "check",
          check_id: "sheet_total",
        },
      ],
    },
    {
      title: "Tranche 2: outcomes report",
      amount: "20000.00",
      due_date: "2027-09-30",
      criteria: [
        {
          statement: "Final report shows pre and post reading scores for every enrolled student",
          measure: { metric: "students with pre and post scores", comparator: "exists" },
          evidence_required: ["document"],
          evidence_description: "Final outcomes report PDF with the anonymized score table",
          verification: "model",
        },
        {
          statement: "Final report is submitted on or before September 30, 2027",
          measure: { metric: "final report date", comparator: "on_or_before", target: "2027-09-30" },
          evidence_required: ["document"],
          evidence_description: "Final outcomes report PDF with its cover date",
          verification: "check",
          check_id: "dated_by",
        },
      ],
    },
  ],
};

/** The "same engine" beat: a contractor paid by invoice when a labelled PR merges with CI green. */
export const DEMO_SOW: Omit<AgreementCreate, "counterparty_email"> = {
  title: "Checkout redesign SOW",
  total_amount: "6000.00",
  currency: "USD",
  settlement_mode: "invoice_on_verify",
  auto_release_threshold: 0.85,
  milestones: [
    {
      title: "Milestone 1: payment form",
      amount: "3500.00",
      due_date: "2027-03-15",
      criteria: [
        {
          statement: "The payment form pull request is merged into main with every CI check passing",
          measure: { metric: "CI check runs on the merged commit", comparator: "exists" },
          evidence_required: ["code_change"],
          evidence_description: "Merged pull request labelled for this milestone, with passing CI on its merge commit",
          verification: "check",
          check_id: "ci_green",
        },
      ],
    },
    {
      title: "Milestone 2: order confirmation",
      amount: "2500.00",
      due_date: "2027-04-15",
      criteria: [
        {
          statement: "The order confirmation pull request is merged into main with every CI check passing",
          measure: { metric: "CI check runs on the merged commit", comparator: "exists" },
          evidence_required: ["code_change"],
          evidence_description: "Merged pull request labelled for this milestone, with passing CI on its merge commit",
          verification: "check",
          check_id: "ci_green",
        },
      ],
    },
  ],
};

/** Dates the grantee enters for evidence that carries none of its own (the CSV exports): the last workshop. */
export const SIGN_IN_DATE = "2027-05-22";

export type DemoProgress = (step: string) => void;

/** One person in the demo: who they are, which org they act for, and what they do. */
export type DemoPerson = { key: "funder" | "grantee" | "reviewer"; email: string; userId: string; orgId: string; orgName: string; role: string; does: string };
/** What the engine made of the agreement document, kept so the demo page can show it. */
export type DemoExtraction = { file: string; model: string; tranches: number; criteria: number; open_questions: string[]; lint: LintIssue[]; problems: string[] };
/** The SOW's GitHub connection: what to add on the repo so a merged PR arrives as evidence. */
export type DemoGitHub = { repo: string; via_app: boolean; webhook_url: string | null; secret: string | null; labels: { milestone_id: string; title: string; label: string }[] };
export type DemoKind = "grant" | "withheld" | "sow";
export type DemoRecord = { agreementId: string; title: string; createdAt: string; kind?: DemoKind; people: DemoPerson[]; extraction?: DemoExtraction; github?: DemoGitHub };

const STORE = "mde.demo";

/** The last demo loaded in this browser (so the demo page can offer its shortcuts). */
export const demoStore = {
  get(): DemoRecord | null {
    try {
      const raw = localStorage.getItem(STORE);
      return raw ? (JSON.parse(raw) as DemoRecord) : null;
    } catch {
      return null;
    }
  },
  set(r: DemoRecord) {
    try {
      localStorage.setItem(STORE, JSON.stringify(r));
    } catch {
      /* storage unavailable: shortcuts just won't survive a reload */
    }
  },
  clear() {
    try {
      localStorage.removeItem(STORE);
    } catch {
      /* ignore */
    }
  },
};

/** Where the API serves one of the demo's documents. */
export const demoFileUrl = (f: Pick<DemoFile, "name">) => `${API_BASE}/demo/files/${encodeURIComponent(f.name)}`;

/** One of the demo's documents as a File, so it goes through the same upload or extraction path as anyone's. */
export async function fetchDemoFile(f: Pick<DemoFile, "name" | "media_type">): Promise<File> {
  const r = await fetch(demoFileUrl(f));
  if (!r.ok) throw new Error(`Couldn't fetch ${f.name} (${r.status}).`);
  return new File([await r.blob()], f.name, { type: f.media_type });
}

async function demoFiles(): Promise<DemoFile[]> {
  return call(api.GET("/demo/files"));
}

/** The grant as the extraction proposed it, ready for POST /agreements: nothing edited, so what the model got
 * wrong shows up as lint and open questions rather than being papered over. */
export function agreementFromExtraction(x: ExtractionResult, people: { grantee: string; reviewer?: string }, extra: Partial<AgreementCreate> = {}): AgreementCreate {
  const milestones = tranchesOut(tranchesFrom(x.milestones));
  const total = x.total_amount ?? milestones.reduce((sum, m) => sum + Number(m.amount), 0).toFixed(2);
  return {
    title: x.title,
    total_amount: String(total),
    currency: x.currency || "USD",
    settlement_mode: "escrow_release",
    auto_release_threshold: 0.85,
    milestones,
    counterparty_email: people.grantee,
    reviewer_emails: people.reviewer ? [people.reviewer] : [],
    ...extra,
  };
}

export function summarizeExtraction(x: ExtractionResult, file: string): DemoExtraction {
  return {
    file,
    model: x.model,
    tranches: x.milestones.length,
    criteria: x.milestones.reduce((n, m) => n + m.criteria.length, 0),
    open_questions: x.open_questions,
    lint: x.lint.issues ?? [],
    problems: x.problems.map((p) => `${p.path}: ${p.message}`),
  };
}

const auth = (token: string, orgId?: string) => ({
  headers: { Authorization: `Bearer ${token}`, ...(orgId ? { "X-Org-Id": orgId } : {}) },
});

/** Accept the one invitation waiting for this person, for a new organization. */
async function acceptWaiting(token: string, newOrg: { name: string; paypal_email?: string }) {
  const me = await call(api.GET("/me", auth(token)));
  const inv = me.invitations[0];
  if (!inv) throw new Error(`No invitation is waiting for ${me.user.email}.`);
  return call(api.POST("/invitations/{invitation_id}/accept", { params: { path: { invitation_id: inv.id } }, body: { new_org: newOrg }, ...auth(token) }));
}

export type SeedOptions = {
  /** document: extract the agreement from its PDF with the real model. ready: the typed-in version. */
  source: "document" | "ready";
  signed: boolean;
  disputeWindowDays?: number;
  /** grant: the literacy grant, funder sees evidence as it arrives. withheld: the same grant SOW-style, the funder
   * sees the work once paid (auto-release on, skeptic, dispute window of at least 3 days). sow: the checkout
   * redesign SOW, invoice on verify, evidence from merged pull requests. */
  kind?: DemoKind;
  /** sow: a GitHub repo (owner/name) the contractor connects; merged PRs carrying a milestone's label are evidence. */
  repo?: string;
};

const PARTIES = {
  grant: { payer: "Harbor Foundation", payee: "Riverside Reads", reviewer: "Sponsor Audit Partners" as string | null, payerRole: "Payer", payeeRole: "Payee" },
  sow: { payer: "Acme Retail", payee: "Dev Co", reviewer: null as string | null, payerRole: "Client", payeeRole: "Contractor" },
};

export async function seedDemo(opts: SeedOptions, progress: DemoProgress) {
  progress("Signing in the funder, the grantee and the reviewer");
  const cast = await call(api.POST("/demo/cast"));
  const person = (key: "funder" | "grantee" | "reviewer") => {
    const p = cast.people.find((x) => x.key === key);
    if (!p) throw new Error(`The server's demo cast has no ${key}.`);
    return p;
  };
  const funder = person("funder");
  const grantee = person("grantee");
  const reviewer = person("reviewer");
  const kind: DemoKind = opts.kind ?? "grant";
  const sow = kind === "sow";
  const withheld = kind === "withheld";
  const names = sow ? PARTIES.sow : PARTIES.grant;
  const emails = { funder: funder.email, grantee: grantee.email, reviewer: reviewer.email };
  const payerOrg = await call(api.POST("/orgs", { body: { name: names.payer, paypal_email: "funder@example.org" }, ...auth(funder.token) }));

  const terms: Partial<AgreementCreate> = {
    dispute_window_days: withheld ? Math.max(3, opts.disputeWindowDays ?? 0) : (opts.disputeWindowDays ?? 0),
    ...(withheld ? { deliverable_release: "on_capture" as const, min_evidence_visibility: "verdict_only" as const } : {}),
    ...(sow ? { settlement_mode: "invoice_on_verify" as const } : {}),
  };
  const invitees = { grantee: emails.grantee, ...(names.reviewer ? { reviewer: emails.reviewer } : {}) };
  let body: AgreementCreate;
  let extraction: DemoExtraction | undefined;
  if (opts.source === "document") {
    const files = await demoFiles();
    const doc = files.find((f) => f.key === (sow ? "sow" : "agreement"));
    if (!doc) throw new Error("This server has no demo agreement document.");
    progress(`Reading “${doc.name}”: the model proposes ${sow ? "milestones" : "tranches"} and criteria (up to a minute)`);
    const x = await extractAgreement(await fetchDemoFile(doc));
    extraction = summarizeExtraction(x, doc.name);
    if (!x.lint.ok) {
      const errors = (x.lint.issues ?? []).filter((i) => i.level === "error").map((i) => i.message);
      throw new Error(`The proposed criteria have lint errors, so the agreement can't be signed as proposed: ${errors.join("; ")}. Load the demo again, or draft it from the document yourself under New agreement.`);
    }
    if (!x.milestones.length) throw new Error("The model proposed no tranches from the agreement document.");
    body = agreementFromExtraction(x, invitees, terms);
  } else {
    body = { ...(sow ? DEMO_SOW : DEMO_GRANT), ...terms, counterparty_email: emails.grantee, reviewer_emails: names.reviewer ? [emails.reviewer] : [] };
  }

  progress(sow ? "Drafting the SOW and inviting the contractor" : "Drafting the grant and inviting the grantee and a reviewer");
  const created = await call(api.POST("/orgs/{org_id}/agreements", { params: { path: { org_id: payerOrg.id } }, body, ...auth(funder.token) }));
  const aid = created.agreement.id;

  progress(`The ${names.payeeRole.toLowerCase()} accepts for ${names.payee}`);
  const payeeOrg = (await acceptWaiting(grantee.token, { name: names.payee, paypal_email: "grantee@example.org" })).organization;

  let reviewerOrg: { id: string } | null = null;
  if (names.reviewer) {
    progress(`The reviewer accepts for ${names.reviewer}`);
    reviewerOrg = (await acceptWaiting(reviewer.token, { name: names.reviewer })).organization;
  }

  if (opts.signed) {
    progress("Everyone signs the criteria");
    const a = await call(api.GET("/agreements/{agreement_id}", { params: { path: { agreement_id: aid } }, ...auth(funder.token, payerOrg.id) }));
    const sig = { criteria_hash: a.criteria_hash };
    const confirm = (token: string, orgId: string, extra = {}) =>
      call(api.POST("/agreements/{agreement_id}/confirm", { params: { path: { agreement_id: aid } }, body: { ...sig, ...extra }, ...auth(token, orgId) }));
    await confirm(grantee.token, payeeOrg.id);
    if (reviewerOrg) await confirm(reviewer.token, reviewerOrg.id);
    await confirm(funder.token, payerOrg.id, { auto_release_enabled: withheld || sow });
  }

  let github: DemoGitHub | undefined;
  const repo = opts.repo?.trim();
  if (sow && repo) {
    progress(`${names.payee} connects ${repo}`);
    const g = await call(api.POST("/agreements/{agreement_id}/integrations/github", { params: { path: { agreement_id: aid } }, body: { repo }, ...auth(grantee.token, payeeOrg.id) }));
    github = { repo: g.repo, via_app: !!g.via_app, webhook_url: g.webhook_url ?? null, secret: g.secret ?? null, labels: g.labels };
  }

  // Keep every person; the payer is active.
  if (reviewerOrg) sessions.add(reviewer.token, reviewer.user, true);
  sessions.add(grantee.token, grantee.user, true);
  sessions.add(funder.token, funder.user, true);
  const people: DemoPerson[] = sow
    ? [
        { key: "funder", email: emails.funder, userId: funder.user.id, orgId: payerOrg.id, orgName: names.payer, role: "Client", does: "Signs the SOW, pays each invoice when a milestone's pull request merges green." },
        { key: "grantee", email: emails.grantee, userId: grantee.user.id, orgId: payeeOrg.id, orgName: names.payee, role: "Contractor", does: "Connects the repo, merges labelled pull requests, gets paid by invoice." },
      ]
    : [
        { key: "funder", email: emails.funder, userId: funder.user.id, orgId: payerOrg.id, orgName: names.payer, role: "Payer", does: "Drafts the criteria, funds each tranche, reviews verdicts, releases payment." },
        { key: "grantee", email: emails.grantee, userId: grantee.user.id, orgId: payeeOrg.id, orgName: names.payee, role: "Payee", does: "Pre-checks and submits evidence, gets paid, can dispute a rejection." },
      ];
  if (reviewerOrg && names.reviewer) {
    people.push({ key: "reviewer", email: emails.reviewer, userId: reviewer.user.id, orgId: reviewerOrg.id, orgName: names.reviewer, role: "Reviewer", does: "Signs the criteria, approves or rejects verdicts, can contest a payment." });
  }
  const record: DemoRecord = {
    agreementId: aid,
    title: body.title,
    createdAt: new Date().toISOString(),
    kind,
    people,
    ...(extraction ? { extraction } : {}),
    ...(github ? { github } : {}),
  };
  demoStore.set(record);
  progress("Done");
  return { agreementId: aid, orgId: payerOrg.id, emails, record };
}
