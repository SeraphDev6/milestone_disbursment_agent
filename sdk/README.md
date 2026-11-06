# SDK

- `openapi-core.json`: the core operations (tagged `x-sdk-core` in the engine), for generating clients. Semver of its own
  (`CORE_VERSION` in `app/sdk_spec.py`).
- `python/`: `milestone-ledger`, webhook verification and typed events (`pip install ./sdk/python` until it's on PyPI).
- `typescript/`: `milestone-ledger`, the same for Node 20+, Deno, Bun, Workers and browsers (`npm install ./sdk/typescript`).
- `fixtures/webhooks.json`: deliveries signed by the engine's own signer, which both SDKs' tests verify. Written by
  `scripts/sdk_fixtures.py`; `tests/test_sdk_webhooks.py` fails when it's stale.

## Quickstart and example app

`python/examples/quickstart.py` and `typescript/examples/quickstart.ts` run the core flow in about sixty lines against a
sandbox deployment's demo: the funder drafts a grant, the grantee joins and signs, uploads the demo's real sign-in
sheets and budget, the engine verifies them, the funder approves, and the tranche is ready to pay once the PayPal hold
is approved (the one browser step; the script prints the URL).

```sh
MDE_BASE_URL=https://app.milestone-ledger.com/api python sdk/python/examples/quickstart.py
MDE_BASE_URL=https://app.milestone-ledger.com/api npx tsx sdk/typescript/examples/quickstart.ts
# or locally: uv run python scripts/dev_server.py --port 8765, then MDE_BASE_URL=http://localhost:8765/api
```

Both quickstarts double as contract tests: `tests/test_sdk_examples.py` runs the Python one against the hermetic app,
and the TypeScript package's `test/quickstart.test.ts` runs the TypeScript one against a running engine when
`MDE_BASE_URL` is set (CI starts `scripts/dev_server.py` and points it there), so an API change that breaks the core
flow fails the build in both languages.

`python/examples/webhook_receiver.py` and `typescript/examples/webhook-receiver.ts` are the smallest real integration:
a receiver that verifies each delivery with the SDK and marks settled tranches paid in its own `paid.json`, ignoring
retries it has already handled. `tests/test_sdk_examples.py` runs the Python quickstart against the hermetic app and
the receiver against engine-signed deliveries; the TypeScript examples are typechecked with the package.
