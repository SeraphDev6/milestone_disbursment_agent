/**
 * Seed a complete grant by driving the real API, the same way tests/helpers.py does. The server signs in the
 * three people (funder officer, grantee director, reviewer) without email (POST /demo/cast); from there
 * everything is the ordinary product: the funder creates the organization and the grant, the grantee and
 * reviewer accept the invitations waiting in their inbox (GET /me) with new orgs. All three sessions are kept
 * so "Switch person" can play every side.
 */
import { api, call } from "@/api/client";
import type { AgreementCreate } from "@/api/types";
import { sessions } from "@/auth/session";

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
          evidence_description: "Q2 budget sheet with actual spend per line item",
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
          evidence_required: ["document", "dataset"],
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

/** Evidence that passes tranche 1 (from tests/test_verification.py), offered as one-click samples. */
export const SAMPLES = {
  signIn: {
    title: "Spring sign-in sheets.csv",
    evidence_type: "spreadsheet" as const,
    content: "name,session\n" + Array.from({ length: 214 }, (_, i) => `Person ${i},S${i % 4}`).join("\n"),
  },
  budget: {
    title: "Q2 budget actuals.csv",
    evidence_type: "spreadsheet" as const,
    content: 'line,actual\nVenue,"$4,200.00"\nBooks,3100\nFacilitators,2500.50\n',
  },
  report: {
    title: "Final outcomes report.txt",
    evidence_type: "document" as const,
    content: "Riverside Reads final report, dated 2027-09-12. Table 2 lists all 87 enrolled students with pre and post scores.",
    dated: "2027-09-12",
  },
  unfinished: {
    title: "Final outcomes report (draft).txt",
    evidence_type: "document" as const,
    content:
      "Riverside Reads final report, dated 2027-09-12. Table 2 lists all 87 enrolled students with pre and post scores. Post scores for section B: TODO.",
    dated: "2027-09-12",
  },
  thin: {
    title: "Workshop photos note.txt",
    evidence_type: "document" as const,
    content: "The workshops went really well and lots of people came. Everyone was engaged.",
  },
};

export type DemoProgress = (step: string) => void;

/** One person in the demo: who they are, which org they act for, and what they do. */
export type DemoPerson = { key: "funder" | "grantee" | "reviewer"; email: string; userId: string; orgId: string; orgName: string; role: string; does: string };
export type DemoRecord = { agreementId: string; title: string; createdAt: string; people: DemoPerson[] };

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

/** Sample evidence as real files, so the demo goes through the same upload path as anyone else. */
export function sampleFile(key: keyof typeof SAMPLES): File {
  const x = SAMPLES[key];
  const type = x.title.endsWith(".csv") ? "text/csv" : "text/plain";
  return new File([x.content], x.title, { type });
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

/** `withheld`: SOW-style. The funder sees the work once paid, auto-release is on, and model-judged criteria need
 * the skeptic's second opinion; needs a dispute window, so it's at least 3 days. */
export async function seedDemo(opts: { signed: boolean; disputeWindowDays?: number; withheld?: boolean }, progress: DemoProgress) {
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
  const emails = { funder: funder.email, grantee: grantee.email, reviewer: reviewer.email };
  const harbor = await call(api.POST("/orgs", { body: { name: "Harbor Foundation", paypal_email: "funder@example.org" }, ...auth(funder.token) }));

  progress("Drafting the grant and inviting the grantee and a reviewer");
  const created = await call(
    api.POST("/orgs/{org_id}/agreements", {
      params: { path: { org_id: harbor.id } },
      body: {
        ...DEMO_GRANT,
        dispute_window_days: opts.withheld ? Math.max(3, opts.disputeWindowDays ?? 0) : (opts.disputeWindowDays ?? 0),
        ...(opts.withheld ? { deliverable_release: "on_capture" as const, min_evidence_visibility: "verdict_only" as const } : {}),
        counterparty_email: emails.grantee,
        reviewer_emails: [emails.reviewer],
      },
      ...auth(funder.token),
    }),
  );
  const aid = created.agreement.id;

  progress("The grantee accepts for Riverside Reads");
  const riverside = await acceptWaiting(grantee.token, { name: "Riverside Reads", paypal_email: "grantee@example.org" });

  progress("The reviewer accepts for Sponsor Audit Partners");
  const sponsor = await acceptWaiting(reviewer.token, { name: "Sponsor Audit Partners" });

  if (opts.signed) {
    progress("Everyone signs the criteria");
    const a = await call(api.GET("/agreements/{agreement_id}", { params: { path: { agreement_id: aid } }, ...auth(funder.token, harbor.id) }));
    const body = { criteria_hash: a.criteria_hash };
    const confirm = (token: string, orgId: string, extra = {}) =>
      call(api.POST("/agreements/{agreement_id}/confirm", { params: { path: { agreement_id: aid } }, body: { ...body, ...extra }, ...auth(token, orgId) }));
    await confirm(grantee.token, riverside.organization.id);
    await confirm(reviewer.token, sponsor.organization.id);
    await confirm(funder.token, harbor.id, { auto_release_enabled: !!opts.withheld });
  }

  // Keep all three people; the funder is active.
  sessions.add(reviewer.token, reviewer.user, true);
  sessions.add(grantee.token, grantee.user, true);
  sessions.add(funder.token, funder.user, true);
  const record: DemoRecord = {
    agreementId: aid,
    title: DEMO_GRANT.title,
    createdAt: new Date().toISOString(),
    people: [
      { key: "funder", email: emails.funder, userId: funder.user.id, orgId: harbor.id, orgName: "Harbor Foundation", role: "Payer", does: "Drafts the criteria, funds each tranche, reviews verdicts, releases payment." },
      { key: "grantee", email: emails.grantee, userId: grantee.user.id, orgId: riverside.organization.id, orgName: "Riverside Reads", role: "Payee", does: "Pre-checks and submits evidence, gets paid, can dispute a rejection." },
      { key: "reviewer", email: emails.reviewer, userId: reviewer.user.id, orgId: sponsor.organization.id, orgName: "Sponsor Audit Partners", role: "Reviewer", does: "Signs the criteria, approves or rejects verdicts, can contest a payment." },
    ],
  };
  demoStore.set(record);
  progress("Done");
  return { agreementId: aid, orgId: harbor.id, emails, record };
}
