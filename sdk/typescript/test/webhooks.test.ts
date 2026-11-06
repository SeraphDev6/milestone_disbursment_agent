// The SDK against deliveries signed by the engine's own signer (sdk/fixtures/webhooks.json).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { WebhookVerificationError, verifyWebhook } from "../src/index.js";

type Case = { name: string; body: string; headers: Record<string, string>; expect: string; event?: Record<string, unknown>; delivery_id?: string };
type Doc = { secret: string; now: number; tolerance_seconds: number; cases: Case[] };

const doc: Doc = JSON.parse(readFileSync(fileURLToPath(new URL("../../../fixtures/webhooks.json", import.meta.url)), "utf8"));

for (const c of doc.cases) {
  test(c.name, async () => {
    const options = { toleranceSeconds: doc.tolerance_seconds, now: doc.now };
    if (c.expect === "ok") {
      const event = await verifyWebhook(c.body, c.headers, doc.secret, options);
      const expected = c.event as { type: string; agreement_id: string; milestone_id: string | null; data: { action: string; to_status: string | null } };
      assert.equal(event.type, expected.type);
      assert.equal(event.agreement_id, expected.agreement_id);
      assert.equal(event.milestone_id, expected.milestone_id);
      assert.equal(event.data.action, expected.data.action);
      assert.equal(event.data.to_status, expected.data.to_status);
      assert.equal(event.delivery_id, c.delivery_id);
      assert.equal(event.is_status_change, expected.data.to_status != null);
    } else {
      await assert.rejects(verifyWebhook(c.body, c.headers, doc.secret, options), (e: unknown) => {
        assert.ok(e instanceof WebhookVerificationError, String(e));
        assert.equal(e.code, c.expect);
        return true;
      });
    }
  });
}

test("a Headers object and a byte body work, and the default clock treats an old fixture as a replay", async () => {
  const ok = doc.cases.find((c) => c.expect === "ok")!;
  const headers = new Headers(ok.headers);
  const event = await verifyWebhook(new TextEncoder().encode(ok.body), headers, doc.secret, { now: doc.now });
  assert.equal(event.id, (ok.event as { id: string }).id);
  await assert.rejects(verifyWebhook(ok.body, headers, doc.secret), (e: WebhookVerificationError) => e.code === "timestamp_out_of_tolerance");
});
