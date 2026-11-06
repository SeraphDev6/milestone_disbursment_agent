"""The GitHub App: installing it for an org (and the checks that keep an installation from landing in the wrong org),
evidence through the app's webhook, per-installation reads, and the check run that follows a tranche."""

import copy
import hashlib
import hmac
import json
import re
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlmodel import select

from app.integrations.github import GitHubError
from app.integrations.github_app import MockGitHubApp, RealGitHubApp
from app.models import GitHubCheck, GitHubInstallation, Milestone
from app.services import github_app as service
from tests.helpers import create_active, h, new_org, sign_in
from tests.test_adapters import SOW, pr_event


def app_of(client) -> MockGitHubApp:
    return client.app.state.github_app


def claim_from(page: str) -> str:
    return re.search(r"<textarea[^>]*>([^<]+)</textarea>", page).group(1)  # type: ignore[union-attr]


def install(client, token, org_id, account="devco", repos=("devco/shop",)) -> str:
    """Run the install flow as GitHub would: start, mock install page, callback. Returns the claim."""
    r = client.post(f"/orgs/{org_id}/github/install", headers=h(token))
    assert r.status_code == 200, r.text
    url = r.json()["install_url"]
    state = re.search(r"state=([^&]+)", url).group(1)  # type: ignore[union-attr]
    from urllib.parse import unquote

    page = client.post(
        "/mock/github/install", data={"state": unquote(state), "account": account, "repos": ", ".join(repos)}
    )
    assert page.status_code == 200 and "Confirm the GitHub connection" in page.text, page.text
    return claim_from(page.text)


def link(client, token, org_id, **kw) -> dict:
    r = client.post(
        f"/orgs/{org_id}/github/installations", json={"claim": install(client, token, org_id, **kw)}, headers=h(token)
    )
    assert r.status_code == 200, r.text
    return r.json()


def app_delivery(client, payload, event="pull_request", delivery="d1", secret=None):
    body = json.dumps(payload).encode()
    key = (secret or app_of(client).webhook_secret).encode()
    sig = "sha256=" + hmac.new(key, body, hashlib.sha256).hexdigest()
    return client.post(
        "/webhooks/github/app",
        content=body,
        headers={"X-GitHub-Event": event, "X-GitHub-Delivery": delivery, "X-Hub-Signature-256": sig},
    )


@pytest.fixture
def sow(client):
    aid, (m1,), keys = create_active(client, copy.deepcopy(SOW), auto_release=True)
    return aid, m1, keys


# --- installing --------------------------------------------------------------------------


def test_install_links_the_installation_to_the_org(client, sow):
    _, _, keys = sow
    status = client.get(f"/orgs/{keys['payee_org']}/github", headers=h(keys["payee"])).json()
    assert status["app_available"] and status["installations"] == []
    row = link(client, keys["payee"], keys["payee_org"])
    assert row["account_login"] == "devco"
    status = client.get(f"/orgs/{keys['payee_org']}/github", headers=h(keys["payee"])).json()
    assert [i["installation_id"] for i in status["installations"]] == [row["installation_id"]]
    repos = client.get(f"/orgs/{keys['payee_org']}/github/repos", headers=h(keys["payee"])).json()
    assert repos == [{"repo": "devco/shop", "installation_id": row["installation_id"]}]

    r = client.delete(
        f"/orgs/{keys['payee_org']}/github/installations/{row['installation_id']}", headers=h(keys["payee"])
    )
    assert r.status_code == 204
    assert client.get(f"/orgs/{keys['payee_org']}/github", headers=h(keys["payee"])).json()["installations"] == []


def test_claim_only_works_for_the_admin_and_org_that_started_it(client, sow):
    _, _, keys = sow
    claim = install(client, keys["payee"], keys["payee_org"])
    # The payer can't confirm the payee's claim into their own org...
    r = client.post(f"/orgs/{keys['payer_org']}/github/installations", json={"claim": claim}, headers=h(keys["payer"]))
    assert r.status_code == 403
    # ...nor can another admin of the payee org who didn't start it.
    other = sign_in(client, "second@riverside.example.org")
    other_org = new_org(client, other, "Elsewhere")
    r = client.post(f"/orgs/{other_org}/github/installations", json={"claim": claim}, headers=h(other))
    assert r.status_code == 403
    r = client.post(
        f"/orgs/{keys['payee_org']}/github/installations", json={"claim": "garbage"}, headers=h(keys["payee"])
    )
    assert r.status_code == 400


def test_forged_installation_id_is_refused(client, sow):
    _, _, keys = sow
    victim_id, _ = app_of(client).install("victim-co", ["victim-co/secret"])
    url = client.post(f"/orgs/{keys['payee_org']}/github/install", headers=h(keys["payee"])).json()["install_url"]
    from urllib.parse import parse_qs, urlsplit

    state = parse_qs(urlsplit(url).query)["state"][0]
    _, my_code = app_of(client).install("devco", ["devco/shop"])
    # My OAuth code, someone else's installation id.
    r = client.get("/github/app/callback", params={"code": my_code, "installation_id": victim_id, "state": state})
    assert r.status_code == 403 and "access that installation" in r.text


def test_install_link_works_once_and_expires(client, sow, monkeypatch):
    _, _, keys = sow
    from urllib.parse import parse_qs, urlsplit

    url = client.post(f"/orgs/{keys['payee_org']}/github/install", headers=h(keys["payee"])).json()["install_url"]
    state = parse_qs(urlsplit(url).query)["state"][0]
    iid, code = app_of(client).install("devco", ["devco/shop"])
    assert (
        client.get("/github/app/callback", params={"code": code, "installation_id": iid, "state": state}).status_code
        == 200
    )
    _, code2 = app_of(client).install("devco", ["devco/shop"])
    app_of(client).codes[code2] = iid
    again = client.get("/github/app/callback", params={"code": code2, "installation_id": iid, "state": state})
    assert again.status_code == 409

    monkeypatch.setattr(service, "STATE_TTL", -1)
    url = client.post(f"/orgs/{keys['payee_org']}/github/install", headers=h(keys["payee"])).json()["install_url"]
    state = parse_qs(urlsplit(url).query)["state"][0]
    iid, code = app_of(client).install("devco", ["devco/shop"])
    r = client.get("/github/app/callback", params={"code": code, "installation_id": iid, "state": state})
    assert r.status_code == 400 and "expired" in r.text


def test_only_people_who_administer_the_org_can_install(client, sow):
    _, _, keys = sow
    key = client.post(
        f"/orgs/{keys['payee_org']}/api-keys", json={"name": "ci", "org_role": "admin"}, headers=h(keys["payee"])
    ).json()
    assert client.post(f"/orgs/{keys['payee_org']}/github/install", headers=h(key["token"])).status_code == 403
    assert client.post(f"/orgs/{keys['payee_org']}/github/install", headers=h(keys["payer"])).status_code == 404


def test_callback_sends_the_browser_to_the_web_app(client, sow):
    _, _, keys = sow
    client.app.state.app_url = "https://app.example.org"
    from urllib.parse import parse_qs, urlsplit

    url = client.post(f"/orgs/{keys['payee_org']}/github/install", headers=h(keys["payee"])).json()["install_url"]
    state = parse_qs(urlsplit(url).query)["state"][0]
    iid, code = app_of(client).install("devco", ["devco/shop"])
    r = client.get(
        "/github/app/callback",
        params={"code": code, "installation_id": iid, "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 303
    target = r.headers["location"]
    assert target.startswith(f"https://app.example.org/o/{keys['payee_org']}/settings?github_claim=")
    assert target.endswith("#github")


# --- connecting a repo and evidence through the app ----------------------------------------


def test_repo_connected_through_the_app_needs_no_webhook(client, sow):
    aid, _m1, keys = sow
    link(client, keys["payee"], keys["payee_org"])
    r = client.post(f"/agreements/{aid}/integrations/github", json={"repo": "devco/shop"}, headers=h(keys["payee"]))
    assert r.json()["via_app"] and r.json()["webhook_url"] is None and r.json()["secret"] is None
    forced = client.post(
        f"/agreements/{aid}/integrations/github",
        json={"repo": "devco/other", "use_app": False},
        headers=h(keys["payee"]),
    )
    assert not forced.json()["via_app"] and forced.json()["webhook_url"]


def test_merged_pr_through_the_app_becomes_evidence_and_a_check_run(client, sow):
    aid, m1, keys = sow
    inst = link(client, keys["payee"], keys["payee_org"])
    client.post(f"/agreements/{aid}/integrations/github", json={"repo": "devco/shop"}, headers=h(keys["payee"]))
    client.app.state.github = None  # reads must go through the installation, not the server token
    url = client.post(f"/milestones/{m1}/fund", headers=h(keys["payer"])).json()["approve_url"]
    client.get(url)

    assert app_delivery(client, pr_event("devco/shop", [f"mde-{m1}"]), secret="wrong").status_code == 401
    r = app_delivery(client, pr_event("devco/shop", [f"mde-{m1}"]) | {"installation": {"id": inst["installation_id"]}})
    assert r.json() == {"outcome": "queued for 1 agreement(s)"}, r.text
    [ev] = client.get(f"/milestones/{m1}/evidence", headers=h(keys["payee"])).json()
    assert ev["check_results"] == {"ci_green": True} and ev["pinned_ref"] == "a" * 40

    with client.app.state.sessionmaker() as s:
        [check] = s.exec(select(GitHubCheck)).all()
        assert check.repo == "devco/shop" and check.installation_id == inst["installation_id"]
        status = s.get(Milestone, m1).status
        assert service.publish_checks(s, app_of(client), lambda m, o: f"https://app/{m.id}") == 1
        assert service.publish_checks(s, app_of(client), None) == 0  # nothing changed since
    [run] = app_of(client).check_runs.values()
    assert run["name"] == "Milestone Ledger" and run["head_sha"] == "a" * 40
    assert run["conclusion"] == "success" and status == "settled" and run["output"]["title"] == "Paid"
    assert run["details_url"] == f"https://app/{m1}"


def test_check_run_follows_the_tranche_and_updates_in_place(client, sow):
    _aid, m1, _keys = sow
    with client.app.state.sessionmaker() as s:
        m = s.get(Milestone, m1)
        service.track(s, m, 77, "devco/shop", "c" * 40)
        s.commit()
        assert service.publish_checks(s, app_of(client), None) == 1
        m.status = "needs_review"
        s.commit()
        assert service.publish_checks(s, app_of(client), None) == 1
        [check] = s.exec(select(GitHubCheck)).all()
    assert list(app_of(client).check_runs) == [check.check_run_id]  # one run, updated
    assert app_of(client).check_runs[check.check_run_id]["output"]["title"] == "Waiting for a person to review"


def test_check_run_failures_are_kept_and_retried(client, sow):
    _, m1, _ = sow

    class Down(MockGitHubApp):
        def upsert_check_run(self, *a, **k):
            raise GitHubError("GitHub 502")

    with client.app.state.sessionmaker() as s:
        service.track(s, s.get(Milestone, m1), 77, "devco/shop", "c" * 40)
        s.commit()
        assert service.publish_checks(s, Down(base_url="x"), None) == 0
        [check] = s.exec(select(GitHubCheck)).all()
        assert check.error == "GitHub 502" and check.posted_state is None
        assert service.publish_checks(s, app_of(client), None) == 1


def test_uninstalling_unlinks_every_org(client, sow):
    _, _, keys = sow
    inst = link(client, keys["payee"], keys["payee_org"])
    r = app_delivery(
        client, {"action": "deleted", "installation": {"id": inst["installation_id"]}}, event="installation"
    )
    assert "unlinked from 1" in r.json()["outcome"]
    with client.app.state.sessionmaker() as s:
        assert s.exec(select(GitHubInstallation)).all() == []
    assert app_delivery(client, {}, event="installation", delivery="d1").json() == {"outcome": "duplicate"}


def test_handover_reads_the_clients_repo_through_their_installation(client, draft):
    from tests.test_verified_delivery import _code_evidence, setup

    _aid, m2, ctx = setup(client, draft)
    link(client, ctx["payer"], ctx["payer_org"], account="harbor", repos=("harbor/site",))
    pinned = "a" * 40
    app_of(client).commits[("harbor/site", "main")] = ("d" * 40, "tree1")
    app_of(client).commits[("river/app", pinned)] = (pinned, "tree1")
    link(client, ctx["payee"], ctx["payee_org"], account="river", repos=("river/app",))
    client.app.state.github = None  # would fail if the check used the server token
    eid = _code_evidence(client, m2, ctx, pinned)
    r = client.post(f"/evidence/{eid}/handover", json={"repo": "harbor/site", "ref": "main"}, headers=h(ctx["payer"]))
    assert r.status_code == 200, r.text
    assert r.json()["matches"] and r.json()["how"] == "same_tree"


# --- the real app's authentication -------------------------------------------------------


def _key() -> tuple[str, rsa.RSAPublicKey]:
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return pem.decode(), k.public_key()


def test_real_app_signs_a_jwt_and_caches_installation_tokens():
    pem, public = _key()
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path, req.headers.get("authorization", "")))
        if req.url.path == "/app/installations/5/access_tokens":
            return httpx.Response(201, json={"token": "ghs_inst", "expires_at": "2099-01-01T00:00:00Z"})
        if req.url.path == "/installation/repositories":
            return httpx.Response(200, json={"repositories": [{"full_name": "devco/shop"}]})
        if req.url.path == "/login/oauth/access_token":
            body = json.loads(req.content)
            return httpx.Response(200, json={"access_token": "ghu_user"} if body["code"] == "ok" else {"error": "bad"})
        if req.url.path == "/user/installations":
            return httpx.Response(200, json={"installations": [{"id": 5}]})
        return httpx.Response(404)

    app = RealGitHubApp(
        app_id="123",
        slug="milestone-ledger",
        client_id="Iv1.x",
        client_secret="s",
        private_key=pem.replace("\n", "\\n"),  # env vars often carry the PEM on one line
        webhook_secret="w",
        api_url="https://api.test",
        web_url="https://web.test",
        transport=httpx.MockTransport(handler),
    )
    claims = jwt.decode(app.app_jwt(), public, algorithms=["RS256"])
    assert claims["iss"] == "123" and claims["exp"] - claims["iat"] == 600 and claims["iat"] < time.time()
    assert app.repos(5) == ["devco/shop"] and app.repos(5) == ["devco/shop"]
    assert [c[1] for c in calls].count("/app/installations/5/access_tokens") == 1  # cached
    assert calls[-1][2] == "Bearer ghs_inst"
    assert app.exchange_code("ok") == "ghu_user" and app.user_installation_ids("ghu_user") == {5}
    with pytest.raises(GitHubError):
        app.exchange_code("nope")
    assert app.install_url("s t") == "https://web.test/apps/milestone-ledger/installations/new?state=s+t"


# --- registering the app (scripts/github_app_setup.py) -------------------------------------


def _setup_script():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "scripts" / "github_app_setup.py"
    spec = importlib.util.spec_from_file_location("github_app_setup", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_setup_manifest_points_github_at_this_deployment():
    setup = _setup_script()
    m = setup.manifest("https://app.example.org/", "http://127.0.0.1:8765/created", "Milestone Ledger", True)
    assert m["hook_attributes"]["url"] == "https://app.example.org/api/webhooks/github/app"
    assert m["callback_urls"] == ["https://app.example.org/api/github/app/callback"]
    assert m["request_oauth_on_install"] is True  # the install flow's access check depends on it
    assert m["default_permissions"] == {  # contents and PR write: only for handing paid work over
        "contents": "write",
        "metadata": "read",
        "pull_requests": "write",
        "checks": "write",
    }
    assert set(m["default_events"]) == {"pull_request", "release", "push", "repository"}


def test_setup_credentials_load_into_settings(tmp_path):
    from app.config import Settings
    from app.integrations.github_app import make_github_app

    setup = _setup_script()
    pem, public = _key()
    conversion = {
        "id": 42,
        "slug": "milestone-ledger",
        "client_id": "Iv1.abc",
        "client_secret": "cs",
        "webhook_secret": "ws",
        "pem": pem,
    }

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/app-manifests/the-code/conversions"
        return httpx.Response(201, json=conversion)

    assert setup.convert("the-code", httpx.MockTransport(handler)) == conversion
    out = tmp_path / "github-app.env"
    setup.write_private(out, setup.env_lines(conversion))
    assert oct(out.stat().st_mode & 0o777) == "0o600"
    settings = Settings(
        _env_file=str(out),
        paypal_client_id="id",
        paypal_client_secret="s",
        secret_key="k" * 32,
        database_url="sqlite://",
    )
    assert settings.github_app_configured
    app = make_github_app(settings)
    assert isinstance(app, RealGitHubApp) and app.slug == "milestone-ledger"
    assert jwt.decode(app.app_jwt(), public, algorithms=["RS256"])["iss"] == "42"
