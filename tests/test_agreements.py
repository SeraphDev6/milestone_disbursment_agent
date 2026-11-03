def create(client, draft):
    r = client.post("/agreements", json=draft)
    assert r.status_code == 201, r.text
    body = r.json()
    keys = {k["role"]: k["api_key"] for k in body["party_keys"]}
    return body["agreement"], keys


def h(key):
    return {"X-API-Key": key}


def test_health_reports_mock_mode(client):
    assert client.get("/health").json() == {"status": "ok", "mode": "mock"}


def test_both_parties_confirm_activates(client, draft):
    agreement, keys = create(client, draft)
    aid, chash = agreement["id"], agreement["criteria_hash"]
    assert agreement["lint"]["ok"] and agreement["status"] == "draft"
    assert [c["id"] for c in agreement["milestones"][0]["criteria"]] == ["c1", "c2"]

    r = client.post(
        f"/agreements/{aid}/confirm",
        json={"criteria_hash": chash, "auto_release_enabled": True},
        headers=h(keys["payer"]),
    )
    assert r.json()["status"] == "awaiting_confirmation"

    r = client.post(f"/agreements/{aid}/confirm", json={"criteria_hash": chash}, headers=h(keys["payee"]))
    body = r.json()
    assert body["status"] == "active" and body["locked"] and body["auto_release_enabled"]

    r = client.put(f"/agreements/{aid}/milestones", json={"milestones": draft["milestones"]}, headers=h(keys["payer"]))
    assert r.status_code == 409


def test_hash_survives_db_roundtrip(client, draft):
    draft["milestones"][0]["amount"] = "10000"
    draft["total_amount"] = 30000
    agreement, keys = create(client, draft)
    fetched = client.get(f"/agreements/{agreement['id']}", headers=h(keys["payer"])).json()
    assert fetched["criteria_hash"] == agreement["criteria_hash"]


def test_lint_errors_block_confirm(client, draft):
    draft["milestones"][0]["criteria"][0]["statement"] = "Does the thing"
    agreement, keys = create(client, draft)
    assert not agreement["lint"]["ok"]
    r = client.post(
        f"/agreements/{agreement['id']}/confirm",
        json={"criteria_hash": agreement["criteria_hash"]},
        headers=h(keys["payer"]),
    )
    assert r.status_code == 422
    assert r.json()["detail"]["lint"]["issues"][0]["code"] == "too_short"


def test_edit_clears_signatures_and_stale_hash_rejected(client, draft):
    agreement, keys = create(client, draft)
    aid, old_hash = agreement["id"], agreement["criteria_hash"]
    client.post(f"/agreements/{aid}/confirm", json={"criteria_hash": old_hash}, headers=h(keys["payer"]))

    draft["milestones"][0]["criteria"][0]["measure"]["target"] = 250
    r = client.put(f"/agreements/{aid}/milestones", json={"milestones": draft["milestones"]}, headers=h(keys["payer"]))
    body = r.json()
    assert body["status"] == "draft" and not any(p["confirmed_current"] for p in body["parties"])

    r = client.post(f"/agreements/{aid}/confirm", json={"criteria_hash": old_hash}, headers=h(keys["payee"]))
    assert r.status_code == 409


def test_role_rules(client, draft):
    agreement, keys = create(client, draft)
    aid, chash = agreement["id"], agreement["criteria_hash"]
    r = client.post(
        f"/agreements/{aid}/confirm",
        json={"criteria_hash": chash, "auto_release_enabled": True},
        headers=h(keys["payee"]),
    )
    assert r.status_code == 403
    r = client.put(f"/agreements/{aid}/milestones", json={"milestones": draft["milestones"]}, headers=h(keys["payee"]))
    assert r.status_code == 403


def test_auth(client, draft):
    agreement, keys = create(client, draft)
    _, other_keys = create(client, draft)
    assert client.get(f"/agreements/{agreement['id']}").status_code == 401
    assert client.get(f"/agreements/{agreement['id']}", headers=h(other_keys["payer"])).status_code == 404
    assert client.get(f"/agreements/{agreement['id']}", headers=h(keys["payee"])).status_code == 200


def test_lint_endpoint_and_mcp_tags(client, draft):
    draft["milestones"][1]["criteria"][0]["statement"] = "A meaningful final report"
    report = client.post("/criteria/lint", json=draft).json()
    assert not report["ok"]
    paths = client.get("/openapi.json").json()["paths"]
    assert paths["/criteria/lint"]["post"]["x-mcp-tool"] == "lint_agreement_criteria"
