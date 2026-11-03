# Milestone Disbursement Engine

Turns a grant or statement of work into tranches with checkable acceptance criteria, verifies evidence against them, and settles each tranche through PayPal. Built for the PayPal AI Hackathon (Nov 2026). Spec: [`docs/SPEC.md`](docs/SPEC.md).

**Status:** agreements, structured criteria, criteria lint, two-party confirmation and the PayPal escrow path (per-tranche hold, reauthorize, capture, void, expiry, webhooks) work in mock mode. Sandbox is wired but not yet run against PayPal. Up next: extraction and the verifier.

## Run it

Needs [uv](https://docs.astral.sh/uv/). Mock mode needs no keys.

```sh
uv sync
uv run uvicorn app.main:app --reload
# open http://localhost:8000/docs
```

By default it uses SQLite at `./local.db`. For Postgres:

```sh
docker compose up -d db
DATABASE_URL=postgresql://milestone:milestone@localhost:5433/milestone uv run uvicorn app.main:app --reload
```

Copy `.env.example` to `.env` to set `MODE=sandbox` and PayPal sandbox credentials.

## Try the flow

1. `POST /criteria/lint` with a draft to see what a reviewer couldn't verify.
2. `POST /agreements` creates a draft and returns one API key per party (shown once).
3. Payer and payee each `POST /agreements/{id}/confirm` with the `criteria_hash` they reviewed (`X-API-Key` header). Confirmation is refused while the lint has errors. When both have signed, the agreement is `active` and its criteria are locked.
4. Payer `POST /milestones/{id}/fund` returns an `approve_url`; open it to approve the hold (mock mode simulates PayPal's page).
5. Until the verifier lands, mock mode has `POST /mock/milestones/{id}/mark-verified`.
6. Payer or approver `POST /milestones/{id}/release` captures the hold to the payee. `GET /agreements/{id}` shows held, released and unfunded.

A complete example draft is in [`tests/conftest.py`](tests/conftest.py).

Expired holds (PayPal keeps them 29 days) are cleared by `uv run python -m app.jobs expire-holds`; run it daily.

## Develop

```sh
uv run pytest
uv run ruff check . && uv run ruff format .
```

## Deploy

`render.yaml` is a Render Blueprint: a web service plus Postgres. Set `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET` in the Render dashboard when switching to `MODE=sandbox`.

## License

[AGPL-3.0-only](LICENSE).
