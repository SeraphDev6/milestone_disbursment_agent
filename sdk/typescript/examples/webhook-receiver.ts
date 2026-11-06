/**
 * A webhook receiver that marks tranches paid in its own records: the smallest real integration.
 *
 *   npm install ./sdk/typescript
 *   MDE_WEBHOOK_SECRET=whsec_... npx tsx webhook-receiver.ts        # listens on :8787, writes paid.json
 *
 * Subscribe the engine to it (as a party on the agreement):
 *   POST /agreements/{id}/subscriptions  {"url": "https://your.host/hooks/mde", "events": ["milestone.status_changed"]}
 * The response shows the secret once. Subscriber URLs must be https on a real deployment (http is allowed by the
 * local dev server), so put this behind a tunnel or a reverse proxy.
 */
import { readFileSync, existsSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { verifyWebhook, WebhookVerificationError } from "../src/index.js"; // from the package: "milestone-ledger"

const RECORDS = process.env.MDE_RECORDS ?? "paid.json";

type Records = { paid: { milestone_id: string; agreement_id: string; at: string }[]; seen: string[] };

/** Verify one delivery and apply it. Returns [HTTP status, message]. Safe to call again with a retry. */
export async function handle(raw: Uint8Array, headers: Record<string, string | string[] | undefined>, secret: string, records = RECORDS): Promise<[number, string]> {
  let event;
  try {
    event = await verifyWebhook(raw, headers, secret);
  } catch (e) {
    if (e instanceof WebhookVerificationError) return [400, `${e.code}: ${e.message}`];
    throw e;
  }
  const state: Records = existsSync(records) ? JSON.parse(readFileSync(records, "utf8")) : { paid: [], seen: [] };
  if (event.delivery_id && state.seen.includes(event.delivery_id)) return [200, "already handled"];
  if (event.delivery_id) state.seen.push(event.delivery_id);
  let message = `noted ${event.type} (${event.data.action})`;
  if (event.is_status_change && event.data.to_status === "settled" && event.milestone_id) {
    state.paid.push({ milestone_id: event.milestone_id, agreement_id: event.agreement_id, at: event.created_at });
    message = `marked ${event.milestone_id} paid`;
  }
  writeFileSync(records, JSON.stringify(state, null, 2));
  return [200, message];
}

if (process.argv[1] && import.meta.url.endsWith(process.argv[1].split("/").pop()!)) {
  const port = Number(process.env.PORT ?? 8787);
  createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", async () => {
      const [status, message] = await handle(new Uint8Array(Buffer.concat(chunks)), req.headers, process.env.MDE_WEBHOOK_SECRET!);
      res.writeHead(status, { "Content-Type": "text/plain" }).end(message);
      console.log(status, message);
    });
  }).listen(port, () => console.log(`listening on :${port}; settled tranches go to ${RECORDS}`));
}
