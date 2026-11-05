"""Contractor-owned repos (#7): the work stays in the contractor's private repo, a release or tag pins the commit
that's verified, and the code reaches the client only after capture: a PR into their repo, or a source archive,
each checked against the verified commit's git tree first."""

import copy
import hashlib
import io
import shutil
import subprocess
import tarfile

import pytest
from sqlmodel import select

from app.integrations import gittree
from app.models import AuditEvent, GitHubHandover, Milestone
from app.services import handover
from tests.helpers import create_active, h
from tests.test_adapters import SOW, pr_event
from tests.test_escrow import fund
from tests.test_github_app import app_delivery, app_of, link

WORK = {"README.md": b"# Checkout\n", "src/payment_form.tsx": b"export const Form = () => null;\n"}


def sow_on_capture() -> dict:
    d = copy.deepcopy(SOW)
    d.update({"deliverable_release": "on_capture", "dispute_window_days": 3})
    return d


def release_event(repo: str, tag: str, body: str = "") -> dict:
    return {
        "action": "published",
        "repository": {"full_name": repo},
        "installation": {"id": 0},
        "release": {
            "tag_name": tag,
            "name": "Payment form",
            "body": body,
            "html_url": f"https://github.com/{repo}/releases/tag/{tag}",
            "author": {"login": "devco"},
            "published_at": "2027-03-01T12:00:00Z",
            "draft": False,
        },
    }


def tag_push(repo: str, tag: str) -> dict:
    return {
        "ref": f"refs/tags/{tag}",
        "deleted": False,
        "repository": {"full_name": repo},
        "sender": {"login": "devco"},
    }


@pytest.fixture
def deal(client):
    """Active on_capture SOW. The payee installed the app on its private repo, the payer on its own repo."""
    aid, (m1,), keys = create_active(client, sow_on_capture(), auto_release=False)
    payee_install = link(client, keys["payee"], keys["payee_org"], account="devco", repos=("devco/shop-work",))
    payer_install = link(client, keys["payer"], keys["payer_org"], account="acme", repos=("acme/shop",))
    keys |= {"payee_iid": payee_install["installation_id"], "payer_iid": payer_install["installation_id"]}
    return aid, m1, keys


def register(client, aid, keys, **body):
    payload = {"repo": "devco/shop-work", "client_repo": "acme/shop"} | body
    return client.post(f"/agreements/{aid}/integrations/github", json=payload, headers=h(keys["payee"]))


def deliver_release(client, keys, m1, tag=None, delivery="r1", files=WORK):
    gh = app_of(client)
    tag = tag or f"mde-{m1}"
    sha, tree = gh.push("devco/shop-work", files, tag=tag)
    payload = release_event("devco/shop-work", tag, body="Preview: https://preview.devco.dev/checkout\n")
    payload["installation"]["id"] = keys["payee_iid"]
    r = app_delivery(client, payload, event="release", delivery=delivery)
    assert r.status_code == 200, r.text
    return r.json()["outcome"], sha, tree


def approve(client, mid, keys):
    r = client.post(f"/milestones/{mid}/review", json={"decision": "approve"}, headers=h(keys["payer"]))
    assert r.status_code == 200, r.text


def audit(client, mid, action) -> list[AuditEvent]:
    with client.app.state.sessionmaker() as s:
        return list(s.exec(select(AuditEvent).where(AuditEvent.milestone_id == mid, AuditEvent.action == action)))


def status(client, mid) -> str:
    with client.app.state.sessionmaker() as s:
        return s.get(Milestone, mid).status


def work_handovers(client) -> int:
    with client.app.state.sessionmaker() as s:
        return handover.run(s, client.app.state.github_app)


def rows(client, mid) -> list[GitHubHandover]:
    with client.app.state.sessionmaker() as s:
        out = list(s.exec(select(GitHubHandover).where(GitHubHandover.milestone_id == mid)))
        for r in out:
            s.expunge(r)
        return out


# --- the tree check ----------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_tree_id_matches_git(tmp_path):
    repo = tmp_path / "r"
    (repo / "src" / "a").mkdir(parents=True)
    (repo / "README.md").write_text("hi\n")
    (repo / "src" / "a" / "f.py").write_text("x")
    (repo / "src.txt").write_text("sorts after src/ in git's order")
    (repo / "run.sh").write_text("#!/bin/sh\n")
    (repo / "run.sh").chmod(0o755)
    (repo / "link").symlink_to("README.md")

    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True).stdout

    git("init", "-q")
    git("add", "-A")
    git("-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "work")
    expected = git("rev-parse", "HEAD^{tree}").decode().strip()
    archive = git("archive", "--format=tar.gz", "--prefix=devco-shop-abc1234/", "HEAD")
    assert gittree.tree_id(gittree.untar(archive)) == expected


def test_tree_id_changes_with_any_byte_name_or_mode():
    base = [gittree.Entry("a.txt", "100644", b"x"), gittree.Entry("d/b.txt", "100644", b"y")]
    t = gittree.tree_id(base)
    assert gittree.tree_id(list(reversed(base))) == t  # order of the archive doesn't matter
    assert gittree.tree_id([base[0], gittree.Entry("d/b.txt", "100644", b"z")]) != t
    assert gittree.tree_id([base[0], gittree.Entry("d/c.txt", "100644", b"y")]) != t
    assert gittree.tree_id([base[0], gittree.Entry("d/b.txt", "100755", b"y")]) != t
    assert gittree.tree_id(base[:1]) != t


def test_untar_refuses_paths_that_escape():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo("top/../../etc/passwd")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(gittree.ArchiveError):
        gittree.untar(buf.getvalue())


# --- connecting the repo -----------------------------------------------------------------


def test_registration_defaults_to_contractor_repo_and_checks_the_client_cant_read_it(client, deal):
    aid, _, keys = deal
    merged = register(client, aid, keys, mode="merged_pr")
    assert merged.status_code == 409 and "contractor_repo" in merged.json()["detail"]

    r = register(client, aid, keys)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "contractor_repo" and body["via_app"] and body["handover"] == "pull_request"
    assert body["client_repo"] == "acme/shop" and body["handover_ready"] is True
    assert set(body["events"]) == {"pull_request", "release", "push"}

    assert register(client, aid, keys, client_repo=None).status_code == 422
    assert register(client, aid, keys, repo="devco/elsewhere").status_code == 422  # app can't read it

    app_of(client).repo_meta["devco/shop-work"] = {"private": False}
    public = register(client, aid, keys)
    assert public.status_code == 409 and "public" in public.json()["detail"]


def test_a_repo_the_client_can_already_read_isnt_withheld(client, deal):
    aid, _, keys = deal
    link(client, keys["payer"], keys["payer_org"], account="acme-two", repos=("devco/shop-work",))
    r = register(client, aid, keys)
    assert r.status_code == 409 and "can already read" in r.json()["detail"]


def test_contractor_repo_is_for_work_withheld_until_capture(client):
    aid, _, keys = create_active(client, copy.deepcopy(SOW))
    link(client, keys["payee"], keys["payee_org"], account="devco", repos=("devco/shop-work",))
    r = register(client, aid, keys, mode="contractor_repo")
    assert r.status_code == 422 and "on_capture" in r.json()["detail"]


# --- evidence from a release or tag -------------------------------------------------------


def test_release_pins_the_commit_and_the_payer_sees_no_source(client, deal):
    aid, m1, keys = deal
    assert register(client, aid, keys).status_code == 200
    fund(client, m1, keys)
    outcome, sha, tree = deliver_release(client, keys, m1)
    assert outcome == "queued for 1 agreement(s)"

    [ev] = client.get(f"/milestones/{m1}/evidence", headers=h(keys["payee"])).json()
    assert ev["pinned_ref"] == sha and ev["check_results"] == {"ci_green": True}
    assert tree in ev["excerpt"] and "https://preview.devco.dev/checkout" in ev["artifact_uris"]
    assert status(client, m1) == "needs_review"  # the payer approves before anything is captured
    [shown] = client.get(f"/milestones/{m1}/evidence", headers=h(keys["payer"])).json()
    assert shown["withheld"] and shown["excerpt"] is None and shown["artifact_uris"] == []
    assert shown["preview_url"] == "https://preview.devco.dev/checkout"  # the link the payee chose to share

    # A tag push for the same commit (GitHub sends both for a new release) adds nothing.
    push = tag_push("devco/shop-work", f"mde-{m1}") | {"installation": {"id": keys["payee_iid"]}}
    assert app_delivery(client, push, event="push", delivery="p1").json()["outcome"].startswith("queued")
    assert len(client.get(f"/milestones/{m1}/evidence", headers=h(keys["payee"])).json()) == 1

    # Verified, not yet paid: nothing has gone to the client.
    assert rows(client, m1) == [] and app_of(client).pulls == []


@pytest.mark.parametrize(
    ("tag", "counts"), [("mde-{m}-v2", True), ("mde-{m}.1", True), ("v1.0", False), ("mde-{m}x", False)]
)
def test_tag_names_that_count(client, deal, tag, counts):
    aid, m1, keys = deal
    assert register(client, aid, keys).status_code == 200
    fund(client, m1, keys)
    deliver_release(client, keys, m1, tag=tag.format(m=m1))
    evidence = client.get(f"/milestones/{m1}/evidence", headers=h(keys["payee"])).json()
    assert len(evidence) == (1 if counts else 0)


def test_labelled_prs_in_the_contractor_repo_still_count(client, deal):
    aid, m1, keys = deal
    assert register(client, aid, keys).status_code == 200
    payload = pr_event("devco/shop-work", [f"mde-{m1}"]) | {"installation": {"id": keys["payee_iid"]}}
    assert app_delivery(client, payload).json()["outcome"] == "queued for 1 agreement(s)"
    [ev] = client.get(f"/milestones/{m1}/evidence", headers=h(keys["payee"])).json()
    assert ev["pinned_ref"] == "a" * 40


# --- handover on capture ------------------------------------------------------------------


def test_capture_hands_the_verified_tree_over_as_a_pr(client, deal):
    aid, m1, keys = deal
    assert register(client, aid, keys).status_code == 200
    fund(client, m1, keys)
    _, sha, tree = deliver_release(client, keys, m1)
    approve(client, m1, keys)
    assert status(client, m1) == "held"  # captured; in the dispute window

    [row] = rows(client, m1)
    assert row.status == "pending" and row.pinned_sha == sha and row.method == "pull_request"
    assert app_of(client).pulls == []  # capture only queues it; GitHub is worked by the loop

    assert work_handovers(client) == 1
    [pr] = app_of(client).pulls
    assert pr["repo"] == "acme/shop" and pr["base"] == "main" and sha in pr["body"] and tree in pr["body"]
    [shown] = client.get(f"/milestones/{m1}/handovers", headers=h(keys["payer"])).json()
    assert shown["status"] == "delivered" and shown["tree_sha"] == tree and shown["pr_url"].endswith("/pull/1")
    [done] = audit(client, m1, "deliverable_handed_over")
    assert done.detail["matches"] and done.detail["tree_sha"] == tree
    assert work_handovers(client) == 0  # delivered once


def test_handover_waits_until_the_client_installs_the_app(client):
    aid, (m1,), keys = create_active(client, sow_on_capture())
    iid = link(client, keys["payee"], keys["payee_org"], account="devco", repos=("devco/shop-work",))
    keys["payee_iid"] = iid["installation_id"]
    r = register(client, aid, keys)
    assert r.status_code == 200 and r.json()["handover_ready"] is False
    fund(client, m1, keys)
    deliver_release(client, keys, m1)
    approve(client, m1, keys)

    assert work_handovers(client) == 0
    [row] = rows(client, m1)
    assert row.status == "pending" and row.attempts == 1 and "acme/shop" in row.error

    link(client, keys["payer"], keys["payer_org"], account="acme", repos=("acme/shop",))
    retried = client.post(f"/handovers/{row.id}/retry", headers=h(keys["payer"]))
    assert retried.status_code == 200 and retried.json()["status"] == "pending"
    assert work_handovers(client) == 1 and len(app_of(client).pulls) == 1


def test_an_archive_that_doesnt_reproduce_the_verified_tree_is_never_handed_over(client, deal):
    aid, m1, keys = deal
    assert register(client, aid, keys).status_code == 200
    fund(client, m1, keys)
    _, sha, tree = deliver_release(client, keys, m1)
    # The contractor's repo serves different code for that commit than what was verified.
    gh = app_of(client)
    gh.push("devco/shop-work", {**WORK, "src/payment_form.tsx": b"// something else\n"})
    swapped = next(v for (repo, s), v in gh.tarballs.items() if s != sha)
    gh.tarballs[("devco/shop-work", sha)] = swapped
    approve(client, m1, keys)

    assert work_handovers(client) == 0
    [row] = rows(client, m1)
    assert row.status == "failed" and "doesn't reproduce the verified tree" in row.error
    assert gh.pulls == []
    [bad] = audit(client, m1, "deliverable_mismatch")
    assert bad.detail["expected_tree"] == tree and bad.detail["got_tree"] != tree


def test_archive_handover_is_kept_and_downloads_with_its_hash(client, deal):
    aid, m1, keys = deal
    assert register(client, aid, keys, handover="archive", client_repo=None).status_code == 200
    fund(client, m1, keys)
    _, sha, tree = deliver_release(client, keys, m1)
    approve(client, m1, keys)
    assert work_handovers(client) == 1

    [shown] = client.get(f"/milestones/{m1}/handovers", headers=h(keys["payer"])).json()
    assert shown["method"] == "archive" and shown["archive_sha256"] and shown["pr_url"] is None
    r = client.get(f"/handovers/{shown['id']}/archive", headers=h(keys["payer"]))
    assert r.status_code == 200
    assert hashlib.sha256(r.content).hexdigest() == r.headers["x-content-sha256"] == shown["archive_sha256"]
    assert r.headers["x-git-tree"] == tree and r.headers["x-pinned-ref"] == sha
    assert gittree.tree_id(gittree.untar(r.content)) == tree
    assert audit(client, m1, "deliverable_downloaded")

    other = client.get(f"/handovers/{shown['id']}/archive", headers=h("not-a-token"))
    assert other.status_code == 401


def test_no_pr_when_github_stores_a_different_tree_in_the_client_repo(client, deal, monkeypatch):
    from app.integrations.github_app import MockGitHubClient

    aid, m1, keys = deal
    assert register(client, aid, keys).status_code == 200
    fund(client, m1, keys)
    deliver_release(client, keys, m1)
    approve(client, m1, keys)
    monkeypatch.setattr(MockGitHubClient, "create_tree", lambda self, repo, entries: "f" * 40)

    assert work_handovers(client) == 0
    [row] = rows(client, m1)
    assert row.status == "failed" and "different tree" in row.error and app_of(client).pulls == []
    [bad] = audit(client, m1, "deliverable_mismatch")
    assert bad.detail["stage"] == "push" and bad.detail["got_tree"] == "f" * 40


# --- transfer: the contractor hands the repo itself over; the app only watches ----------


def transferred(client, keys, new_name, delivery="t1"):
    gh = app_of(client)
    payload = {
        "action": "transferred",
        "repository": {"id": gh.repo_ids[new_name], "full_name": new_name},
        "changes": {"owner": {"from": {"user": {"login": "devco"}}}},
        "installation": {"id": keys["payer_iid"]},
    }
    r = app_delivery(client, payload, event="repository", delivery=delivery)
    assert r.status_code == 200, r.text
    return r.json()["outcome"]


def test_transfer_is_watched_for_and_never_given_up_on(client, deal):
    aid, m1, keys = deal
    r = register(client, aid, keys, handover="transfer", client_repo=None)
    assert r.status_code == 200, r.text
    assert r.json()["handover_ready"] is True and r.json()["transfer_to"] == ["acme"]
    fund(client, m1, keys)
    _, sha, tree = deliver_release(client, keys, m1)
    approve(client, m1, keys)

    for _ in range(handover.MAX_ATTEMPTS + 2):  # the contractor transfers when ready; no failure for waiting
        [row] = rows(client, m1)
        client.post(f"/handovers/{row.id}/retry", headers=h(keys["payee"]))
        assert work_handovers(client) == 0
    [row] = rows(client, m1)
    assert row.status == "pending" and "hasn't been transferred" in row.error
    [shown] = client.get(f"/milestones/{m1}/handovers", headers=h(keys["payee"])).json()
    assert shown["transfer_to"] == ["acme"]

    new = app_of(client).transfer("devco/shop-work", "acme")
    assert transferred(client, keys, new) == "1 handover(s) to check"  # checked straight away, in the background
    [shown] = client.get(f"/milestones/{m1}/handovers", headers=h(keys["payer"])).json()
    assert shown["status"] == "delivered" and shown["transferred_to"] == "acme/shop-work"
    assert shown["tree_sha"] == tree and shown["pinned_sha"] == sha
    assert app_of(client).pulls == []  # nothing written to GitHub
    [done] = audit(client, m1, "deliverable_handed_over")
    assert done.detail["transferred_to"] == "acme/shop-work" and done.detail["matches"]


def test_a_transfer_also_completes_a_pr_handover(client, deal):
    aid, m1, keys = deal
    assert register(client, aid, keys).status_code == 200  # handover=pull_request
    fund(client, m1, keys)
    deliver_release(client, keys, m1)
    approve(client, m1, keys)
    app_of(client).transfer("devco/shop-work", "acme")  # before the loop got to it

    assert work_handovers(client) == 1
    [row] = rows(client, m1)
    assert row.status == "delivered" and row.transferred_to == "acme/shop-work" and row.pr_url is None
    assert app_of(client).pulls == []


def test_a_transfer_without_the_verified_commit_on_the_default_branch_keeps_waiting(client, deal):
    aid, m1, keys = deal
    assert register(client, aid, keys, handover="transfer", client_repo=None).status_code == 200
    fund(client, m1, keys)
    _, sha, _ = deliver_release(client, keys, m1)
    approve(client, m1, keys)
    gh = app_of(client)
    gh.history["devco/shop-work"].remove(sha)  # e.g. only on the tag, or the branch was rewritten
    gh.transfer("devco/shop-work", "acme")

    assert work_handovers(client) == 0
    [row] = rows(client, m1)
    assert row.status == "pending" and "isn't on its default branch" in row.error and row.transferred_to is None

    gh.history["acme/shop-work"].append(sha)
    client.post(f"/handovers/{row.id}/retry", headers=h(keys["payer"]))
    assert work_handovers(client) == 1


def test_a_transfer_to_someone_other_than_the_client_doesnt_count(client, deal):
    aid, m1, keys = deal
    assert register(client, aid, keys, handover="transfer", client_repo=None).status_code == 200
    fund(client, m1, keys)
    deliver_release(client, keys, m1)
    approve(client, m1, keys)
    gh = app_of(client)
    gh.install("elsewhere", [])
    new = gh.transfer("devco/shop-work", "elsewhere")

    assert transferred(client, keys, new) == "1 handover(s) to check"
    [row] = rows(client, m1)
    assert row.status == "pending" and row.transferred_to is None


def test_after_a_transfer_the_connection_follows_the_repo(client):
    from app.models import Evidence, GitHubCheck, Integration

    sow = sow_on_capture()
    second = copy.deepcopy(sow["milestones"][0]) | {"title": "Milestone 2: receipts", "amount": "3000.00"}
    sow["milestones"] = [sow["milestones"][0] | {"amount": "3000.00"}, second]
    aid, (m1, m2), keys = create_active(client, sow)
    keys["payee_iid"] = link(client, keys["payee"], keys["payee_org"], account="devco", repos=("devco/shop-work",))[
        "installation_id"
    ]
    keys["payer_iid"] = link(client, keys["payer"], keys["payer_org"], account="acme", repos=("acme/shop",))[
        "installation_id"
    ]
    assert register(client, aid, keys, handover="transfer", client_repo=None).status_code == 200
    fund(client, m1, keys)
    deliver_release(client, keys, m1)
    approve(client, m1, keys)
    gh = app_of(client)
    new = gh.transfer("devco/shop-work", "acme")
    assert transferred(client, keys, new) == "1 handover(s) to check"
    assert rows(client, m1)[0].status == "delivered"

    with client.app.state.sessionmaker() as s:
        [integration] = s.exec(select(Integration).where(Integration.agreement_id == aid)).all()
        assert (integration.target, integration.installation_id) == ("acme/shop-work", keys["payer_iid"])
        [check] = s.exec(select(GitHubCheck).where(GitHubCheck.milestone_id == m1)).all()
        assert (check.repo, check.installation_id) == ("acme/shop-work", keys["payer_iid"])
    [moved] = [e for e in audit_all(client, aid) if e.action == "github_repo_moved"]
    assert moved.detail["from"] == "devco/shop-work" and moved.detail["to"] == "acme/shop-work"

    # The next tranche's release, now in the client's repo, arrives through the client's installation and counts.
    sha, _ = gh.push(new, {"src/receipts.tsx": b"export const R = 1;\n"}, tag=f"mde-{m2}")
    payload = release_event(new, f"mde-{m2}") | {"installation": {"id": keys["payer_iid"]}}
    r = app_delivery(client, payload, event="release", delivery="r2")
    assert r.json()["outcome"] == "queued for 1 agreement(s)", r.text
    with client.app.state.sessionmaker() as s:
        [ev] = s.exec(select(Evidence).where(Evidence.milestone_id == m2)).all()
        assert ev.pinned_ref == sha


def audit_all(client, aid) -> list[AuditEvent]:
    with client.app.state.sessionmaker() as s:
        return list(s.exec(select(AuditEvent).where(AuditEvent.agreement_id == aid)))
