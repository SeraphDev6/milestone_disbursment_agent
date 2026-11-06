/**
 * The core flow in one file, against a sandbox deployment's demo: a funder drafts a grant, the grantee joins and
 * signs, submits real evidence, the engine verifies it, the funder approves, and the tranche is ready to pay.
 *
 *   MDE_BASE_URL=https://app.milestone-ledger.com/api npx tsx quickstart.ts   # the hosted sandbox demo
 *   MDE_BASE_URL=http://localhost:8765/api npx tsx quickstart.ts              # uv run python scripts/dev_server.py --port 8765
 *
 * Only sandbox deployments have /demo/cast (people signed in without email). Everything after it is the ordinary API.
 */

const GRANT = {
  title: "Riverside Literacy Grant 2027",
  total_amount: "10000.00",
  currency: "USD",
  settlement_mode: "escrow_release",
  milestones: [
    {
      title: "Tranche 1: spring workshops",
      amount: "10000.00",
      criteria: [
        { statement: "At least 200 people attend the spring reading workshops", measure: { metric: "workshop attendees", comparator: "at_least", target: 200, unit: "people" }, evidence_required: ["spreadsheet"], evidence_description: "Sign-in sheets, one row per attendee", verification: "check", check_id: "row_count" },
        { statement: "Workshop spending stays within the $10,000 tranche budget", measure: { metric: "workshop spend", comparator: "at_most", target: 10000, unit: "USD" }, evidence_required: ["spreadsheet"], evidence_description: "Budget report with actual spend per line item", verification: "check", check_id: "sheet_total" },
      ],
    },
  ],
};

type Json = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

export async function run(base: string, out: (line: string) => void = console.log): Promise<Json> {
  const call = async (method: string, path: string, token?: string, org?: string, body?: BodyInit, json?: unknown): Promise<Json> => {
    const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
    if (org) headers["X-Org-Id"] = org;
    if (json !== undefined) headers["Content-Type"] = "application/json";
    const r = await fetch(base + path, { method, headers, body: json !== undefined ? JSON.stringify(json) : body });
    if (!r.ok) throw new Error(`${method} ${path}: ${r.status} ${await r.text()}`);
    return r.json();
  };

  const cast = await call("POST", "/demo/cast");
  const person = (key: string) => cast.people.find((p: Json) => p.key === key);
  const funder = person("funder").token;
  const grantee = person("grantee").token;

  const org = await call("POST", "/orgs", funder, undefined, undefined, { name: "Harbor Foundation", paypal_email: "funder@example.org" });
  const created = await call("POST", `/orgs/${org.id}/agreements`, funder, undefined, undefined, { ...GRANT, counterparty_email: person("grantee").email });
  const aid = created.agreement.id;
  const mid = created.agreement.milestones[0].id;
  out(`1. Funder drafted '${GRANT.title}' (${aid}); lint ok: ${created.agreement.lint.ok}`);

  const inv = (await call("GET", "/me", grantee)).invitations[0];
  const payeeOrg = (await call("POST", `/invitations/${inv.id}/accept`, grantee, undefined, undefined, { new_org: { name: "Riverside Reads", paypal_email: "grantee@example.org" } })).organization.id;
  let a: Json = {};
  for (const [who, o] of [[grantee, payeeOrg], [funder, org.id]] as const) {
    a = await call("GET", `/agreements/${aid}`, who, o);
    a = await call("POST", `/agreements/${aid}/confirm`, who, o, undefined, { criteria_hash: a.criteria_hash });
  }
  out(`2. Grantee joined and both signed the criteria; agreement is ${a.status}`);

  const hold = await call("POST", `/milestones/${mid}/fund`, funder, org.id);
  out(`3. Funder started the hold; the payer approves it in PayPal's sandbox at ${hold.approve_url}`);

  const files = (await call("GET", "/demo/files")) as unknown as Json[]; // the demo's real evidence
  const form = new FormData();
  for (const key of ["sign_in", "budget"]) {
    const f = files.find((x) => x.key === key)!;
    form.append("files", await (await fetch(`${base}/demo/files/${encodeURIComponent(f.name)}`)).blob(), f.name);
  }
  form.append("dated", "2027-05-22");
  await call("POST", `/milestones/${mid}/uploads`, grantee, payeeOrg, form);
  let status = "";
  for (let i = 0; i < 60; i++) { // verification runs in the background; checks-only criteria take a second
    status = (await call("GET", `/agreements/${aid}`, funder, org.id)).milestones[0].status;
    if (status !== "evidence_received" && status !== "verifying") break;
    await new Promise((r) => setTimeout(r, 1000));
  }
  const verdict = await call("GET", `/milestones/${mid}/verdict`, funder, org.id);
  out(`4. Grantee uploaded the sign-in sheets and budget; verdict met=${verdict.met}: ${verdict.criteria_results.map((r: Json) => `${r.criterion_id} ${r.observed}`).join("; ")}`);

  if (status === "needs_review") {
    status = (await call("POST", `/milestones/${mid}/review`, funder, org.id, undefined, { decision: "approve", note: "Sheets check out" })).milestone_status;
  }
  out(`5. Funder approved; tranche is ${status}. Once the hold is approved, POST /milestones/${mid}/release captures it to the grantee.`);
  return { agreement_id: aid, milestone_id: mid, status, approve_url: hold.approve_url };
}

if (process.argv[1] && import.meta.url.endsWith(process.argv[1].split("/").pop()!)) {
  const base = process.env.MDE_BASE_URL ?? "https://app.milestone-ledger.com/api";
  const health = await (await fetch(`${base}/health`)).json();
  if (!health.demo) throw new Error(`${base} has no demo (not a sandbox deployment, or DEMO=false); the quickstart needs /demo/cast`);
  await run(base);
}
