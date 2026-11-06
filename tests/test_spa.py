"""The web build at the site root, the API under /api and the MCP server at /mcp, through the Site dispatcher."""

from fastapi.testclient import TestClient

from app.api.spa import make_spa
from app.main import Site, create_site
from tests.conftest import hermetic_app, hermetic_settings


def make(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "favicon.svg").write_text("<svg/>")
    (tmp_path / "index.html").write_text("<!doctype html><div id=root></div>")
    return TestClient(make_spa(tmp_path))


def test_client_routes_fall_back_to_index(tmp_path):
    c = make(tmp_path)
    for path in ("/", "/o/123/a/456/m/789", "/sign-in", "/demo"):
        r = c.get(path)
        assert r.status_code == 200 and "id=root" in r.text, path
    assert c.get("/app/o/1", follow_redirects=False).headers["location"] == "/o/1"  # the old prefix
    # Links emailed while the app lived under /app keep their token across the redirect.
    r = c.get("/app/auth/verify?token=mdl_x&keep=0", follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"] == "/auth/verify?token=mdl_x&keep=0"


def test_files_are_served_and_missing_assets_404(tmp_path):
    c = make(tmp_path)
    assert c.get("/assets/app.js").text == "console.log(1)"
    assert c.get("/favicon.svg").text == "<svg/>"
    assert c.get("/assets/missing.js").status_code == 404


def test_no_escape_from_dist(tmp_path):
    c = make(tmp_path)
    (tmp_path.parent / "secret.txt").write_text("nope")
    assert "nope" not in c.get("/../secret.txt").text
    assert "nope" not in c.get("/%2e%2e/secret.txt").text


def test_without_a_build_the_root_points_at_the_docs(tmp_path):
    c = TestClient(make_spa(tmp_path / "missing"))
    assert c.get("/", follow_redirects=False).headers["location"] == "/api/docs"
    assert c.get("/o/1").status_code == 404


def test_site_routes_api_mcp_and_app(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><div id=root></div>")
    api = hermetic_app(PUBLIC_BASE_URL="http://localhost")
    with TestClient(Site(api, make_spa(tmp_path)), base_url="http://localhost") as c:
        assert c.get("/api/health").json()["status"] == "ok"
        assert c.get("/health").status_code == 200 and "id=root" in c.get("/health").text  # not an API path
        docs = c.get("/api/docs")
        assert (
            docs.status_code == 200
            and "/api/openapi.json" in docs.text
            and "content-security-policy" not in docs.headers
        )
        assert c.get("/api/openapi.json").json()["servers"] == [{"url": "/api"}]
        prm = c.get("/.well-known/oauth-protected-resource/mcp").json()
        assert prm["resource"] == "http://localhost/mcp"
        assert c.post("/mcp", json={}).status_code in (400, 401, 406)  # the MCP server answered, not the app
        assert "id=root" in c.get("/o/1/a/2/m/3").text
        # the API's own browser pages, and the links it builds, carry the prefix
        assert c.post("/api/auth/magic-link", json={"email": "pat@example.org"}).status_code == 200
        link = api.state.mailer.sent[-1].link
        assert link.startswith("http://localhost/api/auth/magic-link/verify?token=")
        page = c.get(link)
        assert page.status_code == 200 and "action='/api/auth/magic-link/complete'" in page.text
        gw = api.state.paypal
        inv = gw.create_and_send_invoice(
            recipient_email="p@x.example",
            item_name="i",
            amount=1,
            currency="USD",
            note="n",
            reference="r",
            request_id="k",
        )
        assert inv.payer_view_url.startswith("http://localhost/api/mock/paypal/invoices/")
        page = c.get(inv.payer_view_url)
        assert page.status_code == 200 and "action='/api/mock/paypal/invoices/" in page.text


def test_create_site_serves_the_build_when_configured(tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>app</title>")  # web/dist is gitignored; CI has none
    monkeypatch.setattr("app.main.make_spa", lambda dist=tmp_path: make_spa(dist))
    site = create_site(hermetic_settings(web_app_built=True))
    with TestClient(site) as c:
        assert c.get("/api/health").status_code == 200
        r = c.get("/", follow_redirects=False)
        assert (
            r.status_code == 200
            and "x-content-type-options" in r.headers
            and "content-security-policy" not in r.headers
        )
