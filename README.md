# Milestone Disbursement Engine

Turns a grant or statement of work into tranches with checkable acceptance criteria, verifies evidence against them, and settles each tranche through PayPal. Built for the PayPal AI Hackathon (Nov 2026). Spec: [`docs/SPEC.md`](docs/SPEC.md).

**Status:** agreements, structured criteria and lint, two-party confirmation, PayPal escrow (per-tranche hold, reauthorize, capture, void, expiry, webhooks), agreement extraction, evidence intake, deterministic checks, the AI verifier, the release gate, pre-check and human review all work in mock mode, as do the GitHub and upload evidence adapters. Extraction and verification have been run live against Claude; PayPal sandbox is wired but not yet run against PayPal. Up next: invoice mode and outbound webhooks.

AI parts (extraction, verification) use [Pydantic AI](https://ai.pydantic.dev) with Claude (`claude-opus-5-5`, server-side refusal fallbacks on). Mock mode swaps in a deterministic `FunctionModel`, so both paths run the same agents.

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

1. `POST /agreements/extract` with a PDF or text agreement proposes tranches and criteria (with source quotes, open questions and lint), or `POST /criteria/lint` checks a draft you wrote.
2. `POST /agreements` creates a draft and returns one API key per party (shown once).
3. Payer and payee each `POST /agreements/{id}/confirm` with the `criteria_hash` they reviewed (`X-API-Key` header). Confirmation is refused while the lint has errors. When both have signed, the agreement is `active` and its criteria are locked.
4. Payer `POST /milestones/{id}/fund` returns an `approve_url`; open it to approve the hold (mock mode simulates PayPal's page).
5. Payee `POST /milestones/{id}/precheck` tries draft evidence without submitting it, then `POST /milestones/{id}/evidence` submits it (spreadsheets as CSV text). Checks run first, then the AI verifier; `GET /milestones/{id}/verdict` shows per-criterion results with citations.
6. If the payer turned on auto-release and the verdict passes the threshold, a funded tranche settles on its own. Otherwise it waits in `needs_review` for `POST /milestones/{id}/review` (payer or approver): approve releases it, reject returns what's missing.
   Or upload files: `POST /milestones/{id}/uploads` takes PDF, CSV, XLSX, TXT, MD and images (multipart `files`, optional `note` and `dated`). Spreadsheets become CSV for the checks; PDFs are text-extracted and the original goes to the verifier too.
   Or connect GitHub: payee `POST /agreements/{id}/integrations/github` with `{"repo": "owner/name"}` returns a webhook URL, secret and one label per milestone. Add the webhook (pull_request events) to the repo; a merged PR carrying a milestone's label becomes evidence pinned to the merge commit, with CI results from GitHub (the only way to satisfy a `ci_green` check).
7. In invoice mode (`settlement_mode: invoice_on_verify`) nothing is funded up front: a verified tranche sends the payer a PayPal invoice and settles when it's paid (mock mode has a pay page at the invoice link).
8. After a rejection the payee can `POST /milestones/{id}/dispute`; the payer side then reviews it again.
9. Integrations: `POST /agreements/{id}/subscriptions` registers a URL for signed events (`X-MDE-Signature: sha256=HMAC(secret, "{X-MDE-Timestamp}.{body}")`); `GET /agreements/{id}/audit` and `GET /milestones/{id}/audit` return the event log; `GET /agreements/{id}/compliance-packet?format=pdf` (payer/approver) exports everything behind each payment.
10. `POST /milestones/{id}/release` releases a verified tranche manually (e.g. after funding late). `GET /agreements/{id}` shows held, released and unfunded.

A complete example draft is in [`tests/conftest.py`](tests/conftest.py).

Run `uv run python -m app.jobs daily` once a day: it clears expired PayPal holds (29 days) and cancels invoices left unpaid for 30 days.

## Develop

```sh
uv run pytest
uv run ruff check . && uv run ruff format .
```

## Deploy

`render.yaml` is a Render Blueprint: a web service plus Postgres. Set `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET` in the Render dashboard when switching to `MODE=sandbox`.

## License

[AGPL-3.0-only](LICENSE).
