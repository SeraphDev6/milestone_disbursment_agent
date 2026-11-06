"""The core flow in one file, against a sandbox deployment's demo: a funder drafts a grant, the grantee joins and
signs, submits real evidence, the engine verifies it, the funder approves, and the tranche is ready to pay.

    pip install httpx
    MDE_BASE_URL=https://app.milestone-ledger.com/api python quickstart.py      # the hosted sandbox demo
    MDE_BASE_URL=http://localhost:8765/api python quickstart.py                 # uv run python scripts/dev_server.py --port 8765

Only sandbox deployments have /demo/cast (people signed in without email). Everything after it is the ordinary API.
"""

import os
import sys
import time

import httpx

GRANT = {
    "title": "Riverside Literacy Grant 2027",
    "total_amount": "10000.00",
    "currency": "USD",
    "settlement_mode": "escrow_release",
    "milestones": [
        {
            "title": "Tranche 1: spring workshops",
            "amount": "10000.00",
            "criteria": [
                {
                    "statement": "At least 200 people attend the spring reading workshops",
                    "measure": {
                        "metric": "workshop attendees",
                        "comparator": "at_least",
                        "target": 200,
                        "unit": "people",
                    },
                    "evidence_required": ["spreadsheet"],
                    "evidence_description": "Sign-in sheets, one row per attendee",
                    "verification": "check",
                    "check_id": "row_count",
                },
                {
                    "statement": "Workshop spending stays within the $10,000 tranche budget",
                    "measure": {"metric": "workshop spend", "comparator": "at_most", "target": 10000, "unit": "USD"},
                    "evidence_required": ["spreadsheet"],
                    "evidence_description": "Budget report with actual spend per line item",
                    "verification": "check",
                    "check_id": "sheet_total",
                },
            ],
        }
    ],
}


def run(api: httpx.Client, out=print) -> dict:
    """Runs the flow and returns {"agreement_id", "milestone_id", "status", "approve_url"}."""

    def call(method, path, token, org=None, **kw):
        headers = {"Authorization": f"Bearer {token}", **({"X-Org-Id": org} if org else {})}
        r = api.request(method, path, headers=headers, **kw)
        r.raise_for_status()
        return r.json()

    people = {p["key"]: p for p in api.post("/demo/cast").raise_for_status().json()["people"]}
    funder, grantee = people["funder"]["token"], people["grantee"]["token"]

    org = call("POST", "/orgs", funder, json={"name": "Harbor Foundation", "paypal_email": "funder@example.org"})
    created = call(
        "POST",
        f"/orgs/{org['id']}/agreements",
        funder,
        json={**GRANT, "counterparty_email": people["grantee"]["email"]},
    )
    aid, mid = created["agreement"]["id"], created["agreement"]["milestones"][0]["id"]
    out(f"1. Funder drafted '{GRANT['title']}' ({aid}); lint ok: {created['agreement']['lint']['ok']}")

    inv = call("GET", "/me", grantee)["invitations"][0]
    payee_org = call(
        "POST",
        f"/invitations/{inv['id']}/accept",
        grantee,
        json={"new_org": {"name": "Riverside Reads", "paypal_email": "grantee@example.org"}},
    )["organization"]["id"]
    for who, o in ((grantee, payee_org), (funder, org["id"])):
        a = call("GET", f"/agreements/{aid}", who, o)
        a = call("POST", f"/agreements/{aid}/confirm", who, o, json={"criteria_hash": a["criteria_hash"]})
    out(f"2. Grantee joined and both signed the criteria; agreement is {a['status']}")

    hold = call("POST", f"/milestones/{mid}/fund", funder, org["id"])
    out(f"3. Funder started the hold; the payer approves it in PayPal's sandbox at {hold['approve_url']}")

    files = {f["key"]: f for f in api.get("/demo/files").raise_for_status().json()}  # the demo's real evidence
    uploads = [
        ("files", (files[k]["name"], api.get(f"/demo/files/{files[k]['name']}").content, files[k]["media_type"]))
        for k in ("sign_in", "budget")
    ]
    call("POST", f"/milestones/{mid}/uploads", grantee, payee_org, files=uploads, data={"dated": "2027-05-22"})
    for _ in range(60):  # verification runs in the background; checks-only criteria take a second
        status = call("GET", f"/agreements/{aid}", funder, org["id"])["milestones"][0]["status"]
        if status not in ("evidence_received", "verifying"):
            break
        time.sleep(1)
    verdict = call("GET", f"/milestones/{mid}/verdict", funder, org["id"])
    out(
        f"4. Grantee uploaded the sign-in sheets and budget; verdict met={verdict['met']}: "
        + "; ".join(f"{r['criterion_id']} {r['observed']}" for r in verdict["criteria_results"])
    )

    if status == "needs_review":
        status = call(
            "POST",
            f"/milestones/{mid}/review",
            funder,
            org["id"],
            json={"decision": "approve", "note": "Sheets check out"},
        )["milestone_status"]
    out(
        f"5. Funder approved; tranche is {status}. Once the hold is approved, POST /milestones/{mid}/release captures it to the grantee."
    )
    return {"agreement_id": aid, "milestone_id": mid, "status": status, "approve_url": hold["approve_url"]}


if __name__ == "__main__":
    base = os.environ.get("MDE_BASE_URL", "https://app.milestone-ledger.com/api")
    with httpx.Client(base_url=base, timeout=60) as api:
        if not api.get("/health").json().get("demo"):
            sys.exit(f"{base} has no demo (not a sandbox deployment, or DEMO=false); the quickstart needs /demo/cast")
        run(api)
