"""Agreements between organizations: creation, invitations, confirmation, and who may do what."""

import copy

from tests.helpers import (
    PAYEE_EMAIL,
    PAYER_EMAIL,
    REVIEWER_EMAIL,
    accept,
    confirm,
    create_active,
    create_draft,
    emailed_link,
    h,
    new_org,
    sign_in,
)


def get(client, aid, token):
    return client.get(f"/agreements/{aid}", headers=h(token))


def test_health_reports_the_mode_and_the_demo(client):
    assert client.get("/health").json() == {"status": "ok", "mode": "sandbox", "demo": True}


def test_create_requires_membership_and_paypal(client, draft):
    token = sign_in(client, PAYER_EMAIL)
    no_paypal = new_org(client, token, "Harbor Foundation")
    r = client.post(f"/orgs/{no_paypal}/agreements", json=draft, headers=h(token))
    assert r.status_code == 422 and "PayPal" in r.json()["detail"]

    outsider = sign_in(client, "outsider@example.org")
    assert client.post(f"/orgs/{no_paypal}/agreements", json=draft, headers=h(outsider)).status_code == 404
    assert client.post(f"/orgs/{no_paypal}/agreements", json=draft).status_code == 401


def test_invitation_flow_and_activation(client, draft):
    aid, _mids, ctx = create_draft(client, draft)
    a = get(client, aid, ctx["payer"]).json()
    assert [p["role"] for p in a["parties"]] == ["payer", "payee"]
    assert a["pending_invitations"] == [] and a["status"] == "draft"
    assert [c["id"] for c in a["milestones"][0]["criteria"]] == ["c1", "c2"]

    assert confirm(client, aid, ctx["payer"], auto_release_enabled=True).json()["status"] == "awaiting_confirmation"
    body = confirm(client, aid, ctx["payee"]).json()
    assert body["status"] == "active" and body["locked"] and body["auto_release_enabled"]
    r = client.put(f"/agreements/{aid}/milestones", json={"milestones": draft["milestones"]}, headers=h(ctx["payer"]))
    assert r.status_code == 409


def test_only_the_invited_email_can_accept(client, draft):
    payer = sign_in(client, PAYER_EMAIL)
    org = new_org(client, payer, "Harbor", "funder@example.org")
    assert client.post(f"/orgs/{org}/agreements", json=draft, headers=h(payer)).status_code == 201
    link = emailed_link(client, PAYEE_EMAIL, "/invitations")

    impostor = sign_in(client, "impostor@example.org")
    r = accept(client, impostor, link, new_org={"name": "Fake", "paypal_email": "fake@example.org"})
    assert r.status_code == 403 and PAYEE_EMAIL in r.json()["detail"]

    payee = sign_in(client, PAYEE_EMAIL)
    r = accept(client, payee, link, new_org={"name": "Riverside", "paypal_email": "grantee@example.org"})
    assert r.status_code == 200
    assert accept(client, payee, link, new_org={"name": "Again"}).status_code == 404  # single use


def test_payee_org_needs_paypal_and_accepting_admin(client, draft):
    payer = sign_in(client, PAYER_EMAIL)
    org = new_org(client, payer, "Harbor", "funder@example.org")
    assert client.post(f"/orgs/{org}/agreements", json=draft, headers=h(payer)).status_code == 201
    link = emailed_link(client, PAYEE_EMAIL, "/invitations")
    payee = sign_in(client, PAYEE_EMAIL)
    no_paypal = new_org(client, payee, "Riverside")
    r = accept(client, payee, link, org_id=no_paypal)
    assert r.status_code == 422 and "PayPal" in r.json()["detail"]
    client.patch(f"/orgs/{no_paypal}", json={"paypal_email": "grantee@example.org"}, headers=h(payee))
    assert accept(client, payee, link, org_id=no_paypal).status_code == 200


def test_lint_errors_block_confirm(client, draft):
    draft["milestones"][0]["criteria"][0]["statement"] = "Does the thing"
    aid, _, ctx = create_draft(client, draft)
    assert not get(client, aid, ctx["payer"]).json()["lint"]["ok"]
    r = confirm(client, aid, ctx["payer"])
    assert r.status_code == 422 and r.json()["detail"]["lint"]["issues"][0]["code"] == "too_short"


def test_hash_survives_db_roundtrip(client, draft):
    draft["milestones"][0]["amount"] = "10000"
    draft["total_amount"] = 30000
    aid, _, ctx = create_draft(client, draft)
    assert (
        get(client, aid, ctx["payer"]).json()["criteria_hash"] == get(client, aid, ctx["payee"]).json()["criteria_hash"]
    )


def test_edit_clears_signatures_and_stale_hash_rejected(client, draft):
    aid, _, ctx = create_draft(client, draft)
    old_hash = get(client, aid, ctx["payer"]).json()["criteria_hash"]
    confirm(client, aid, ctx["payer"])
    changed = copy.deepcopy(draft["milestones"])
    changed[0]["criteria"][0]["measure"]["target"] = 250
    body = client.put(f"/agreements/{aid}/milestones", json={"milestones": changed}, headers=h(ctx["payer"])).json()
    assert body["status"] == "draft" and not any(p["confirmed_current"] for p in body["parties"])
    r = client.post(f"/agreements/{aid}/confirm", json={"criteria_hash": old_hash}, headers=h(ctx["payee"]))
    assert r.status_code == 409


def test_permissions_follow_side_and_org_role(client, draft):
    aid, _, ctx = create_draft(client, draft)
    hash_ = get(client, aid, ctx["payer"]).json()["criteria_hash"]
    # payee can't edit tranches or turn on auto-release
    r = client.put(f"/agreements/{aid}/milestones", json={"milestones": draft["milestones"]}, headers=h(ctx["payee"]))
    assert r.status_code == 403
    r = client.post(
        f"/agreements/{aid}/confirm",
        json={"criteria_hash": hash_, "auto_release_enabled": True},
        headers=h(ctx["payee"]),
    )
    assert r.status_code == 403

    # a viewer in the payer org can read but not confirm; a member can't confirm either (admin+)
    for role in ("viewer", "member"):
        email = f"{role}@harbor.example.org"
        r = client.post(
            f"/orgs/{ctx['payer_org']}/invitations", json={"email": email, "role": role}, headers=h(ctx["payer"])
        )
        assert r.status_code == 201
        person = sign_in(client, email)
        assert accept(client, person, emailed_link(client, email, "/invitations")).status_code == 200
        assert get(client, aid, person).status_code == 200
        r = client.post(f"/agreements/{aid}/confirm", json={"criteria_hash": hash_}, headers=h(person))
        assert r.status_code == 403 and "admin" in r.json()["detail"]


def test_other_orgs_cant_see_it(client, draft):
    aid, _, _ = create_active(client, draft)
    stranger = sign_in(client, "stranger@example.org")
    new_org(client, stranger, "Elsewhere", "else@example.org")
    assert get(client, aid, stranger).status_code == 404
    assert get(client, aid, "not-a-token").status_code == 401


def test_reviewer_must_sign_before_activation(client, draft):
    aid, _, ctx = create_draft(client, draft, reviewers=(REVIEWER_EMAIL,))
    assert get(client, aid, ctx["payer"]).json()["pending_invitations"] == [REVIEWER_EMAIL]
    confirm(client, aid, ctx["payer"])
    assert confirm(client, aid, ctx["payee"]).json()["status"] == "awaiting_confirmation"  # reviewer still pending

    reviewer = sign_in(client, REVIEWER_EMAIL)
    r = accept(client, reviewer, ctx["links"][REVIEWER_EMAIL], new_org={"name": "Fiscal Sponsor LLC"})
    assert r.status_code == 200  # reviewers don't need a PayPal account
    a = get(client, aid, reviewer).json()
    assert [p["role"] for p in a["parties"]] == ["payer", "payee", "reviewer"]
    assert confirm(client, aid, reviewer).json()["status"] == "active"


def test_revoking_a_pending_reviewer_unblocks_activation(client, draft):
    aid, _, ctx = create_draft(client, draft, reviewers=(REVIEWER_EMAIL,))
    confirm(client, aid, ctx["payer"])
    confirm(client, aid, ctx["payee"])
    [inv] = [
        i
        for i in client.get(f"/agreements/{aid}/invitations", headers=h(ctx["payer"])).json()
        if i["kind"] == "reviewer"
    ]
    assert client.delete(f"/agreements/{aid}/invitations/{inv['id']}", headers=h(ctx["payee"])).status_code == 403
    assert client.delete(f"/agreements/{aid}/invitations/{inv['id']}", headers=h(ctx["payer"])).status_code == 204
    assert confirm(client, aid, ctx["payer"]).json()["status"] == "active"


def test_payer_can_add_a_reviewer_later(client, draft):
    aid, _, ctx = create_active(client, draft)
    r = client.post(
        f"/agreements/{aid}/invitations", json={"email": REVIEWER_EMAIL, "kind": "reviewer"}, headers=h(ctx["payer"])
    )
    assert r.status_code == 201
    reviewer = sign_in(client, REVIEWER_EMAIL)
    link = emailed_link(client, REVIEWER_EMAIL, "/invitations")
    assert accept(client, reviewer, link, new_org={"name": "Auditor"}).status_code == 200
    assert get(client, aid, reviewer).json()["status"] == "active"  # joining later doesn't reopen criteria
    r = client.post(
        f"/agreements/{aid}/invitations",
        json={"email": "x@example.org", "kind": "counterparty"},
        headers=h(ctx["payer"]),
    )
    assert r.status_code == 409  # already has a payee


def test_lint_endpoint_and_mcp_tags(client, draft):
    draft["milestones"][1]["criteria"][0]["statement"] = "A meaningful final report"
    report = client.post(
        "/criteria/lint", json={"total_amount": draft["total_amount"], "milestones": draft["milestones"]}
    ).json()
    assert not report["ok"]
    paths = client.get("/openapi.json").json()["paths"]
    assert paths["/criteria/lint"]["post"]["x-mcp-tool"] == "lint_agreement_criteria"
    assert paths["/orgs/{org_id}/agreements"]["post"]["x-mcp-tool"] == "create_agreement"


def test_same_person_on_both_sides_must_choose(client, draft):
    aid, _, ctx = create_draft(client, draft, payee_email=PAYER_EMAIL.replace("officer", "officer2"))
    # the payer's person also joins the payee org
    r = client.post(
        f"/orgs/{ctx['payee_org']}/invitations", json={"email": PAYER_EMAIL, "role": "admin"}, headers=h(ctx["payee"])
    )
    assert r.status_code == 201
    assert accept(client, ctx["payer"], emailed_link(client, PAYER_EMAIL, "/invitations")).status_code == 200
    assert get(client, aid, ctx["payer"]).status_code == 409  # ambiguous: which side?
    r = client.get(f"/agreements/{aid}", headers=h(ctx["payer"], ctx["payee_org"]))
    assert r.status_code == 200


def test_deliverable_release_defaults_to_on_submit_and_keeps_old_hashes(client, draft):
    from app.domain.criteria import criteria_hash
    from app.models import MilestoneCreate

    aid, _, ctx = create_draft(client, draft)
    a = get(client, aid, ctx["payer"]).json()
    assert a["deliverable_release"] == "on_submit"
    tranches = [MilestoneCreate.model_validate(m) for m in a["milestones"]]
    assert a["criteria_hash"] == criteria_hash(tranches)  # same hash as before the setting existed


def test_deliverable_release_is_part_of_the_signed_hash(client, draft):
    plain, _, ctx1 = create_draft(client, draft)
    on_capture, _, ctx2 = create_draft(
        client,
        {**draft, "deliverable_release": "on_capture", "dispute_window_days": 3},
        payer_email="p2@harbor.org",
        payee_email="e2@river.org",
    )
    a1, a2 = get(client, plain, ctx1["payer"]).json(), get(client, on_capture, ctx2["payer"]).json()
    assert a2["deliverable_release"] == "on_capture"
    assert a1["criteria_hash"] != a2["criteria_hash"]
    stale = client.post(
        f"/agreements/{on_capture}/confirm", json={"criteria_hash": a1["criteria_hash"]}, headers=h(ctx2["payer"])
    )
    assert stale.status_code == 409


def test_on_capture_with_auto_release_and_no_reviewer_warns(client, draft):
    body = {**draft, "deliverable_release": "on_capture", "dispute_window_days": 3}
    aid, _, ctx = create_draft(client, body)
    r = confirm(client, aid, ctx["payer"], auto_release_enabled=True)
    assert r.status_code == 200
    codes = [i["code"] for i in r.json()["lint"]["issues"]]
    assert "pays_on_model_alone" in codes and r.json()["lint"]["ok"]  # a warning, not a blocker

    with_reviewer, _, ctx2 = create_draft(
        client, body, reviewers=(REVIEWER_EMAIL,), payer_email="p2@harbor.org", payee_email="e2@river.org"
    )
    r = confirm(client, with_reviewer, ctx2["payer"], auto_release_enabled=True)
    assert "pays_on_model_alone" not in [i["code"] for i in r.json()["lint"]["issues"]]


def test_deliverable_release_rejects_unknown_values(client, draft):
    token = sign_in(client, PAYER_EMAIL)
    org = new_org(client, token, "Harbor Foundation", "pay@harbor.org")
    body = {**draft, "deliverable_release": "never", "counterparty_email": PAYEE_EMAIL}
    assert client.post(f"/orgs/{org}/agreements", json=body, headers=h(token)).status_code == 422
