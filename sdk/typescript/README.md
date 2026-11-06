# milestone-ledger (TypeScript)

Helpers for integrating with the Milestone Ledger engine. No dependencies; Web Crypto, so it runs on Node 20+,
Deno, Bun, Cloudflare Workers and in browsers.

## Verify a webhook

Every outbound webhook is signed: `X-MDE-Signature` is `sha256=` + HMAC-SHA256 over `"<X-MDE-Timestamp>.<raw body>"`
with the subscription's secret (shown once when the subscription is created). Verify before trusting the body, and
verify the raw body: parsing and re-serialising it changes the signature.

```ts
import { verifyWebhook, WebhookVerificationError } from "milestone-ledger";

// Express: app.post("/hooks/milestone-ledger", express.raw({ type: "application/json" }), handler)
async function handler(req, res) {
  try {
    const event = await verifyWebhook(req.body, req.headers, process.env.MDE_WEBHOOK_SECRET!);
    if (event.type === "milestone.status_changed" && event.data.to_status === "settled") await markPaid(event.milestone_id!);
    res.status(204).end();
  } catch (e) {
    if (e instanceof WebhookVerificationError) return res.status(400).send(`${e.code}: ${e.message}`);
    throw e;
  }
}
```

`verifyWebhook(rawBody, headers, secret, { toleranceSeconds = 300, now })` checks the headers are present, the
timestamp is within `toleranceSeconds` of now (replays), and the signature matches (constant-time), then parses the
body into a `WebhookEvent`. It throws `WebhookVerificationError` with a stable `code`: `missing_signature`,
`missing_timestamp`, `bad_timestamp`, `timestamp_out_of_tolerance`, `signature_mismatch`, `bad_json`,
`event_type_mismatch`.

## Events

| Field | Meaning |
| --- | --- |
| `id` | Event id (also the audit log entry's id) |
| `type` | `milestone.status_changed`, `milestone.<action>` or `agreement.<action>` |
| `created_at` | ISO 8601 |
| `agreement_id`, `milestone_id` | `milestone_id` is `null` for agreement-level events |
| `data.action`, `data.actor` | e.g. `captured` by `system:gate`, `webhook:paypal`, `user:<id>@org:<id>` |
| `data.from_status`, `data.to_status` | A `MilestoneStatus` for `milestone.status_changed` |
| `data.detail` | Action-specific |
| `delivery_id` | `X-MDE-Delivery`, unique per attempt; store it to drop retries you already handled |

`MILESTONE_STATUSES` and `AGREEMENT_STATUSES` list every status the engine emits.
