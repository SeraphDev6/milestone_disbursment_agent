from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.spa import mount_spa


def make(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "favicon.svg").write_text("<svg/>")
    (tmp_path / "index.html").write_text("<!doctype html><div id=root></div>")
    app = FastAPI()
    assert mount_spa(app, tmp_path)
    return TestClient(app)


def test_client_routes_fall_back_to_index(tmp_path):
    c = make(tmp_path)
    for path in ("/app/", "/app/o/123/a/456/m/789", "/app/sign-in"):
        r = c.get(path)
        assert r.status_code == 200 and "id=root" in r.text, path
    assert c.get("/app", follow_redirects=False).status_code == 308


def test_files_are_served_and_missing_assets_404(tmp_path):
    c = make(tmp_path)
    assert c.get("/app/assets/app.js").text == "console.log(1)"
    assert c.get("/app/favicon.svg").text == "<svg/>"
    assert c.get("/app/assets/missing.js").status_code == 404


def test_no_escape_from_dist(tmp_path):
    c = make(tmp_path)
    (tmp_path.parent / "secret.txt").write_text("nope")
    assert "nope" not in c.get("/app/../secret.txt").text
    assert "nope" not in c.get("/app/%2e%2e/secret.txt").text


def test_no_build_mounts_nothing(tmp_path):
    assert mount_spa(FastAPI(), tmp_path / "missing") is False


def test_root_redirects_to_the_web_app_or_the_docs(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == ("/app/" if client.app.state.spa else "/docs")
    client.app.state.spa = not client.app.state.spa
    r = client.get("/", follow_redirects=False)
    assert r.headers["location"] == ("/app/" if client.app.state.spa else "/docs")
