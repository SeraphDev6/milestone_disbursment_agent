"""The SDK examples do what their docstrings say: the quickstart runs the core flow against the hermetic app (it
is an httpx client, like the real thing), and the webhook receiver marks a settled tranche paid."""

import importlib.util
import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.services.outbound import sign
from tests.conftest import hermetic_app

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "sdk" / "python" / "examples"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, EXAMPLES / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_quickstart_runs_the_core_flow_to_a_verified_tranche():
    lines = []
    with TestClient(hermetic_app()) as api:
        result = load("quickstart").run(api, out=lines.append)
        assert result["status"] == "verified" and result["approve_url"]
        a = api.get(f"/agreements/{result['agreement_id']}", headers={"Authorization": "Bearer x"})
        assert a.status_code == 401  # the quickstart's sessions are the demo's people, not ours
    assert [line[:2] for line in lines] == ["1.", "2.", "3.", "4.", "5."]
    assert "lint ok: True" in lines[0] and "agreement is active" in lines[1]
    assert "met=True" in lines[3] and "214 rows" in lines[3] and "9800.50" in lines[3]
    assert "tranche is verified" in lines[4]


def test_webhook_receiver_marks_settled_tranches_paid_once(tmp_path):
    receiver = load("webhook_receiver")
    records = tmp_path / "paid.json"
    secret = "whsec_test"

    def delivery(payload: dict, delivery_id: str) -> tuple[bytes, dict]:
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        ts = str(int(time.time()))
        return body, {
            "X-MDE-Event": payload["type"],
            "X-MDE-Delivery": delivery_id,
            "X-MDE-Timestamp": ts,
            "X-MDE-Signature": sign(secret, ts, body),
        }

    settled = {
        "id": "ev1",
        "type": "milestone.status_changed",
        "created_at": "2027-06-01T12:00:00+00:00",
        "agreement_id": "ag1",
        "milestone_id": "ms1",
        "data": {
            "action": "captured",
            "actor": "system:gate",
            "from_status": "settling",
            "to_status": "settled",
            "detail": {},
        },
    }
    body, headers = delivery(settled, "d1")
    assert receiver.handle(body, headers, secret, records) == (200, "marked ms1 paid")
    assert receiver.handle(body, headers, secret, records) == (200, "already handled")  # a retry changes nothing
    assert [p["milestone_id"] for p in json.loads(records.read_text())["paid"]] == ["ms1"]

    other = {
        **settled,
        "id": "ev2",
        "data": {
            **settled["data"],
            "action": "evidence_received",
            "from_status": "funded",
            "to_status": "evidence_received",
        },
    }
    body, headers = delivery(other, "d2")
    assert receiver.handle(body, headers, secret, records)[1].startswith("noted milestone.status_changed")

    status, message = receiver.handle(body, {**headers, "X-MDE-Signature": "sha256=" + "0" * 64}, secret, records)
    assert status == 400 and message.startswith("signature_mismatch")
    assert len(json.loads(records.read_text())["paid"]) == 1
