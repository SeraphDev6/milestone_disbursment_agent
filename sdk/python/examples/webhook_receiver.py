"""A webhook receiver that marks tranches paid in its own records: the smallest real integration.

    pip install ./sdk/python
    MDE_WEBHOOK_SECRET=whsec_... python webhook_receiver.py          # listens on :8787, writes paid.json

Subscribe the engine to it (as a party on the agreement):
    POST /agreements/{id}/subscriptions  {"url": "https://your.host/hooks/mde", "events": ["milestone.status_changed"]}
The response shows the secret once. Subscriber URLs must be https on a real deployment (http is allowed by the
local dev server), so put this behind a tunnel or a reverse proxy.
"""

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from milestone_ledger import WebhookVerificationError, verify_webhook

RECORDS = Path(os.environ.get("MDE_RECORDS", "paid.json"))


def handle(raw: bytes, headers, secret: str, records: Path = RECORDS) -> tuple[int, str]:
    """Verify one delivery and apply it. Returns (HTTP status, message). Safe to call again with a retry."""
    try:
        event = verify_webhook(raw, headers, secret)
    except WebhookVerificationError as e:
        return 400, f"{e.code}: {e}"
    state = json.loads(records.read_text()) if records.exists() else {"paid": [], "seen": []}
    if event.delivery_id in state["seen"]:
        return 200, "already handled"
    if event.is_status_change and event.data.to_status == "settled" and event.milestone_id:
        state["paid"].append(
            {"milestone_id": event.milestone_id, "agreement_id": event.agreement_id, "at": event.created_at}
        )
        state["seen"].append(event.delivery_id)
        records.write_text(json.dumps(state, indent=2))
        return 200, f"marked {event.milestone_id} paid"
    state["seen"].append(event.delivery_id)
    records.write_text(json.dumps(state, indent=2))
    return 200, f"noted {event.type} ({event.data.action})"


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        status, message = handle(raw, self.headers, os.environ["MDE_WEBHOOK_SECRET"])
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(message.encode())
        print(f"{status} {message}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8787"))
    print(f"listening on :{port}; settled tranches go to {RECORDS}")
    HTTPServer(("", port), Handler).serve_forever()
