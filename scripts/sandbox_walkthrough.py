"""Walk the whole PayPal wallet flow through a running app in sandbox mode (issue #33).

Drives the real API the way tests/helpers.py does, and stops whenever a person has to act: paste a sign-in or
invitation link from the server log (or inbox), connect PayPal as the payee, approve a hold or pay as the funder.
After each step it checks the tranche status and the ledger, so a run that finishes is evidence the flow works.

    MODE=sandbox LLM=mock uv run uvicorn app.main:app          # in one terminal; see docs/SANDBOX.md
    uv run python scripts/sandbox_walkthrough.py               # in another

Flows (all by default):
    escrow   fund a hold, submit evidence, auto-release: capture straight to the connected payee
    paynow   invoice mode with a connected payee: the funder pays a pay-now link, money goes to the payee
    platform the payee never connects PayPal: hold paid to the platform, then PayPal Payouts to the payee

PayPal's return pages are browser redirects, not calls from PayPal, so localhost works and no webhooks are needed.
The platform flow's payout finishes on the app's payouts loop (PAYOUTS_INTERVAL_SECONDS) or `python -m app.jobs
payouts`; nothing else waits on a webhook.
"""

import argparse
import os
import sys
import time
import uuid
from urllib.parse import parse_qs, urlsplit

import httpx

SIGN_IN = "name,session\n" + "\n".join(f"Person {i},S{i % 4}" for i in range(214))
BUDGET = 'line,actual\nVenue,"$4.20"\nBooks,3.10\nFacilitators,2.50\n'


def agreement_body(title: str, mode: str, counterparty: str) -> dict:
    """Two small tranches whose criteria are all deterministic checks, so no model call is needed (LLM=mock)."""
    criteria = [
        {
            "statement": "At least 200 people attend the spring reading workshops",
            "measure": {"metric": "workshop attendees", "comparator": "at_least", "target": 200, "unit": "people"},
            "evidence_required": ["spreadsheet"],
            "evidence_description": "Signed sign-in sheets for each session, one row per attendee",
            "verification": "check",
            "check_id": "row_count",
        },
        {
            "statement": "Workshop spending stays within the $10 tranche budget",
            "measure": {"metric": "workshop spend", "comparator": "at_most", "target": 10, "unit": "USD"},
            "evidence_required": ["spreadsheet"],
            "evidence_description": "Budget sheet with actual spend per line item",
            "verification": "check",
            "check_id": "sheet_total",
        },
    ]
    return {
        "title": title,
        "total_amount": "15.00",
        "currency": "USD",
        "settlement_mode": mode,
        "auto_release_threshold": 0.85,
        "counterparty_email": counterparty,
        "milestones": [
            {"title": "Tranche 1: spring workshops", "amount": "10.00", "due_date": "2027-05-31", "criteria": criteria},
            {
                "title": "Tranche 2: closing note",
                "amount": "5.00",
                "due_date": "2027-09-30",
                "criteria": [
                    {
                        "statement": "A closing note is uploaded by the end of the grant",
                        "measure": {"metric": "closing note", "comparator": "exists"},
                        "evidence_required": ["document"],
                        "evidence_description": "Closing note document from the grantee",
                        "verification": "check",
                        "check_id": "file_present",
                    }
                ],
            },
        ],
    }


class Walk:
    def __init__(self, base: str, timeout: float, *, http: httpx.Client | None = None, mode: str = "sandbox"):
        self.base = base.rstrip("/")
        self.http = http or httpx.Client(base_url=f"{self.base}/api", timeout=30)
        self.timeout = timeout
        self.mode = mode  # tests run the same walk against mock mode
        self.poll_seconds = 3.0
        self.tokens: dict[str, str] = {}

    # --- output and prompts ---

    @staticmethod
    def step(text: str) -> None:
        print(f"\n== {text}")

    @staticmethod
    def ok(text: str) -> None:
        print(f"   ok   {text}")

    @staticmethod
    def fail(text: str) -> None:
        print(f"   FAIL {text}")
        raise SystemExit(1)

    @staticmethod
    def ask(text: str) -> str:
        return input(f"   >>   {text}\n        ").strip()

    def open_in_browser(self, url: str, who: str) -> None:
        print(f"   >>   As {who}, open this in a browser and finish in PayPal:\n        {url}")

    # --- API ---

    def call(self, method: str, path: str, who: str | None = None, ok=(200, 201, 202), **kwargs) -> dict:
        headers = {"Authorization": f"Bearer {self.tokens[who]}"} if who else {}
        r = self.http.request(method, path, headers=headers, **kwargs)
        if r.status_code not in ok:
            self.fail(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
        return r.json() if r.content else {}

    def token_from(self, link: str) -> str:
        token = parse_qs(urlsplit(link).query).get("token", [link])[0]
        return token.strip()

    def sign_in(self, who: str, email: str) -> None:
        sent = self.call("POST", "/auth/magic-link", json={"email": email})
        link = sent.get("dev_link") or self.ask(f"Paste the sign-in link emailed to {email} (server log with console)")
        self.tokens[who] = self.call("POST", "/auth/magic-link/verify", json={"token": self.token_from(link)})["token"]
        self.ok(f"{who} signed in as {email}")

    def wait_for(self, what: str, check, who: str, path: str) -> dict:
        """Poll path until check(response) is true, or give up after the timeout."""
        deadline = time.monotonic() + self.timeout
        while True:
            data = self.call("GET", path, who)
            if check(data):
                return data
            if time.monotonic() > deadline:
                self.fail(f"timed out waiting for {what}")
            time.sleep(self.poll_seconds)

    def tranche(self, aid: str, index: int = 0) -> dict:
        return self.call("GET", f"/agreements/{aid}", "payer")["milestones"][index]

    def tranche_reaches(self, aid: str, statuses: set[str], what: str) -> dict:
        a = self.wait_for(what, lambda a: a["milestones"][0]["status"] in statuses, "payer", f"/agreements/{aid}")
        m = a["milestones"][0]
        self.ok(f"tranche 1 is {m['status']}; funding {a['funding']}")
        return a

    # --- setup ---

    def preflight(self) -> None:
        self.step("Server")
        health = self.call("GET", "/health")
        if health.get("mode") != self.mode:
            self.fail(f"server is in {health.get('mode')} mode; start it with MODE=sandbox (see docs/SANDBOX.md)")
        self.ok(f"{self.base} is up in {self.mode} mode")

    def orgs(self, payer_email: str, payer_paypal: str) -> str:
        self.step("Funder signs in and creates an organization")
        self.sign_in("payer", payer_email)
        org = self.call("POST", "/orgs", "payer", json={"name": "Sandbox Foundation", "paypal_email": payer_paypal})
        self.ok(f"funder org {org['id']}")
        return org["id"]

    def create(self, payer_org: str, body: dict, payee_email: str, payee_paypal: str, payee_org: str | None):
        self.step(f"Agreement '{body['title']}' ({body['settlement_mode']})")
        created = self.call("POST", f"/orgs/{payer_org}/agreements", "payer", json=body)
        aid = created["agreement"]["id"]
        self.ok(f"agreement {aid}")
        if "payee" not in self.tokens:
            self.sign_in("payee", payee_email)
        link = (created.get("dev_links") or {}).get(payee_email) or self.ask(
            f"Paste the invitation link emailed to {payee_email} for '{body['title']}'"
        )
        accept = {"token": self.token_from(link)}
        accept |= (
            {"org_id": payee_org}
            if payee_org
            else {"new_org": {"name": "Sandbox Grantee", "paypal_email": payee_paypal}}
        )
        joined = self.call("POST", "/invitations/accept", "payee", json=accept)
        payee_org = joined["organization"]["id"]
        self.ok(f"grantee joined with org {payee_org}")
        for who, extra in (("payer", {"auto_release_enabled": True}), ("payee", {})):
            current = self.call("GET", f"/agreements/{aid}", who)["criteria_hash"]
            self.call("POST", f"/agreements/{aid}/confirm", who, json={"criteria_hash": current, **extra})
        status = self.call("GET", f"/agreements/{aid}", "payer")["status"]
        if status != "active":
            self.fail(f"agreement is {status} after both confirmed")
        self.ok("both sides confirmed; agreement active, auto-release on")
        return aid, payee_org

    def connect_paypal(self, payee_org: str) -> None:
        self.step("Grantee connects PayPal (partner onboarding)")
        status = self.call("GET", f"/orgs/{payee_org}/paypal", "payee")
        if status.get("ready"):
            self.ok(f"already connected: merchant {status.get('merchant_id')}")
            return
        url = self.call("POST", f"/orgs/{payee_org}/paypal/connect", "payee")["onboarding_url"]
        self.open_in_browser(url, "the grantee (sandbox BUSINESS account that isn't the app owner)")
        self.ask("Press Enter once PayPal has sent you back to the 'PayPal connected' page")
        deadline = time.monotonic() + self.timeout
        while not status.get("ready"):
            status = self.call("POST", f"/orgs/{payee_org}/paypal/refresh", "payee")
            if status.get("ready"):
                break
            if time.monotonic() > deadline:
                self.fail(f"PayPal account not ready: {status.get('issues')}")
            time.sleep(self.poll_seconds)
        self.ok(f"connected: merchant {status['merchant_id']} (PayPal email {status.get('primary_email')})")

    def submit_evidence(self, mid: str) -> None:
        for name, content in (("sign-in.csv", SIGN_IN), ("budget.csv", BUDGET)):
            self.call(
                "POST",
                f"/milestones/{mid}/evidence",
                "payee",
                json={"evidence_type": "spreadsheet", "title": name, "content": content},
            )
        self.ok("grantee submitted the sign-in sheet and the budget sheet")

    # --- flows ---

    def escrow(self, aid: str, route: str) -> None:
        mid = self.tranche(aid)["id"]
        self.step(f"Fund tranche 1 (escrow, {route} route)")
        started = self.call("POST", f"/milestones/{mid}/fund", "payer")
        self.open_in_browser(started["approve_url"], "the funder (sandbox PERSONAL account)")
        self.tranche_reaches(aid, {"funded"}, "the hold to be placed (approve it in PayPal)")

        self.step("Evidence, checks, auto-release")
        self.submit_evidence(mid)
        if route == "direct":
            a = self.tranche_reaches(aid, {"settled", "held"}, "capture to the payee")
        else:
            print(
                "   ..   platform route: the payout finishes on the app's payouts loop or `python -m app.jobs payouts`"
            )
            a = self.tranche_reaches(aid, {"settled"}, "the Payouts payment to the payee")
        if a["funding"]["released"] == "0.00" and a["milestones"][0]["status"] == "settled":
            self.fail("tranche settled but the ledger shows nothing released")
        self.ok("check the grantee's sandbox account for the money")

    def paynow(self, aid: str) -> None:
        mid = self.tranche(aid)["id"]
        self.step("Evidence, checks, payment request (invoice mode)")
        self.submit_evidence(mid)
        m = self.wait_for("the pay link", lambda a: a["milestones"][0].get("pay_path"), "payer", f"/agreements/{aid}")[
            "milestones"
        ][0]
        self.ok(f"tranche 1 is {m['status']}; pay link {m['pay_path']}")
        self.open_in_browser(f"{self.base}/api{m['pay_path']}", "the funder (sandbox PERSONAL account)")
        self.tranche_reaches(aid, {"settled"}, "the payment (pay it in PayPal)")


def run(
    w: Walk, flows: list[str], payer_email: str, payee_email: str, payer_paypal: str, payee_paypal: str, run_id: str
) -> None:
    """The walk itself, separate from argument parsing so tests can run it against mock mode."""
    w.preflight()
    payer_org = w.orgs(payer_email, payer_paypal)
    payee_org = None

    if "platform" in flows:  # first, while the grantee hasn't connected PayPal
        aid, payee_org = w.create(
            payer_org,
            agreement_body(f"Sandbox platform route {run_id}", "escrow_release", payee_email),
            payee_email,
            payee_paypal,
            payee_org,
        )
        w.escrow(aid, "platform")
    if "escrow" in flows:
        aid, payee_org = w.create(
            payer_org,
            agreement_body(f"Sandbox escrow {run_id}", "escrow_release", payee_email),
            payee_email,
            payee_paypal,
            payee_org,
        )
        w.connect_paypal(payee_org)
        w.escrow(aid, "direct")
    if "paynow" in flows:
        aid, payee_org = w.create(
            payer_org,
            agreement_body(f"Sandbox pay-now {run_id}", "invoice_on_verify", payee_email),
            payee_email,
            payee_paypal,
            payee_org,
        )
        w.connect_paypal(payee_org)
        w.paynow(aid)

    w.step("Done")
    print(f"   Every flow ran through the app against PayPal {w.mode}: {', '.join(flows)}.")
    print("   The audit log for each agreement is at /api/agreements/{id}/audit.")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", default=os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000"))
    p.add_argument("--flows", default="escrow,paynow,platform", help="comma-separated: escrow, paynow, platform")
    p.add_argument("--payer-email", default=os.environ.get("WALKTHROUGH_PAYER_EMAIL"), help="funder's sign-in email")
    p.add_argument("--payee-email", default=os.environ.get("WALKTHROUGH_PAYEE_EMAIL"), help="grantee's sign-in email")
    p.add_argument("--timeout", type=float, default=600, help="seconds to wait at each step")
    args = p.parse_args()
    flows = [f.strip() for f in args.flows.split(",") if f.strip()]
    unknown = set(flows) - {"escrow", "paynow", "platform"}
    if unknown:
        p.error(f"unknown flows: {', '.join(sorted(unknown))}")

    payer_paypal = os.environ.get("PAYPAL_TEST_PAYER_EMAIL")
    payee_paypal = os.environ.get("PAYPAL_TEST_PAYEE_EMAIL")
    if not (payer_paypal and payee_paypal):
        p.error("set PAYPAL_TEST_PAYER_EMAIL and PAYPAL_TEST_PAYEE_EMAIL (sandbox account emails; see docs/SANDBOX.md)")
    run_id = uuid.uuid4().hex[:6]
    payer_email = args.payer_email or f"funder+{run_id}@example.org"
    payee_email = args.payee_email or f"grantee+{run_id}@example.org"

    w = Walk(args.base_url, args.timeout)
    run(w, flows, payer_email, payee_email, payer_paypal, payee_paypal, run_id)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print()
        sys.exit(130)
