"""scripts/sandbox_walkthrough.py runs every flow end to end against the stand-in PayPal, so the script that proves
the sandbox wallet flow (issue #33) can't drift from the API. The "browser" steps follow the stand-in's pages."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "sandbox_walkthrough.py"
spec = importlib.util.spec_from_file_location("sandbox_walkthrough", SCRIPT)
walkthrough = importlib.util.module_from_spec(spec)
spec.loader.exec_module(walkthrough)


class MockWalk(walkthrough.Walk):
    """No person at the keyboard: links are read from the test mailer, and each PayPal page is 'approved' by
    following it, the way a browser would."""

    def __init__(self, client):
        super().__init__("http://localhost:8000", timeout=5, http=client)
        self.poll_seconds = 0
        self.visited: list[str] = []

    def ask(self, text: str) -> str:
        raise AssertionError(f"nothing should need a person: {text}")

    def emailed_link(self, to: str, path: str, prompt: str) -> str:
        for mail in reversed(self.http.app.state.mailer.sent):
            if mail.to == to.lower() and path in mail.link:
                return mail.link
        raise AssertionError(f"no email to {to} with a {path} link")

    def open_in_browser(self, url: str, who: str, account: str | None = None) -> bool:
        r = self.http.get(url)  # PayPal approval, onboarding or pay link, then back to the app
        assert r.status_code == 200, r.text
        self.visited.append(url)
        return True


@pytest.mark.parametrize("flows", [["platform", "escrow", "paynow"], ["paynow"]])
def test_walkthrough_runs_every_flow(client, flows):
    w = MockWalk(client)
    walkthrough.run(w, flows, "funder@example.org", "grantee@example.org", "payer@pp.test", "payee@pp.test", "t1")
    # One approval per escrow flow, one onboarding per connected flow (the second finds it ready), one payment.
    assert len(w.visited) == flows.count("platform") + 2 * flows.count("escrow") + 2 * flows.count("paynow") - (
        1 if {"escrow", "paynow"} <= set(flows) else 0
    )


def test_walkthrough_refuses_the_wrong_mode(client):
    w = MockWalk(client)
    w.mode = "live"
    with pytest.raises(SystemExit):
        w.preflight()
