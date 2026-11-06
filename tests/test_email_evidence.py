"""Email as an evidence source (#44): an agreement-specific address, a tranche tag in the subject, attachments
through the upload parser, and mail that fails a check dropped and audited rather than guessed onto a tranche."""

import base64
import json
import time

from fastapi.testclient import TestClient

from app.integrations.inbound_email import Attachment, ReceivedMail, address_of, sign_svix, verify_svix
from tests.conftest import GOOD_DRAFT, hermetic_app
from tests.helpers import PAYEE_EMAIL, create_active, h
from tests.test_verification import BUDGET, SIGN_IN

SECRET = "whsec_" + base64.b64encode(b"a-32-byte-test-key-for-svix-sigs").decode()
DOMAIN = "mail.example.org"


def app():
    return hermetic_app(email_inbox_domain=DOMAIN, resend_api_key="re_test", resend_webhook_secret=SECRET)


def register(client, aid, keys):
    r = client.post(f"/agreements/{aid}/integrations/email", headers=h(keys["payee"], keys["payee_org"]))
    assert r.status_code == 200, r.text
    return r.json()


def deliver(client, *, email_id, sender, to, subject, msg_id="msg_1", ts=None):
    body = json.dumps(
        {
            "type": "email.received",
            "created_at": "2027-06-01T12:00:00.000Z",
            "data": {
                "email_id": email_id,
                "from": sender,
                "to": [to],
                "cc": [],
                "received_for": [],
                "subject": subject,
                "attachments": [],
            },
        }
    ).encode()
    headers = sign_svix(SECRET, msg_id, ts or str(int(time.time())), body)
    return client.post("/webhooks/email/resend", content=body, headers={**headers, "Content-Type": "application/json"})


def audit_actions(client, aid, keys):
    return [(e["action"], e["detail"]) for e in client.get(f"/agreements/{aid}/audit", headers=h(keys["payer"])).json()]


def test_svix_signatures_verify_and_reject():
    body = b'{"type":"email.received"}'
    headers = sign_svix(SECRET, "m1", str(int(time.time())), body)
    assert verify_svix(SECRET, headers, body)
    assert not verify_svix(SECRET, headers, body + b" ")
    assert not verify_svix(SECRET, {**headers, "svix-timestamp": str(int(time.time()) - 3600)}, body)
    assert not verify_svix("whsec_" + base64.b64encode(b"another-key").decode(), headers, body)
    assert address_of("Dana Whitfield <Dana@Harbor.example.org>") == "dana@harbor.example.org"


def test_forwarded_evidence_lands_on_the_tagged_tranche():
    with TestClient(app()) as client:
        aid, (m1, _), keys = create_active(client, GOOD_DRAFT)
        reg = register(client, aid, keys)
        assert reg["address"] == f"evidence-{reg['integration_id']}@{DOMAIN}"
        tag = next(t["tag"] for t in reg["tags"] if t["milestone_id"] == m1)
        assert register(client, aid, keys)["address"] == reg["address"]  # idempotent

        client.app.state.inbox.messages["em_1"] = ReceivedMail(
            id="em_1",
            sender=PAYEE_EMAIL,
            recipients=(reg["address"],),
            subject=f"Spring workshops evidence {tag}",
            text="Sheets attached. Attendance was strong.",
            attachments=(
                Attachment("sign-in.csv", "text/csv", SIGN_IN.encode()),
                Attachment("budget.csv", "text/csv", BUDGET.encode()),
            ),
        )
        r = deliver(
            client,
            email_id="em_1",
            sender=f"Riverside Director <{PAYEE_EMAIL}>",
            to=reg["address"],
            subject=f"Spring workshops evidence {tag}",
        )
        assert r.json() == {"outcome": "queued"}
        evidence = client.get(f"/milestones/{m1}/evidence", headers=h(keys["payer"])).json()
        assert [e["source"] for e in evidence] == ["email"] * 3
        assert {e["evidence_type"] for e in evidence} == {"document", "spreadsheet"}
        assert sum(e["has_file"] for e in evidence) == 2
        verdict = client.get(f"/milestones/{m1}/verdict", headers=h(keys["payer"])).json()
        assert verdict["met"] is True  # 214 rows and the budget total, from the attachments
        actions = audit_actions(client, aid, keys)
        assert any(a == "email_evidence_received" and d["items"] == 3 for a, d in actions)
        assert all(
            a.startswith("user:")
            for a in [
                e["actor"]
                for e in client.get(f"/agreements/{aid}/audit", headers=h(keys["payer"])).json()
                if e["action"] == "evidence_received"
            ]
        )

        assert deliver(client, email_id="em_1", sender=PAYEE_EMAIL, to=reg["address"], subject=tag).json() == {
            "outcome": "duplicate"
        }


def test_unverified_strangers_and_untagged_mail_never_reach_a_tranche():
    with TestClient(app()) as client:
        aid, (m1, _), keys = create_active(client, GOOD_DRAFT)
        reg = register(client, aid, keys)
        tag = reg["tags"][0]["tag"]
        for email_id, sender, subject, reason in (
            ("em_2", "stranger@elsewhere.example", tag, "sender isn't a member of the payee's organization"),
            ("em_3", PAYEE_EMAIL, "no tag here", "subject names no tranche"),
            ("em_4", PAYEE_EMAIL, " ".join(t["tag"] for t in reg["tags"]), "subject names several tranches"),
        ):
            client.app.state.inbox.messages[email_id] = ReceivedMail(
                email_id, PAYEE_EMAIL, (reg["address"],), subject, "hello"
            )
            assert deliver(
                client, email_id=email_id, sender=sender, to=reg["address"], subject=subject, msg_id=email_id
            ).json() == {"outcome": "queued"}
            assert any(
                a == "email_evidence_dropped" and d["reason"] == reason for a, d in audit_actions(client, aid, keys)
            ), reason
        assert client.get(f"/milestones/{m1}/evidence", headers=h(keys["payer"])).json() == []

        # Mail to an address that isn't ours is ignored without a record; a bad signature is refused.
        r = deliver(client, email_id="em_5", sender=PAYEE_EMAIL, to=f"someone@{DOMAIN}", subject=tag, msg_id="m5")
        assert r.json()["outcome"].startswith("ignored")
        body = b'{"type":"email.received","data":{"email_id":"x"}}'
        r = client.post(
            "/webhooks/email/resend",
            content=body,
            headers={"svix-id": "m6", "svix-timestamp": str(int(time.time())), "svix-signature": "v1,bad"},
        )
        assert r.status_code == 401


def test_without_a_receiving_domain_there_is_no_email_adapter(client):
    aid, _, keys = create_active(client, GOOD_DRAFT)
    r = client.post(f"/agreements/{aid}/integrations/email", headers=h(keys["payee"], keys["payee_org"]))
    assert r.status_code == 404 and "EMAIL_INBOX_DOMAIN" in r.json()["detail"]
    assert client.post("/webhooks/email/resend", content=b"{}").status_code == 404
    # The payer can't register the address: it's the payee's inbox.
    with TestClient(app()) as c:
        aid, _, keys = create_active(c, GOOD_DRAFT)
        assert (
            c.post(f"/agreements/{aid}/integrations/email", headers=h(keys["payer"], keys["payer_org"])).status_code
            == 403
        )
