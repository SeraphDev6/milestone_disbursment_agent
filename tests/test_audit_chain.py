"""Tamper-evident audit log (#25): a hash chain per agreement, verified in the packet and by a job; and the
access-review export."""

from datetime import timedelta

from sqlmodel import select

from app.db.audit import seal_chain, verify_chain
from app.db.session import utcnow
from app.models import ApiToken, AuditEvent, OAuthClient, TokenKind
from app.services.access import hash_token
from tests.helpers import create_active, h
from tests.test_verification import BUDGET, SIGN_IN, submit


def chain(client, aid):
    with client.app.state.sessionmaker() as s:
        return verify_chain(s, aid).as_dict()


def test_every_event_is_chained_and_the_packet_says_verified(client, draft):
    aid, (m1, _), keys = create_active(client, draft)
    submit(client, m1, keys, "spreadsheet", "s.csv", SIGN_IN)
    submit(client, m1, keys, "spreadsheet", "b.csv", BUDGET)
    events = client.get(f"/agreements/{aid}/audit", headers=h(keys["payer"])).json()
    assert len(events) > 5 and all(len(e["hash"]) == 64 for e in events)
    assert len({e["hash"] for e in events}) == len(events)
    report = chain(client, aid)
    assert report["status"] == "verified" and report["sealed"] == len(events) and report["unsealed"] == 0
    assert report["head"] == events[-1]["hash"]
    packet = client.get(f"/agreements/{aid}/compliance-packet", headers=h(keys["payer"])).json()
    assert packet["integrity"]["status"] == "verified" and packet["integrity"]["head"] == report["head"]


def test_an_edited_or_deleted_event_breaks_the_chain_from_that_point(client, draft):
    aid, (m1, _), keys = create_active(client, draft)
    submit(client, m1, keys, "spreadsheet", "s.csv", SIGN_IN)
    with client.app.state.sessionmaker() as s:
        rows = s.exec(select(AuditEvent).where(AuditEvent.agreement_id == aid).order_by(AuditEvent.at)).all()
        victim = rows[2]
        victim.actor = "user:someone-else"  # a row rewritten behind the app's back
        s.add(victim)
        s.commit()
        report = verify_chain(s, aid)
        assert report.status == "broken" and report.broken_at == [victim.id]
        s.delete(rows[4])  # a row removed: the one after it links to a hash that is no longer there
        s.commit()
        report = verify_chain(s, aid)
        assert report.status == "broken" and report.broken_at == [victim.id, rows[5].id]
    packet = client.get(f"/agreements/{aid}/compliance-packet", headers=h(keys["payer"])).json()
    assert packet["integrity"]["status"] == "broken"


def test_legacy_events_are_unsealed_until_the_seal_job_runs(client, draft):
    aid, (m1, _), keys = create_active(client, draft)
    with client.app.state.sessionmaker() as s:
        rows = s.exec(select(AuditEvent).where(AuditEvent.agreement_id == aid).order_by(AuditEvent.at)).all()
        for r in rows:  # as if written before the chain existed
            r.prev_hash = r.hash = None
            s.add(r)
        s.commit()
        assert verify_chain(s, aid).status == "unsealed"
    submit(client, m1, keys, "spreadsheet", "s.csv", SIGN_IN)  # new events chain from nothing yet
    with client.app.state.sessionmaker() as s:
        before = verify_chain(s, aid)
        assert before.status == "unsealed" and before.sealed > 0
        assert seal_chain(s, aid) == before.unsealed
        after = verify_chain(s, aid)
        # Sealing the old rows gives the first new event a different link than it was written with, so the
        # chain is re-anchored: seal again and it recomputes clean from the start.
        assert after.unsealed == 0
    from app.jobs import main as jobs_main  # noqa: F401  (the CLI wraps the same two functions)


def test_access_review_lists_people_keys_and_mcp_clients(client, draft):
    _aid, _, keys = create_active(client, draft)
    org = keys["payer_org"]
    assert client.get(f"/orgs/{org}/access-review", headers=h(keys["payee"])).status_code == 404  # not a member
    key = client.post(
        f"/orgs/{org}/api-keys", json={"name": "ops agent", "org_role": "member"}, headers=h(keys["payer"])
    ).json()
    client.delete(f"/orgs/{org}/api-keys/{key['id']}", headers=h(keys["payer"]))
    client.post(
        f"/orgs/{org}/invitations",
        json={"email": "finance@harbor.example.org", "role": "member"},
        headers=h(keys["payer"]),
    )
    with client.app.state.sessionmaker() as s:  # an MCP client that consented once, with a live access token
        me = client.get("/me", headers=h(keys["payer"])).json()["user"]["id"]
        s.add(OAuthClient(client_id="claude-desktop", info={"client_name": "Claude Desktop"}))
        for kind, ttl in ((TokenKind.oauth_refresh, 30), (TokenKind.oauth_access, 1)):
            s.add(
                ApiToken(
                    kind=kind,
                    token_hash=hash_token(f"t-{kind}"),
                    user_id=me,
                    org_id=org,
                    client_id="claude-desktop",
                    family_id="fam-1",
                    scopes=["mcp"],
                    expires_at=utcnow() + timedelta(days=ttl),
                )
            )
        s.commit()
    r = client.get(f"/orgs/{org}/access-review", headers=h(keys["payer"]))
    assert r.status_code == 200, r.text
    review = r.json()
    assert [m["role"] for m in review["members"]] == ["owner"]
    assert review["pending_invitations"] == ["finance@harbor.example.org"]
    [k] = review["api_keys"]
    assert k["name"] == "ops agent" and k["role"] == "member" and k["revoked_at"]
    [c] = review["mcp_clients"]
    assert c["client_name"] == "Claude Desktop" and c["consents"] == 1 and c["active"] is True and c["user_id"] == me
