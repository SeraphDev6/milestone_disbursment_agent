# Milestone Disbursement Engine

Turns a grant or statement of work into tranches with checkable acceptance criteria, verifies evidence against them, and settles each tranche through PayPal. Built for the PayPal AI Hackathon (Nov 2026). Live at **[app.milestone-ledger.com](https://app.milestone-ledger.com)**. Spec: [`docs/SPEC.md`](docs/SPEC.md). Business plan: [`docs/BUSINESS.md`](docs/BUSINESS.md).

**PayPal:** sandbox and live calls go through the APIMatic-generated [PayPal Server SDK](https://pypi.org/project/paypal-server-sdk/) for everything it covers (Orders v2 and Payments v2: create, authorize and capture orders; reauthorize, capture, void, refund) and through plain REST for what it doesn't (Payouts, referenced payouts, Partner Referrals, Invoicing, webhook verification). One httpx client serves both, so tests mock PayPal with a single transport.

**Status (Nov 4):** built and tested against a stand-in PayPal in the test suite: agreements, structured criteria and lint, two-party confirmation, PayPal escrow (per-tranche hold, reauthorize, capture, void, expiry, webhooks), direct payment to connected payees with a dispute window and a Payouts fallback, invoice mode, agreement extraction, evidence intake (upload and GitHub), deterministic checks, the AI verifier, the release gate, pre-check, human review, outbound webhooks, audit log and compliance packets, organizations with SSO, the MCP server and the web app. Extraction and verification have run live against Claude. Each PayPal call has been checked in sandbox on its own; the full wallet flow through the app and live webhooks have not yet run. What's left is tracked in [issues](https://github.com/SeraphDev6/milestone_disbursment_agent/issues), starting with the P0s: the sandbox wallet flow ([#33](https://github.com/SeraphDev6/milestone_disbursment_agent/issues/33)) and live webhooks ([#34](https://github.com/SeraphDev6/milestone_disbursment_agent/issues/34)).

AI parts (extraction, verification) use [Pydantic AI](https://ai.pydantic.dev) with Claude (`claude-opus-5-5`, server-side refusal fallbacks on). The test suite (and `LLM=mock`) swaps in a deterministic `FunctionModel`, so both paths run the same agents.

## Run it

The whole app (Postgres, the API and the web app) with Docker. It needs a PayPal sandbox REST app's credentials and a `SECRET_KEY` in `.env` (copy `.env.example`; `ANTHROPIC_API_KEY` for the model, or `LLM=mock` for the stand-in):

```sh
docker compose up --build
# open http://localhost:8000/  (the demo at /demo; API docs at /api/docs; MCP server at /mcp)
```

Use `APP_PORT=8080 docker compose up` if port 8000 is taken. Data lives in the `pgdata` volume (`docker compose down -v` resets it).

## Modes and the demo

The product is the same everywhere; `MODE` only says which PayPal it is connected to.

- **`sandbox`** (the default): PayPal's sandbox API. Payees connect sandbox business accounts, payers approve holds and pay links with sandbox accounts, and no real money moves. Every PayPal call goes to the sandbox host, so a live account can't be connected or charged here. This is what [app.milestone-ledger.com](https://app.milestone-ledger.com) runs.
- **`live`**: PayPal's production API, direct route only (payer pays the connected payee; the platform never holds funds). It needs PayPal partner approval and a BN code, an https `PUBLIC_BASE_URL` and `PAYPAL_WEBHOOK_ID`. A live deployment never accepts a sandbox account.

**The demo** lives at **`/demo`** on sandbox deployments (off with `DEMO=false`, never on a live one). It loads the Riverside literacy grant with a funder, a grantee and a reviewer already signed in so a visitor can play every side, and has two shortcuts the product doesn't (mark a tranche verified, connect the grantee's PayPal). The grant is real work, not a fixture: by default the demo reads the grant agreement PDF in `demo/` through the extraction endpoint, so the tranches, criteria, lint findings and open questions on screen are what the model made of the document (the "ready-made" option types the same grant in instantly). The grantee's evidence is real files too, served by `GET /api/demo/files`: the signed sign-in sheets and their register, the budget report, the final outcomes report (and a draft of it with gaps), and a thin note that fails everything. `scripts/demo_fixtures.py` writes them deterministically; `uv run pytest -m eval tests/evals -s` runs the agreement and the evidence through the real model as a [pydantic-evals](https://ai.pydantic.dev/evals/) dataset and reports each beat of the demo script as a pass or fail. The money part is PayPal's real sandbox: set `PAYPAL_TEST_PAYER_EMAIL`/`_PASSWORD` and `PAYPAL_TEST_PAYEE_*` to throwaway sandbox accounts and the demo page shows them to visitors, so they can approve a hold without a PayPal developer account of their own. The demo's people have addresses at a reserved domain nothing is delivered to; the server signs them in itself (`POST /demo/cast`, rate-limited) and drops any mail to them. Everything else they do is the ordinary API.

There is no keyless mode. Tests, and `scripts/dev_server.py` for working on the web app, build the sandbox-mode app with a stand-in PayPal, an in-memory mailer and a stand-in GitHub App instead (`create_app(settings, paypal=..., mailer=..., github_app=...)`).

To develop on the host instead, you need [uv](https://docs.astral.sh/uv/):

```sh
uv sync
uv run uvicorn app.main:app --reload
# open http://localhost:8000/api/docs  (the web app is served at / once `cd web && npm run build` has run)
```

By default it uses SQLite at `./local.db`. For Postgres:

```sh
docker compose up -d db
DATABASE_URL=postgresql://milestone:milestone@localhost:5433/milestone uv run uvicorn app.main:app --reload
```

Copy `.env.example` to `.env` for the PayPal sandbox credentials and `SECRET_KEY`.

## Web app

A React app for every side of an agreement (funder, grantee, reviewer) lives in [`web/`](web/README.md). Its design system, *Ledger & Stamp*, is WCAG 2.2 AA with its checks automated: [`web/DESIGN.md`](web/DESIGN.md).

```sh
cd web && npm install && MDE_API=http://localhost:8000 npm run dev   # http://localhost:5173/
```

Run the API with `PUBLIC_BASE_URL=http://localhost:5173` so PayPal's redirects return through the dev server (it proxies `/api` and the MCP paths). Without PayPal credentials, `uv run python scripts/dev_server.py --port 8000` runs it with stand-in PayPal, email and GitHub; sign-in links print in its terminal. After `npm run build`, the server serves the app itself at `/`, the API under `/api` and the MCP server at `/mcp`.

## Try the flow

Everything is done as a signed-in person acting for an organization (see "Organizations, people and access" in the spec).

1. **Sign in:** `POST /auth/magic-link {"email": ...}` then `POST /auth/magic-link/verify {"token": ...}` returns a session token; send `Authorization: Bearer <token>`. The link is emailed (with `EMAIL_PROVIDER=console` it is in the server log). Orgs can add OIDC single sign-on for domains they own (`PUT /orgs/{id}/sso`, verify each domain via DNS TXT, then people sign in with `POST /auth/sso/start`).
2. **Payer org:** `POST /orgs {"name", "paypal_email"}`. Invite colleagues with `POST /orgs/{id}/invitations {"email", "role"}` (owner, admin, member, viewer); integrations and agents use `POST /orgs/{id}/api-keys`.
3. **Draft:** `POST /agreements/extract` (PDF or text) proposes tranches and criteria, or `POST /criteria/lint` checks ones you wrote. Then `POST /orgs/{id}/agreements` with `counterparty_email` (and optional `reviewer_emails`) creates it and sends invitations.
4. **Payee and reviewers** sign in with the invited email and `POST /invitations/accept` for one of their orgs (or a new one). Pending invitations show in `GET /me`.
5. **Confirm:** each party's admin `POST /agreements/{id}/confirm` with the `criteria_hash` they reviewed. The agreement goes live when the payee has joined, every party (reviewers included) has signed, and no invitation is pending.
6. **Fund** (escrow): payer `POST /milestones/{id}/fund` returns an `approve_url` for the PayPal hold. Invoice mode skips this.
7. **Evidence:** payee members pre-check (`/precheck`), then submit (`/evidence`, `/uploads`, or merged GitHub PRs). Checks run first, then the AI verifier; `GET /milestones/{id}/verdict` shows results with citations.
8. **Decide:** with auto-release on and a confident verdict, a funded tranche settles itself. Otherwise it waits in each side's `GET /orgs/{id}/review-queue` for a payer admin or reviewer to `POST /milestones/{id}/review` (with `require_reviewer_approval`, only a reviewer can approve). Rejections return what's missing; the payee can `/dispute`.
9. **Agents (MCP):** point an MCP client (Claude Desktop, Claude Code, etc.) at `<base>/mcp`. It discovers the OAuth endpoints, opens a consent page where you sign in and pick the org it acts for, then gets org-bound tools (list agreements, status, evidence, verdicts, pre-check, submit evidence, review queue, portfolio, compliance packet, drafting). Headless agents use an org API key as the bearer token. Confirming, approving, releasing, funding and disputes stay with people.
10. **Track:** `GET /orgs/{id}/agreements?side=payer|payee|reviewer`, `GET /orgs/{id}/portfolio`, audit logs, compliance packets (JSON/PDF), and signed outbound webhooks via `POST /agreements/{id}/subscriptions`.

The whole thing in one file: [`sdk/python/examples/quickstart.py`](sdk/python/examples/quickstart.py) or [`sdk/typescript/examples/quickstart.ts`](sdk/typescript/examples/quickstart.ts) run steps 2 to 8 against the hosted sandbox demo (or a local dev server) and print what happened; [`webhook_receiver.py`](sdk/python/examples/webhook_receiver.py) / [`webhook-receiver.ts`](sdk/typescript/examples/webhook-receiver.ts) show step 10 from the receiving end. A complete example draft is in [`tests/conftest.py`](tests/conftest.py), and [`tests/helpers.py`](tests/helpers.py) walks the whole sign-in, org and invitation flow.

## Running it for real

Two scheduled jobs and a few settings turn a local run into a deployment (all in `.env.example`):

- `python -m app.jobs daily` once a day: clears expired PayPal holds (29 days), cancels invoices left unpaid for 30 days, and marks tranches whose due date passed with no evidence `overdue` (both sides are emailed; evidence and funding are still accepted).
- `python -m app.jobs payouts` hourly: asks PayPal about captures, pay-now payments and invoices still in flight (so a missed webhook never strands a tranche), pays tranches whose dispute window closed, follows Payouts still in flight, polls the PayPal Disputes API for disputes no webhook announced (before paying anything), and ends contests nobody resolved within 28 days of capture the way PayPal does (paid to the payee). The app also runs this in-process every `PAYOUTS_INTERVAL_SECONDS`; with more than one instance set that to 0 and schedule the job once.
- **Email:** `EMAIL_PROVIDER=resend` (with `RESEND_API_KEY`) or `smtp` (with `SMTP_*`), plus `EMAIL_FROM`. Sign-in links, invitations, review requests, rejections with what's missing, pay links, overdue notices and unclaimed or failed payouts go to the tranche's assignee on that side, or to every member of the party's org. `console` (the default) only logs them.
- **Rate limits:** sign-in links per IP and per address, extraction and pre-check per caller, pay-link visits per IP (`RATE_LIMIT_*_PER_HOUR`); `TRUST_PROXY_HEADERS` says whether `X-Forwarded-For` names the client.
- **PayPal disputes:** `CUSTOMER.DISPUTE.*` webhooks are recorded on the tranche; a dispute against a tranche still in its window pauses the payout as a contest for a reviewer to resolve. The hourly `payouts` job also polls the Disputes API (the platform account and each payee with a capture in the last 180 days) as a backstop. Reading a connected payee's disputes needs the `READ_SELLER_DISPUTE` onboarding feature, which payees who connected before Nov 5 don't have; those accounts are skipped and counted.
- **GitHub App:** `uv run python scripts/github_app_setup.py --public-url https://your.host` registers the app in one click (GitHub's manifest flow) and writes its `GITHUB_APP_*` settings to `github-app.env`; add them to the environment.
  - Orgs install it from **Organization > GitHub** in the web app. The callback checks with the installer's own GitHub token that they can access the installation, and the admin who started it confirms it for their org.
  - Payees then pick a repo instead of configuring a webhook, and merged, labelled PRs arrive at `/webhooks/github/app`.
  - Reads use each installation's short-lived token, so the handover check can read a client's private repo.
  - Contractor-owned repos (agreements that withhold work until capture): the work stays in the payee's private repo, a release or tag named for a tranche pins the commit, and once the tranche is paid the code goes to the client as a PR into their repo (they install the app on it), a source archive, or the contractor transferring the repo to the client, which the app watches for (by repo id, with the verified commit on the default branch). PRs and archives are checked against the verified commit's git tree first; a transfer completes any of the three. Handovers are worked by an in-process loop, or by the `deliver-webhooks` job.
  - Each PR's merge commit gets a "Milestone Ledger" check run that follows the tranche. It's posted by an in-process loop, or by the `deliver-webhooks` job when that loop is off.
  - The per-repo webhook still works without the app.
- **Custody of held work:** uploaded files are encrypted at rest (AES-256-GCM) with `FILE_ENCRYPTION_KEY`, or a key derived from `SECRET_KEY` when that isn't set. To turn on a dedicated key or rotate it, set the new key, list the previous ones in `FILE_ENCRYPTION_OLD_KEYS`, and run `python -m app.jobs encrypt-files` once (it also encrypts files stored before encryption existed). `RETENTION_DAYS` (off by default) has the `daily` job end custody of a closed tranche's originals that many days after its last activity, never within 180 days of capture (PayPal's chargeback window): `RETENTION_ACTION=delete` removes the files and evidence text and keeps their hashes, `payee_only` keeps them for the payee side alone.
- **Live mode:** `MODE=live` uses PayPal's production API, pays connected payees only (never the platform route), and requires an https `PUBLIC_BASE_URL` and `PAYPAL_WEBHOOK_ID`. It also needs PayPal's partner approval and a BN code. The demo is off on a live deployment.
- `uv run python scripts/paypal_sandbox_check.py` checks the PayPal credentials, the platform merchant id and the webhook subscription from `.env` without running the app (`--order` also creates a $1 hold link).
- `uv run python scripts/sandbox_walkthrough.py` walks the whole wallet flow through a running sandbox-mode app (escrow, pay-now and Payouts), pausing wherever you act in PayPal: see [`docs/SANDBOX.md`](docs/SANDBOX.md).

MCP clients act only through the tools shown at consent, even when they call the REST API directly with their access token: confirming, funding, reviewing, releasing, contesting, disputing and organization management (members, keys, SSO) stay with people.

## Code layout

```
app/
  main.py          app factory: wires settings, PayPal, AI, GitHub, routers
  config.py        settings (env / .env)
  jobs.py          scheduled jobs: daily expiry and overdue sweep, payouts, webhook delivery
  models/          single source of truth: one SQLModel definition per entity (XBase -> table X, XCreate, XRead),
                   plus enums and the value objects stored as typed JSON (Criterion, Measure, CriterionResult)
  api/             HTTP endpoints by resource, plus shared deps (auth, roles) and response assembly
  db/              engine, sessions, audit log + webhook outbox
  domain/          criteria lint and deterministic checks
  ai/              Pydantic AI agents (extraction, verification) and extraction post-processing
  services/        settlement (escrow + invoice), verification and gate, notifications, rate limits,
                   outbound webhooks, compliance packet
  integrations/    PayPal gateway (mock; PayPal Server SDK for orders and payments, REST for the rest), email
                   (console, Resend, SMTP), GitHub, OIDC, file uploads
scripts/           paypal_sandbox_check.py: verify PayPal credentials and webhook setup from .env
                   sandbox_walkthrough.py: run the full wallet flow through the app against PayPal sandbox
                   demo_fixtures.py: write the demo's grant agreement and evidence files to demo/
demo/              the demo's documents (grant agreement PDF, sign-in sheets, budget report, outcomes report)
tests/evals/       the demo's documents through the real model (pydantic-evals; `uv run pytest -m eval tests/evals -s`)
web/               React app (Vite, React Aria, AG Grid); served at / from web/dist (API under /api, MCP at /mcp)
```

## Develop

```sh
uv run pytest                              # hermetic: stand-in PayPal, mailer, GitHub App and model
uv run pytest -m eval tests/evals -s       # the demo's documents through the real model (spends credits)
uv run ruff check . && uv run ruff format .
uv run python -m app.sdk_spec   # after changing a core operation: rewrites sdk/openapi-core.json
```

### SDK helpers

`sdk/python` and `sdk/typescript` are small dependency-free packages (`milestone-ledger`) with `verify_webhook` /
`verifyWebhook`: signature and timestamp checks for outbound webhooks, constant-time, with stable error codes and a
typed event. Both are tested against deliveries signed by the engine's own signer (`sdk/fixtures/webhooks.json`, from
`scripts/sdk_fixtures.py`), so they can't drift from `app/services/outbound.py`. See `sdk/README.md`.

### SDK core spec

Operations tagged `x-sdk-core` (in `openapi_extra`) make up the SDK's API: drafting and confirming an agreement, funding, evidence, verdicts, review, release, the ledger and subscriptions. `python -m app.sdk_spec` writes just those, with the schemas they use, to `sdk/openapi-core.json`. CI (and `tests/test_sdk_spec.py`) fails when the committed file is stale, so SDK-facing changes are always deliberate. The core spec has its own semver (`CORE_VERSION` in `app/sdk_spec.py`); a breaking change to a core operation bumps the major version.

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

`render.yaml` is a Render Blueprint: the web service, a Postgres database and two cron jobs (`payouts` hourly, `daily` each morning), sharing one environment group. After the first deploy set the secrets marked `sync: false` in the dashboard (`PUBLIC_BASE_URL`, the PayPal keys, `PAYPAL_WEBHOOK_ID`, `PAYPAL_PARTNER_MERCHANT_ID`, `ANTHROPIC_API_KEY`, an email provider) and switch `MODE` to `sandbox`. Point the PayPal app's webhook at `<PUBLIC_BASE_URL>/api/webhooks/paypal` and subscribe it to the authorization, capture, payouts, referenced-payout, invoicing, merchant and customer-dispute events (`scripts/paypal_sandbox_check.py` lists any missing). The database is on the smallest paid plan because Render deletes free databases after 30 days.

## Security and compliance

Report vulnerabilities privately; see [SECURITY.md](SECURITY.md).

- **No card data touches the engine.** Payers pay on PayPal's own pages; we only see order, authorization and
  capture ids. That keeps the engine out of PCI DSS cardholder-data scope.
- **Money transmission.** `MODE=live` pays connected payees directly (PayPal partner onboarding with delayed
  disbursement, so the payer's money goes to the payee's account) and refuses the platform route, which would hold
  money in transit. The platform route exists for sandbox and demos only. A legal review comes before going live
  ([#28](https://github.com/SeraphDev6/milestone_disbursment_agent/issues/28)).
- **Human-only actions.** Confirming criteria, funding, reviewing, releasing and disputing are never available to
  agents: MCP access tokens are limited to the tools shown at consent, even on REST, and can't mint API keys.
- **Audit trail.** Every state change is an audit event with its actor. Verdicts are append-only and store the
  model, prompt version and criteria hash needed to replay them. `GET /agreements/{id}/compliance-packet` exports an
  agreement's compliance packet (criteria, evidence hashes, verdicts, reviews, money movements).
- **Secrets.** Org SSO client secrets are encrypted at rest; API keys and magic-link tokens are stored hashed.
- **Held deliverables.** The engine holds the payee's evidence files to verify them and, where the agreement
  withholds the work until payment, to release them at capture. What it does with them:
  - **Who can open them:** the payee side and reviewers, always; the payer side only what the payee chose to show
    (hashes, the verdict, quotes or a watermarked preview) until the tranche is captured, then the originals. The
    verifier model reads them to judge the criteria. Platform operators can read the database and the bucket;
    nobody else.
  - **Every read is recorded:** which side viewed or downloaded which item, and whether it saw the withheld or
    the full view. That record is the payee's dispute pack.
- **Tamper-evident audit log.** Each audit event's SHA-256 covers its fields and the previous event's hash on the same
  agreement, so the log is a chain: a row edited, removed or inserted later breaks it from that point. The compliance
  packet opens with the chain's status and head hash, and `python -m app.jobs verify-audit` checks every agreement
  (`seal-audit` hashes rows written before the chain existed).
- **Access reviews.** `GET /orgs/{id}/access-review` (admin) lists who can act for an org: members and roles, pending
  invitations, API keys (revoked ones included) and the MCP clients people have connected, with last use.
  - **Storage:** encrypted at rest by the app (AES-256-GCM), in the database or, with `FILE_STORAGE=s3`, a
    Cloudflare R2 (or any S3-compatible) bucket that only ever holds ciphertext. Every download goes through the
    app, which checks access, records the read and checks the file's hash; there are no direct bucket links.
  - **Retention:** kept until the operator's `RETENTION_DAYS` after a tranche closes (never within 180 days of
    capture), then deleted or kept for the payee only. Off by default, so files are kept until deleted.
  - **Not done:** the engine doesn't take ownership of the work, use it for anything but verifying and releasing
    it, train models on it, or share it beyond the agreement's parties and the verifier.
- **Licensing.** The engine is AGPL-3.0. The SDK
  ([#12](https://github.com/SeraphDev6/milestone_disbursment_agent/issues/12)) will be permissively licensed so
  integrators can embed it.

**Subprocessors** (for the hosted service at https://app.milestone-ledger.com):

| Vendor | Purpose | Data it receives | Region |
| --- | --- | --- | --- |
| Render | Hosting and Postgres | All service data, including evidence files | US (Oregon) |
| PayPal | Payments, holds, payouts, invoices, payee onboarding | Payer and payee PayPal emails, amounts, tranche titles, invoice notes | US |
| Anthropic | Agreement extraction and evidence verification (Claude) | Agreement documents, criteria and evidence content | US |
| Resend (or the operator's SMTP server) | Email: sign-in links, invitations, notifications | Recipient emails, names, tranche titles and status | US |

GitHub: the engine reads the repos an org chose when it installed the GitHub App, using that installation's short-lived token (permissions: metadata read; contents, pull requests and checks write). It writes check runs, and, only for a paid tranche from a contractor-owned repo with the pull-request handover, one branch and one PR into the client's repo the client chose; it never writes to a contractor's repo, and a transfer handover writes nothing. Without the app it reads with the operator's optional `GITHUB_TOKEN`. Org SSO talks to
each customer's own identity provider. We don't publish vendors' audit reports; see each vendor's trust center.

**Policies and roadmap.** Generic policy templates will live in `docs/policy-templates/`
([#23](https://github.com/SeraphDev6/milestone_disbursment_agent/issues/23)). The operative policies are kept in
a private repository and are available on request under NDA. The roadmap (SOC 2, then SOC 1, reconciliation,
privacy and AI governance) is tracked in
[#21](https://github.com/SeraphDev6/milestone_disbursment_agent/issues/21).

## License

[AGPL-3.0-only](LICENSE).
