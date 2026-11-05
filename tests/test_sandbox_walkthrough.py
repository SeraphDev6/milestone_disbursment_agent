"""scripts/sandbox_walkthrough.py runs every flow end to end against mock mode, so the script that proves the
sandbox wallet flow (issue #33) can't drift from the API. The "browser" steps follow PayPal's mock pages."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "sandbox_walkthrough.py"
spec = importlib.util.spec_from_file_location("sandbox_walkthrough", SCRIPT)
walkthrough = importlib.util.module_from_spec(spec)
spec.loader.exec_module(walkthrough)


class MockWalk(walkthrough.Walk):
    """No person at the keyboard: links come back in mock responses, and each PayPal page is 'approved' by
    following it, the way a browser would."""

    def __init__(self, client):
        super().__init__("http://localhost:8000", timeout=5, http=client, mode="mock")
        self.poll_seconds = 0
        self.visited: list[str] = []

    def ask(self, text: str) -> str:
        assert "Paste" not in text, f"mock mode should return the link instead of asking: {text}"
        return ""

    def open_in_browser(self, url: str, who: str) -> None:
        r = self.http.get(url)  # PayPal approval, onboarding or pay link, then back to the app
        assert r.status_code == 200, r.text
        self.visited.append(url)


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
    w.mode = "sandbox"
    with pytest.raises(SystemExit):
        w.preflight()
