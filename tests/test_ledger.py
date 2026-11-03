from tests.helpers import create_active, h, sign_in
from tests.test_escrow import fund, release, verify


def test_ledger_lists_movements_and_attempts(client, draft):
    aid, (m1, _), keys = create_active(client, draft)
    r = client.get(f"/agreements/{aid}/ledger", headers=h(keys["payer"]))
    assert r.status_code == 200, r.text
    assert r.json() == {"currency": "USD", "entries": [], "settlements": [], "disbursements": []}

    fund(client, m1, keys)
    verify(client, m1, keys)
    assert release(client, m1, keys).status_code == 200

    body = client.get(f"/agreements/{aid}/ledger", headers=h(keys["payee"])).json()  # any party can read it
    kinds = [e["kind"] for e in body["entries"]]
    assert kinds[0] == "authorized" and "captured" in kinds
    assert all(e["milestone_id"] == m1 for e in body["entries"])
    assert body["entries"][0]["amount"] == "10000.00"
    [attempt] = body["settlements"]
    assert attempt["milestone_id"] == m1 and attempt["attempt"] == 1 and attempt["mode"] == "escrow_release"
    assert attempt["idempotency_key"].startswith(m1)
    # No dispute window: the capture is paid straight on to the payee, and that payout is listed too.
    assert all(d["milestone_id"] == m1 for d in body["disbursements"])


def test_ledger_is_private_to_parties(client, draft):
    aid, _, _ = create_active(client, draft)
    outsider = sign_in(client, "someone@elsewhere.example.org")
    assert client.get(f"/agreements/{aid}/ledger", headers=h(outsider)).status_code == 404
    assert client.get(f"/agreements/{aid}/ledger").status_code == 401
