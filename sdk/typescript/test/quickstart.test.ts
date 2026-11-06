// Contract test: the TypeScript quickstart runs the core flow against a real server (the engine with its stand-ins:
// `uv run python scripts/dev_server.py --port 8765`, then MDE_BASE_URL=http://localhost:8765/api npm test).
// Skipped when MDE_BASE_URL is unset, so `npm test` alone stays offline.
import assert from "node:assert/strict";
import { test } from "node:test";
import { run } from "../examples/quickstart.js";

const base = process.env.MDE_BASE_URL;

test("the quickstart runs the core flow to a verified tranche", { skip: !base && "set MDE_BASE_URL to a sandbox server with the demo on" }, async () => {
  const health = (await (await fetch(`${base}/health`)).json()) as { demo?: boolean };
  assert.equal(health.demo, true, `${base} has no demo`);
  const lines: string[] = [];
  const result = await run(base!, (l) => lines.push(l));
  assert.equal(result.status, "verified");
  assert.ok(result.approve_url);
  assert.deepEqual(lines.map((l) => l.slice(0, 2)), ["1.", "2.", "3.", "4.", "5."]);
  assert.match(lines[0], /lint ok: true/);
  assert.match(lines[1], /agreement is active/);
  assert.match(lines[3], /met=true/);
  assert.match(lines[3], /214 rows/);
  assert.match(lines[3], /9800\.50/);
  assert.match(lines[4], /tranche is verified/);
});
