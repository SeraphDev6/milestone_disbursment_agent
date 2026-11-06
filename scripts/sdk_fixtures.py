"""Write sdk/fixtures/webhooks.json: deliveries signed by the engine's own signer, for the SDKs' tests.

Both SDKs verify these, so their `verifyWebhook` can't drift from app/services/outbound.py. Deterministic.

    uv run python scripts/sdk_fixtures.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db.audit import event_type_for
from app.services.outbound import sign

OUT = ROOT / "sdk" / "fixtures" / "webhooks.json"

SECRET = "whsec_demo_0123456789abcdef"
NOW = 1_800_000_000  # the receiver's clock in every case


def _payload(action: str, *, milestone_id: str | None = "ms_1", from_status=None, to_status=None, detail=None) -> dict:
    event_type = event_type_for(action, milestone_id, to_status)
    return {
        "id": "ev_1",
        "type": event_type,
        "created_at": "2027-06-01T12:00:00+00:00",
        "agreement_id": "ag_1",
        "milestone_id": milestone_id,
        "data": {
            "action": action,
            "actor": "system:gate",
            "from_status": from_status,
            "to_status": to_status,
            "detail": detail or {},
        },
    }


def _body(payload: dict) -> str:
    # exactly how the engine serialises a delivery (app/services/outbound.py)
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _delivery(payload: dict, ts: int, secret: str = SECRET, delivery: str = "dl_1") -> tuple[str, dict]:
    body = _body(payload)
    headers = {
        "Content-Type": "application/json",
        "X-MDE-Event": payload["type"],
        "X-MDE-Delivery": delivery,
        "X-MDE-Timestamp": str(ts),
        "X-MDE-Signature": sign(secret, str(ts), body.encode()),
    }
    return body, headers


def cases() -> list[dict]:
    settled = _payload("captured", from_status="settling", to_status="settled", detail={"capture_id": "CAP-1"})
    verified = _payload("auto_verified", from_status="verifying", to_status="verified", detail={"confidence": 0.9})
    agreement = _payload("confirmed", milestone_id=None, detail={"by": "payer"})
    out = []

    body, headers = _delivery(settled, NOW - 10)
    out.append(
        {
            "name": "settled: fresh delivery verifies",
            "body": body,
            "headers": headers,
            "expect": "ok",
            "event": settled,
            "delivery_id": "dl_1",
        }
    )
    body, headers = _delivery(verified, NOW - 299)
    out.append(
        {
            "name": "verified: just inside the 300 s tolerance",
            "body": body,
            "headers": headers,
            "expect": "ok",
            "event": verified,
            "delivery_id": "dl_1",
        }
    )
    body, headers = _delivery(agreement, NOW + 30)
    out.append(
        {
            "name": "agreement event, receiver clock 30 s behind",
            "body": body,
            "headers": headers,
            "expect": "ok",
            "event": agreement,
            "delivery_id": "dl_1",
        }
    )
    body, headers = _delivery(settled, NOW - 10)
    lower = {k.lower(): v for k, v in headers.items()}
    out.append(
        {
            "name": "lower-case header names (Node, WSGI)",
            "body": body,
            "headers": lower,
            "expect": "ok",
            "event": settled,
            "delivery_id": "dl_1",
        }
    )

    body, headers = _delivery(settled, NOW - 301)
    out.append(
        {"name": "replay: signed 301 s ago", "body": body, "headers": headers, "expect": "timestamp_out_of_tolerance"}
    )
    body, headers = _delivery(settled, NOW + 301)
    out.append(
        {
            "name": "timestamp 301 s in the future",
            "body": body,
            "headers": headers,
            "expect": "timestamp_out_of_tolerance",
        }
    )
    body, headers = _delivery(settled, NOW - 10, secret="whsec_someone_else")
    out.append(
        {
            "name": "signed with another subscription's secret",
            "body": body,
            "headers": headers,
            "expect": "signature_mismatch",
        }
    )
    body, headers = _delivery(settled, NOW - 10)
    out.append(
        {
            "name": "body changed after signing",
            "body": body.replace('"settled"', '"refunded"'),
            "headers": headers,
            "expect": "signature_mismatch",
        }
    )
    body, headers = _delivery(settled, NOW - 10)
    out.append(
        {
            "name": "body re-serialised with spaces",
            "body": json.dumps(json.loads(body), indent=1),
            "headers": headers,
            "expect": "signature_mismatch",
        }
    )
    body, headers = _delivery(settled, NOW - 10)
    out.append(
        {
            "name": "no signature header",
            "body": body,
            "headers": {k: v for k, v in headers.items() if k != "X-MDE-Signature"},
            "expect": "missing_signature",
        }
    )
    body, headers = _delivery(settled, NOW - 10)
    out.append(
        {
            "name": "no timestamp header",
            "body": body,
            "headers": {k: v for k, v in headers.items() if k != "X-MDE-Timestamp"},
            "expect": "missing_timestamp",
        }
    )
    body, headers = _delivery(settled, NOW - 10)
    out.append(
        {
            "name": "timestamp is not a number",
            "body": body,
            "headers": {**headers, "X-MDE-Timestamp": "2027-06-01"},
            "expect": "bad_timestamp",
        }
    )
    body, headers = _delivery(settled, NOW - 10)
    out.append(
        {
            "name": "event header disagrees with the body",
            "body": body,
            "headers": {**headers, "X-MDE-Event": "milestone.refunded"},
            "expect": "event_type_mismatch",
        }
    )
    raw = "not json"
    ts = str(NOW - 10)
    out.append(
        {
            "name": "signed, but not JSON",
            "body": raw,
            "headers": {"X-MDE-Timestamp": ts, "X-MDE-Signature": sign(SECRET, ts, raw.encode())},
            "expect": "bad_json",
        }
    )
    return out


def render() -> str:
    doc = {
        "about": "Deliveries signed by the engine (app/services/outbound.py). Verify each with secret and now; "
        "expect 'ok' (then compare event and delivery_id) or the error code.",
        "secret": SECRET,
        "now": NOW,
        "tolerance_seconds": 300,
        "cases": cases(),
    }
    return json.dumps(doc, indent=2, sort_keys=False) + "\n"


if __name__ == "__main__":
    text = render()
    if OUT.exists() and OUT.read_text() == text:
        print(f"{OUT.relative_to(ROOT)} is up to date")
        sys.exit(0)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(ROOT)} ({len(cases())} cases)")
