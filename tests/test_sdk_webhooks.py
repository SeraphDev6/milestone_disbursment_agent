"""The SDKs' webhook verification against deliveries signed by the engine's own signer (sdk/fixtures/webhooks.json,
written by scripts/sdk_fixtures.py), and the SDKs' status lists against the engine's enums."""

import importlib.util
import json
import re
from pathlib import Path

import pytest
from milestone_ledger import AGREEMENT_STATUSES, MILESTONE_STATUSES, WebhookVerificationError, verify_webhook

from app.models import AgreementStatus, MilestoneStatus

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "sdk" / "fixtures" / "webhooks.json"
TS_SOURCE = ROOT / "sdk" / "typescript" / "src" / "webhooks.ts"


def _fixtures_module():
    spec = importlib.util.spec_from_file_location("sdk_fixtures", ROOT / "scripts" / "sdk_fixtures.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fixtures_are_up_to_date():
    assert FIXTURES.read_text() == _fixtures_module().render(), "run `uv run python scripts/sdk_fixtures.py`"


def cases():
    doc = json.loads(FIXTURES.read_text())
    return [pytest.param(doc, c, id=c["name"]) for c in doc["cases"]]


@pytest.mark.parametrize(("doc", "case"), cases())
def test_python_sdk_verifies_what_the_engine_signed(doc, case):
    kwargs = {"tolerance_seconds": doc["tolerance_seconds"], "now": doc["now"]}
    if case["expect"] == "ok":
        event = verify_webhook(case["body"].encode(), case["headers"], doc["secret"], **kwargs)
        assert event.raw == case["event"] and event.delivery_id == case["delivery_id"]
        assert event.type == case["event"]["type"] and event.agreement_id == case["event"]["agreement_id"]
        assert event.data.action == case["event"]["data"]["action"]
        assert event.data.to_status == case["event"]["data"]["to_status"]
        assert event.scope in ("milestone", "agreement") and event.is_status_change == bool(event.data.to_status)
    else:
        with pytest.raises(WebhookVerificationError) as e:
            verify_webhook(case["body"].encode(), case["headers"], doc["secret"], **kwargs)
        assert e.value.code == case["expect"], str(e.value)


def test_a_str_body_and_framework_style_headers_work():
    doc = json.loads(FIXTURES.read_text())
    ok = next(c for c in doc["cases"] if c["expect"] == "ok")
    wsgi = {"HTTP_" + k.upper().replace("-", "_"): v for k, v in ok["headers"].items()}
    event = verify_webhook(ok["body"], wsgi, doc["secret"], now=doc["now"])
    assert event.id == ok["event"]["id"]
    # The default clock is now: an old fixture is a replay.
    with pytest.raises(WebhookVerificationError) as e:
        verify_webhook(ok["body"], ok["headers"], doc["secret"])
    assert e.value.code == "timestamp_out_of_tolerance"


def test_sdk_status_lists_match_the_engine():
    assert MILESTONE_STATUSES == tuple(s.value for s in MilestoneStatus)
    assert AGREEMENT_STATUSES == tuple(s.value for s in AgreementStatus)
    ts = TS_SOURCE.read_text()
    ts_milestone = re.search(r"MILESTONE_STATUSES = \[(.*?)\] as const", ts, re.DOTALL)
    ts_agreement = re.search(r"AGREEMENT_STATUSES = \[(.*?)\] as const", ts, re.DOTALL)
    assert ts_milestone and ts_agreement, "the TypeScript SDK's status lists moved"
    assert re.findall(r'"(\w+)"', ts_milestone.group(1)) == list(MILESTONE_STATUSES)
    assert re.findall(r'"(\w+)"', ts_agreement.group(1)) == list(AGREEMENT_STATUSES)
