# SDK

- `openapi-core.json`: the core operations (tagged `x-sdk-core` in the engine), for generating clients. Semver of its own
  (`CORE_VERSION` in `app/sdk_spec.py`).
- `python/`: `milestone-ledger`, webhook verification and typed events (`pip install ./sdk/python` until it's on PyPI).
- `typescript/`: `milestone-ledger`, the same for Node 20+, Deno, Bun, Workers and browsers (`npm install ./sdk/typescript`).
- `fixtures/webhooks.json`: deliveries signed by the engine's own signer, which both SDKs' tests verify. Written by
  `scripts/sdk_fixtures.py`; `tests/test_sdk_webhooks.py` fails when it's stale.
