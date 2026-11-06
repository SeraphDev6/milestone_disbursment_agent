# milestone-ledger (Python)

Helpers for integrating with the Milestone Ledger engine. No dependencies.

## Verify a webhook

Every outbound webhook is signed: `X-MDE-Signature` is `sha256=` + HMAC-SHA256 over `"<X-MDE-Timestamp>.<raw body>"` with
the subscription's secret (shown once when the subscription is created). Verify before trusting anything in the body.

```python
from milestone_ledger import WebhookVerificationError, verify_webhook


@app.post("/hooks/milestone-ledger")
async def hook(request):
    try:
        event = verify_webhook(await request.body(), request.headers, SECRET)
    except WebhookVerificationError as e:
        return Response(status_code=400, content=f"{e.code}: {e}")
    if event.type == "milestone.status_changed" and event.data.to_status == "settled":
        mark_paid(event.milestone_id)
    return Response(status_code=204)
```

`verify_webhook(raw_body, headers, secret, tolerance_seconds=300)` checks the headers are present, the timestamp is
within `tolerance_seconds` of now (replays), and the signature matches (constant-time), then parses the body into a
`WebhookEvent`. It raises `WebhookVerificationError` with a stable `code` (`missing_signature`, `missing_timestamp`,
`bad_timestamp`, `timestamp_out_of_tolerance`, `signature_mismatch`, `bad_json`, `event_type_mismatch`).

## Events

```python
event.id  # event id (also the audit log entry's id)
event.type  # "milestone.status_changed", "milestone.<action>" or "agreement.<action>"
event.created_at  # ISO 8601
event.agreement_id
event.milestone_id  # None for agreement-level events
event.data.action  # e.g. "evidence_received", "auto_verified", "captured", "payout_succeeded"
event.data.actor  # "user:<id>@org:<id>", "system:gate", "webhook:paypal", ...
event.data.from_status, event.data.to_status  # a MilestoneStatus for milestone.status_changed
event.data.detail  # action-specific dict
event.delivery_id  # X-MDE-Delivery, unique per attempt; store it to drop retries you already handled
```

`MILESTONE_STATUSES` and `AGREEMENT_STATUSES` list every status the engine emits.

## License

Apache-2.0 (see `LICENSE`). The engine itself is AGPL-3.0; the SDK packages, examples and fixtures under `sdk/` are
Apache-2.0 so they can be used in closed products that integrate with a Milestone Ledger deployment.
