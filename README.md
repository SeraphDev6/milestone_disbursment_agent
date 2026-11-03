# Milestone Disbursement Engine

Turns a grant or statement of work into tranches with checkable acceptance criteria, verifies evidence against them, and settles each tranche through PayPal. Built for the PayPal AI Hackathon (Nov 2026). Spec: [`docs/SPEC.md`](docs/SPEC.md).

**Status:** foundation. Agreements, structured criteria, criteria lint and two-party confirmation work. Up next: PayPal escrow (authorize, capture, void), then the verifier and evidence adapters.

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

A complete example draft is in [`tests/conftest.py`](tests/conftest.py).

## Develop

```sh
uv run pytest
uv run ruff check . && uv run ruff format .
```

## Deploy

`render.yaml` is a Render Blueprint: a web service plus Postgres. Set `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET` in the Render dashboard when switching to `MODE=sandbox`.

## License

[AGPL-3.0-only](LICENSE).
