# Milestone Disbursement Engine

Turns a grant or statement of work into tranches with checkable acceptance criteria, verifies evidence against them, and settles each tranche through PayPal. Built for the PayPal AI Hackathon (Nov 2026). Spec: [`docs/SPEC.md`](docs/SPEC.md).

**Status:** agreements, structured criteria and lint, two-party confirmation, PayPal escrow (per-tranche hold, reauthorize, capture, void, expiry, webhooks), agreement extraction, evidence intake, deterministic checks, the AI verifier, the release gate, pre-check and human review all work in mock mode, as do the GitHub and upload evidence adapters. Extraction and verification have been run live against Claude; PayPal sandbox is wired but not yet run against PayPal. Up next: invoice mode and outbound webhooks.

AI parts (extraction, verification) use [Pydantic AI](https://ai.pydantic.dev) with Claude (`claude-opus-5-5`, server-side refusal fallbacks on). Mock mode swaps in a deterministic `FunctionModel`, so both paths run the same agents.

## Run it

The whole app (Postgres, the API and the web app) with Docker. Mock mode needs no keys:

```sh
docker compose up --build
# open http://localhost:8000/app/  (try /app/demo; API docs at /docs)
```

Compose reads `.env` if there is one: set `MODE=sandbox` plus the PayPal keys and `SECRET_KEY` there for the PayPal sandbox and Claude. Use `APP_PORT=8080 docker compose up` if port 8000 is taken. Data lives in the `pgdata` volume (`docker compose down -v` resets it).

To develop on the host instead, you need [uv](https://docs.astral.sh/uv/):

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

## Web app

A React app for every side of an agreement (funder, grantee, reviewer) lives in [`web/`](web/README.md). Its design system, *Ledger & Stamp*, is WCAG 2.2 AA with its checks automated: [`web/DESIGN.md`](web/DESIGN.md).

```sh
cd web && npm install && MDE_API=http://localhost:8000 npm run dev   # http://localhost:5173/app/
```

Run the API with `PUBLIC_BASE_URL=http://localhost:5173` so mock PayPal redirects return through the dev server. After `npm run build`, the API serves the app itself at `/app`.

## Try the flow

Everything is done as a signed-in person acting for an organization (see "Organizations, people and access" in the spec).

1. **Sign in:** `POST /auth/magic-link {"email": ...}` then `POST /auth/magic-link/verify {"token": ...}` returns a session token; send `Authorization: Bearer <token>`. In mock mode the link comes back in the response (`dev_link`) instead of being emailed. Orgs can add OIDC single sign-on for domains they own (`PUT /orgs/{id}/sso`, verify each domain via DNS TXT, then people sign in with `POST /auth/sso/start`).
2. **Payer org:** `POST /orgs {"name", "paypal_email"}`. Invite colleagues with `POST /orgs/{id}/invitations {"email", "role"}` (owner, admin, member, viewer); integrations and agents use `POST /orgs/{id}/api-keys`.
3. **Draft:** `POST /agreements/extract` (PDF or text) proposes tranches and criteria, or `POST /criteria/lint` checks ones you wrote. Then `POST /orgs/{id}/agreements` with `counterparty_email` (and optional `reviewer_emails`) creates it and sends invitations.
4. **Payee and reviewers** sign in with the invited email and `POST /invitations/accept` for one of their orgs (or a new one). Pending invitations show in `GET /me`.
5. **Confirm:** each party's admin `POST /agreements/{id}/confirm` with the `criteria_hash` they reviewed. The agreement goes live when the payee has joined, every party (reviewers included) has signed, and no invitation is pending.
6. **Fund** (escrow): payer `POST /milestones/{id}/fund` returns an `approve_url` for the PayPal hold (mock mode simulates PayPal's page). Invoice mode skips this.
7. **Evidence:** payee members pre-check (`/precheck`), then submit (`/evidence`, `/uploads`, or merged GitHub PRs). Checks run first, then the AI verifier; `GET /milestones/{id}/verdict` shows results with citations.
8. **Decide:** with auto-release on and a confident verdict, a funded tranche settles itself. Otherwise it waits in each side's `GET /orgs/{id}/review-queue` for a payer admin or reviewer to `POST /milestones/{id}/review` (with `require_reviewer_approval`, only a reviewer can approve). Rejections return what's missing; the payee can `/dispute`.
9. **Agents (MCP):** point an MCP client (Claude Desktop, Claude Code, etc.) at `<base>/mcp`. It discovers the OAuth endpoints, opens a consent page where you sign in and pick the org it acts for, then gets org-bound tools (list agreements, status, evidence, verdicts, pre-check, submit evidence, review queue, portfolio, compliance packet, drafting). Headless agents use an org API key as the bearer token. Confirming, approving, releasing, funding and disputes stay with people.
10. **Track:** `GET /orgs/{id}/agreements?side=payer|payee|reviewer`, `GET /orgs/{id}/portfolio`, audit logs, compliance packets (JSON/PDF), and signed outbound webhooks via `POST /agreements/{id}/subscriptions`.

A complete example draft is in [`tests/conftest.py`](tests/conftest.py), and [`tests/helpers.py`](tests/helpers.py) walks the whole sign-in, org and invitation flow.

Run `uv run python -m app.jobs daily` once a day: it clears expired PayPal holds (29 days) and cancels invoices left unpaid for 30 days.

## Code layout

```
app/
  main.py          app factory: wires settings, PayPal, AI, GitHub, routers
  config.py        settings (env / .env)
  jobs.py          scheduled jobs: daily expiry, webhook delivery
  models/          single source of truth: one SQLModel definition per entity (XBase -> table X, XCreate, XRead),
                   plus enums and the value objects stored as typed JSON (Criterion, Measure, CriterionResult)
  api/             HTTP endpoints by resource, plus shared deps (auth, roles) and response assembly
  db/              engine, sessions, audit log + webhook outbox
  domain/          criteria lint and deterministic checks
  ai/              Pydantic AI agents (extraction, verification) and extraction post-processing
  services/        settlement (escrow + invoice), verification and gate, outbound webhooks, compliance packet
  integrations/    PayPal gateway (mock + REST), GitHub, file uploads
web/               React app (Vite, React Aria, AG Grid); served at /app from web/dist
```

## Develop

```sh
uv run pytest
uv run ruff check . && uv run ruff format .
```

### Database migrations

The schema comes from `app/models/` (SQLModel); Alembic migrations live in `migrations/`. The app applies pending migrations at startup (`AUTO_MIGRATE=true`, the default). With more than one app instance, turn that off and run `uv run python -m app.jobs migrate` before starting.

After changing a model:

```sh
docker compose up -d db
export DATABASE_URL=postgresql://milestone:milestone@localhost:5433/milestone
uv run alembic upgrade head                      # bring the dev database up to date
uv run alembic revision --autogenerate -m "..."  # generate the migration, then read it
uv run alembic upgrade head && uv run alembic check
```

`tests/test_migrations.py` fails if a model changes without a migration.

## Deploy

`render.yaml` is a Render Blueprint: a web service plus Postgres. Set `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET` in the Render dashboard when switching to `MODE=sandbox`.

## License

[AGPL-3.0-only](LICENSE).
