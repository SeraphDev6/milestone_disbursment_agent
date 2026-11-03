"""Production hardening: overdue tranches, notifications, rate limits, PayPal disputes and route decisions,
MCP clients kept to their tools, security headers, and live-mode settings."""

from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import col, select

from app.config import Settings
from app.db import utcnow
from app.integrations.paypal import PayPalError, SandboxPayPal
from app.main import create_app
from app.models import AuditEvent
from app.services.verification import mark_overdue
from tests.conftest import hermetic_settings
from tests.helpers import PAYEE_EMAIL, PAYER_EMAIL, connect_paypal, create_active, h, sign_in
from tests.test_escrow import agreement, fund, gw, release, verify, webhook
from tests.test_payouts import captured, run_payouts, tranche
from tests.test_verification import submit


def mails(client, subject_part: str) -> list:
    return [m for m in client.app.state.mailer.sent if subject_part in m.subject]


def audit_actions(client, mid: str) -> list[str]:
    with client.app.state.sessionmaker() as s:
        rows = s.exec(select(AuditEvent).where(AuditEvent.milestone_id == mid).order_by(col(AuditEvent.at))).all()
        return [r.action for r in rows]


# --- overdue ---------------------------------------------------------------------------


@pytest.fixture
def late(draft):
    draft["milestones"][0]["due_date"] = "2026-01-31"
    return draft


def test_overdue_marks_notifies_and_still_accepts_evidence_and_funding(client, late):
    aid, (m1, _), ctx = create_active(client, late)
    with client.app.state.sessionmaker() as s:
        assert mark_overdue(s, utcnow()) == 1
        assert mark_overdue(s, utcnow()) == 0  # already marked
    a = agreement(client, aid, ctx)
    assert [m["status"] for m in a["milestones"]] == ["overdue", "pending"]
    sent = mails(client, "Overdue")
    assert {m.to for m in sent} == {PAYER_EMAIL, PAYEE_EMAIL}
    assert all("January 31, 2026" in m.body for m in sent)

    fund(client, m1, ctx)  # funding an overdue tranche keeps it overdue
    assert agreement(client, aid, ctx)["milestones"][0]["status"] == "overdue"
    submit(client, m1, ctx, "spreadsheet", "sign-in.csv", "name\nA\nB\n")
    assert agreement(client, aid, ctx)["milestones"][0]["status"] in ("evidence_received", "needs_review")


def test_overdue_skips_tranches_with_evidence_or_inactive_agreements(client, late):
    _, (m1, _), ctx = create_active(client, late)
    submit(client, m1, ctx, "spreadsheet", "sign-in.csv", "name\nA\nB\n")
    with client.app.state.sessionmaker() as s:
        assert mark_overdue(s, utcnow()) == 0


def test_overdue_hold_can_be_voided(client, late):
    aid, (m1, _), ctx = create_active(client, late)
    fund(client, m1, ctx)
    with client.app.state.sessionmaker() as s:
        mark_overdue(s, utcnow())
    assert client.post(f"/milestones/{m1}/void", headers=h(ctx["payer"])).status_code == 200
    assert agreement(client, aid, ctx)["milestones"][0]["status"] == "overdue"


# --- notifications ---------------------------------------------------------------------


def test_rejection_emails_the_payee_whats_missing(client, draft):
    _, (m1, _), ctx = create_active(client, draft)
    fund(client, m1, ctx)
    submit(client, m1, ctx, "spreadsheet", "sign-in.csv", "name\nA\nB\n")
    review = mails(client, "Review needed")
    assert review and review[0].to == PAYER_EMAIL and "criteria were not met" in review[0].body
    client.post(f"/milestones/{m1}/review", json={"decision": "reject", "note": "thin"}, headers=h(ctx["payer"]))
    [mail] = mails(client, "Evidence rejected")
    assert mail.to == PAYEE_EMAIL and "thin" in mail.body and "- c1:" in mail.body and "- c2:" in mail.body
    assert "/agreements/" in mail.link


def test_links_open_the_web_app_when_configured(draft):
    app = create_app(hermetic_settings(public_app_url="https://host/app"))
    with TestClient(app) as client:
        aid, (m1, _), ctx = create_active(client, draft)
        fund(client, m1, ctx)
        submit(client, m1, ctx, "spreadsheet", "sign-in.csv", "name\nA\nB\n")
        [mail] = mails(client, "Review needed")
        assert mail.link == f"https://host/app/o/{ctx['payer_org']}/a/{aid}/m/{m1}"


def test_reviewer_only_agreements_notify_reviewers_not_the_payer(client, draft):
    from tests.helpers import REVIEWER_EMAIL, accept, confirm, create_draft

    aid, (m1, _), ctx = create_draft(client, {**draft, "require_reviewer_approval": True}, reviewers=(REVIEWER_EMAIL,))
    reviewer = sign_in(client, REVIEWER_EMAIL)
    assert accept(client, reviewer, ctx["links"][REVIEWER_EMAIL], new_org={"name": "Sponsor"}).status_code == 200
    for who in (ctx["payer"], ctx["payee"], reviewer):
        assert confirm(client, aid, who).status_code == 200
    fund(client, m1, ctx)
    submit(client, m1, ctx, "spreadsheet", "sign-in.csv", "name\nA\nB\n")
    assert {m.to for m in mails(client, "Review needed")} == {REVIEWER_EMAIL}


def test_unclaimed_payout_emails_the_payee(client, draft):
    aid, (m1, _), ctx = create_active(client, draft)
    gw(client).next_payout_status = "UNCLAIMED"
    captured(client, aid, ctx, m1)
    [mail] = mails(client, "Claim your payment")
    assert mail.to == PAYEE_EMAIL and "grantee@example.org" in mail.body


def test_failed_payout_emails_the_payer(client, draft):
    aid, (m1, _), ctx = create_active(client, draft)
    gw(client).next_payout_status = "FAILED"
    captured(client, aid, ctx, m1)
    [mail] = mails(client, "Payout failed")
    assert mail.to == PAYER_EMAIL and "not lost" in mail.body


def test_verified_but_unfunded_tranche_asks_the_payer_to_fund(client, draft):
    _, (m1, _), ctx = create_active(client, draft, auto_release=True)
    submit(client, m1, ctx, "spreadsheet", "sign-in.csv", "name,session\n" + "\n".join(f"P{i},S" for i in range(214)))
    submit(client, m1, ctx, "spreadsheet", "budget.csv", "line,actual\nVenue,4200\nBooks,3100\n")
    assert mails(client, "Verified, waiting for funding")


def test_pay_link_is_emailed_to_the_payer(client, draft):
    _, (m1, _), ctx = create_active(client, {**draft, "settlement_mode": "invoice_on_verify"})
    connect_paypal(client, ctx)
    verify(client, m1, ctx)
    r = release(client, m1, ctx)
    assert r.status_code == 200, r.text
    [mail] = mails(client, "Payment due")
    assert mail.to == PAYER_EMAIL and mail.link == r.json()["pay_url"]


# --- rate limits -----------------------------------------------------------------------


def test_magic_link_rate_limit_per_ip_and_per_email():
    app = create_app(hermetic_settings(rate_limit_magic_link_per_hour=2))
    with TestClient(app) as client:
        for _ in range(2):
            assert client.post("/auth/magic-link", json={"email": "a@x.example"}).status_code == 200
        r = client.post("/auth/magic-link", json={"email": "a@x.example"})
        assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
        # a different address behind another IP is a different client
        headers = {"X-Forwarded-For": "203.0.113.9"}
        assert client.post("/auth/magic-link", json={"email": "b@x.example"}, headers=headers).status_code == 200
        # ...but the same email keeps counting across IPs (at half the per-IP limit)
        assert client.post("/auth/magic-link", json={"email": "a@x.example"}, headers=headers).status_code == 429


def test_proxy_headers_are_ignored_when_not_trusted():
    app = create_app(hermetic_settings(rate_limit_magic_link_per_hour=1, trust_proxy_headers=False))
    with TestClient(app) as client:
        assert client.post("/auth/magic-link", json={"email": "a@x.example"}).status_code == 200
        headers = {"X-Forwarded-For": "203.0.113.9"}
        assert client.post("/auth/magic-link", json={"email": "b@x.example"}, headers=headers).status_code == 429


def test_extraction_and_precheck_limits_are_per_caller(draft):
    app = create_app(hermetic_settings(rate_limit_extract_per_hour=1, rate_limit_precheck_per_hour=1))
    with TestClient(app) as client:
        token = sign_in(client, "drafter@harbor.example.org")
        body = {"text": "Grant agreement text..."}
        assert client.post("/agreements/extract-text", json=body, headers=h(token)).status_code == 200
        assert client.post("/agreements/extract-text", json=body, headers=h(token)).status_code == 429
        other = sign_in(client, "other@harbor.example.org")
        assert client.post("/agreements/extract-text", json=body, headers=h(other)).status_code == 200

        _, (m1, _), ctx = create_active(client, draft)
        ev = {"evidence": [{"evidence_type": "spreadsheet", "title": "s.csv", "content": "name\nA\n"}]}
        assert client.post(f"/milestones/{m1}/precheck", json=ev, headers=h(ctx["payee"])).status_code == 200
        assert client.post(f"/milestones/{m1}/precheck", json=ev, headers=h(ctx["payee"])).status_code == 429


def test_pay_link_rate_limit(draft):
    app = create_app(hermetic_settings(rate_limit_pay_link_per_hour=1))
    with TestClient(app) as client:
        _, (m1, _), ctx = create_active(client, {**draft, "settlement_mode": "invoice_on_verify"})
        connect_paypal(client, ctx)
        verify(client, m1, ctx)
        path = release(client, m1, ctx).json()["pay_url"].replace("http://localhost:8000", "")
        assert client.get(path, follow_redirects=False).status_code == 303
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 429 and "Nothing to pay" in r.text


# --- PayPal: funding route, disputes, contest timeout, duplicate payouts ------------------


def test_paypal_outage_while_checking_a_connecting_payee_refuses_to_fund(client, draft):
    _, (m1, _), ctx = create_active(client, draft)
    assert client.post(f"/orgs/{ctx['payee_org']}/paypal/connect", headers=h(ctx["payee"])).status_code == 200
    gw(client).fail_next = PayPalError("PayPal 503", status=503, ambiguous=True)
    r = client.post(f"/milestones/{m1}/fund", headers=h(ctx["payer"]))
    assert r.status_code == 502 and "try again" in r.json()["detail"]
    assert tranche(client, m1).payout_route is None  # nothing was routed through the platform
    assert client.post(f"/milestones/{m1}/fund", headers=h(ctx["payer"])).status_code == 200  # PayPal back


def test_live_mode_refuses_the_platform_route(client, draft):
    _, (m1, _), ctx = create_active(client, draft)  # payee never connects
    gw(client).platform_route_allowed = False
    r = client.post(f"/milestones/{m1}/fund", headers=h(ctx["payer"]))
    assert r.status_code == 409 and "connect PayPal" in r.json()["detail"]


def test_customer_dispute_pauses_a_held_tranche_and_is_recorded(client, draft):
    aid, (m1, _), ctx = create_active(client, {**draft, "dispute_window_days": 3})
    connect_paypal(client, ctx)
    s = captured(client, aid, ctx, m1)
    assert s["milestone_status"] == "held"
    capture_id = s["paypal_ref"]
    created = {
        "id": "WH-D1",
        "event_type": "CUSTOMER.DISPUTE.CREATED",
        "resource": {
            "dispute_id": "PP-D-1",
            "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
            "status": "OPEN",
            "dispute_amount": {"currency_code": "USD", "value": "10000.00"},
            "disputed_transactions": [{"seller_transaction_id": capture_id}],
        },
    }
    assert webhook(client, created).json() == {"outcome": "dispute opened: payout paused"}
    assert tranche(client, m1).status == "contested"
    assert run_payouts(client, utcnow() + timedelta(days=5))["paid_out"] == 0  # window closed, still paused
    resolved = {
        "id": "WH-D2",
        "event_type": "CUSTOMER.DISPUTE.RESOLVED",
        "resource": {
            "dispute_id": "PP-D-1",
            "status": "RESOLVED",
            "dispute_outcome": {"outcome_code": "RESOLVED_SELLER_FAVOUR"},
            "disputed_transactions": [{"seller_transaction_id": capture_id}],
        },
    }
    assert webhook(client, resolved).json() == {"outcome": "dispute resolved recorded"}
    assert audit_actions(client, m1)[-2:] == ["contested", "paypal_dispute_resolved"]
    r = client.post(f"/milestones/{m1}/resolve", json={"decision": "release"}, headers=h(ctx["payer"]))
    assert r.status_code == 200 and r.json()["milestone_status"] == "settled"


def test_dispute_for_an_unknown_transaction_is_ignored(client):
    event = {"id": "WH-D9", "event_type": "CUSTOMER.DISPUTE.CREATED", "resource": {"dispute_id": "x"}}
    assert webhook(client, event).json() == {"outcome": "ignored: no tranche for this dispute"}


def test_contested_platform_tranche_pays_the_payee_after_28_days(client, draft):
    aid, (m1, _), ctx = create_active(client, {**draft, "dispute_window_days": 3})  # platform route
    captured(client, aid, ctx, m1)
    client.post(f"/milestones/{m1}/contest", json={"reason": "unresolved"}, headers=h(ctx["payer"]))
    assert run_payouts(client, utcnow() + timedelta(days=27))["auto_released"] == 0
    assert run_payouts(client, utcnow() + timedelta(days=29))["auto_released"] == 1
    assert tranche(client, m1).status == "settled"
    assert "contest_timed_out" in audit_actions(client, m1)
    assert agreement(client, aid, ctx)["funding"]["released"] == "10000.00"


def test_release_is_refused_while_a_refund_is_under_way(client, draft):
    aid, (m1, _), ctx = create_active(client, {**draft, "dispute_window_days": 3})
    captured(client, aid, ctx, m1)
    client.post(f"/milestones/{m1}/contest", json={"reason": "not delivered"}, headers=h(ctx["payer"]))
    gw(client).drop_next_response = True  # PayPal refunded, but we never saw the answer
    r = client.post(f"/milestones/{m1}/resolve", json={"decision": "refund"}, headers=h(ctx["payee"]))
    assert r.status_code == 502
    r = client.post(f"/milestones/{m1}/resolve", json={"decision": "release"}, headers=h(ctx["payer"]))
    assert r.status_code == 409 and "refund" in r.json()["detail"]
    assert tranche(client, m1).status == "contested" and not gw(client).payouts
    r = client.post(f"/milestones/{m1}/resolve", json={"decision": "refund"}, headers=h(ctx["payee"]))
    assert r.status_code == 200 and r.json()["milestone_status"] == "refunded"


def test_failed_refund_is_flagged_and_retried(client, draft):
    aid, (m1, _), ctx = create_active(client, {**draft, "dispute_window_days": 3})
    captured(client, aid, ctx, m1)
    client.post(f"/milestones/{m1}/contest", json={"reason": "not delivered"}, headers=h(ctx["payer"]))
    gw(client).fail_next = PayPalError("PayPal 422: REFUND_FAILED", status=422)
    r = client.post(f"/milestones/{m1}/resolve", json={"decision": "refund"}, headers=h(ctx["payee"]))
    assert r.status_code == 502
    m = agreement(client, aid, ctx)["milestones"][0]
    assert m["status"] == "contested" and m["refund_failed"] is True
    r = client.post(f"/milestones/{m1}/resolve", json={"decision": "refund"}, headers=h(ctx["payee"]))
    assert r.status_code == 200 and r.json()["attempt"] == 2 and r.json()["milestone_status"] == "refunded"
    assert agreement(client, aid, ctx)["milestones"][0]["refund_failed"] is False


def test_connected_account_email_is_compared_with_the_payouts_email(client, draft):
    from urllib.parse import parse_qs, urlsplit

    _, _, ctx = create_active(client, draft)
    r = client.post(f"/orgs/{ctx['payee_org']}/paypal/connect", headers=h(ctx["payee"]))
    tracking = parse_qs(urlsplit(r.json()["onboarding_url"]).query)["tracking_id"][0]
    gw(client).complete_onboarding(tracking, primary_email="finance@riverside.example.org")
    assert client.get(f"/paypal/onboarded?merchantId={tracking}").status_code == 200
    status = client.get(f"/orgs/{ctx['payee_org']}/paypal", headers=h(ctx["payee"])).json()
    assert status["ready"] and status["primary_email"] == "finance@riverside.example.org"
    assert status["paypal_email"] == "grantee@example.org"
    assert status["notes"] and "differs" in status["notes"][0]


def test_reused_sender_batch_id_is_recognised_from_paypals_body():
    """The real Payouts answer to a reused sender_batch_id (HTTP 400), with the field name as PayPal's docs show
    it (upper case) and as the sandbox returned it (lower case): both count as 'already submitted'."""
    bodies = [
        {
            "name": "USER_BUSINESS_ERROR",
            "message": "User business error.",
            "debug_id": "7a4b2c1d9e8f0",
            "details": [
                {"field": "SENDER_BATCH_ID", "issue": "Batch with given sender_batch_id already exists", "link": []}
            ],
        },
        {"name": "VALIDATION_ERROR", "details": [{"field": "sender_batch_id", "issue": "already exists"}]},
    ]
    for body in bodies:

        def handler(request: httpx.Request, body=body) -> httpx.Response:
            if request.url.path == "/v1/oauth2/token":
                return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
            return httpx.Response(400, json=body, headers={"PayPal-Debug-Id": "7a4b2c1d9e8f0"})

        gateway = SandboxPayPal(client_id="id", client_secret="s", base_url="https://api.test", webhook_id=None)
        gateway._client = httpx.Client(base_url="https://api.test", transport=httpx.MockTransport(handler))
        with pytest.raises(PayPalError) as e:
            gateway.send_payout(email="a@b.c", amount=1, currency="USD", note="n", request_id="k-1")
        assert e.value.duplicate and not e.value.ambiguous and e.value.debug_id == "7a4b2c1d9e8f0"


# --- MCP clients stay within their tools -----------------------------------------------


def test_mcp_access_token_cannot_take_human_steps_over_rest(draft):
    from tests.test_mcp import connect

    app = create_app(hermetic_settings(PUBLIC_BASE_URL="http://localhost"))  # the MCP SDK wants localhost or https
    with TestClient(app, base_url="http://localhost") as client:
        aid, (m1, _), ctx = create_active(client, draft)
        tokens = connect(client, PAYER_EMAIL, ctx["payer_org"])
        mcp = h(tokens["access_token"])
        assert client.get(f"/agreements/{aid}", headers=mcp).status_code == 200  # a tool's read
        for path, body in (
            (f"/milestones/{m1}/fund", None),
            (f"/milestones/{m1}/release", None),
            (f"/milestones/{m1}/review", {"decision": "approve"}),
            (f"/agreements/{aid}/confirm", {"criteria_hash": "x"}),
            (f"/orgs/{ctx['payer_org']}/api-keys", {"name": "escape hatch", "org_role": "admin"}),
            ("/orgs", {"name": "Shadow org"}),
        ):
            r = client.post(path, json=body, headers=mcp)
            assert r.status_code == 403, (path, r.text)
            assert "MCP client" in r.json()["detail"]
        payee = connect(client, PAYEE_EMAIL, ctx["payee_org"])
        ev = {"evidence_type": "spreadsheet", "title": "s.csv", "content": "name\nA\n"}
        assert client.post(f"/milestones/{m1}/evidence", json=ev, headers=h(payee["access_token"])).status_code == 202


# --- headers and settings --------------------------------------------------------------


def test_security_headers(client):
    r = client.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert "strict-transport-security" not in r.headers  # http base URL
    page = client.get("/paypal/cancel")
    assert page.headers["content-security-policy"].startswith("default-src 'none'")


def test_hsts_on_https_deployments():
    app = create_app(hermetic_settings(public_base_url="https://engine.example"))
    with TestClient(app) as client:
        assert client.get("/health").headers["strict-transport-security"].startswith("max-age=")


def test_live_mode_settings():
    base = {"_env_file": None, "paypal_client_id": "id", "paypal_client_secret": "s", "secret_key": "k" * 32}
    with pytest.raises(ValueError, match="https"):
        Settings(mode="live", **base)
    with pytest.raises(ValueError, match="PAYPAL_WEBHOOK_ID"):
        Settings(mode="live", public_base_url="https://engine.example", **base)
    live = Settings(mode="live", public_base_url="https://engine.example", paypal_webhook_id="w", **base)
    assert live.paypal_api_url == "https://api-m.paypal.com"
    assert Settings(mode="sandbox", **base).paypal_api_url == "https://api-m.sandbox.paypal.com"
    with pytest.raises(ValueError, match="RESEND_API_KEY"):
        Settings(mode="sandbox", email_provider="resend", **base)
    with pytest.raises(ValueError, match="SECRET_KEY"):
        Settings(mode="sandbox", _env_file=None, paypal_client_id="id", paypal_client_secret="s")


def test_email_senders_render_text_and_html():
    from app.integrations.email import ResendMailer, SmtpMailer, make_mailer, render

    text, html = render("Hi", "Line one.\n\nLine two.", "https://x.example/y")
    assert text.endswith("https://x.example/y\n") and "<p>Line one.</p><p>Line two.</p>" in html
    assert isinstance(make_mailer(hermetic_settings(email_provider="resend", resend_api_key="k")), ResendMailer)
    assert isinstance(make_mailer(hermetic_settings(email_provider="smtp", smtp_host="mail.example")), SmtpMailer)
