# Running the wallet flow against PayPal sandbox

`scripts/sandbox_walkthrough.py` drives the whole flow through the app against PayPal's sandbox. A run that reaches "Done" shows that funding, release, direct payment, Payouts and pay-now links all work through the app, not just as separate API calls (issue #33). With the sandbox passwords in `.env` it does the PayPal pages itself; otherwise it stops wherever a person acts in PayPal.

It needs neither webhooks nor a public URL. PayPal's return pages are browser redirects, so `localhost` works. Anything PayPal finishes later (a pending capture, a paid invoice, a payout) is picked up by the payouts job asking PayPal, so webhooks only make it faster. Webhooks are issue #34; the sandbox app has one registered for `https://app.milestone-ledger.com/api/webhooks/paypal`.

## Accounts

In the [PayPal developer dashboard](https://developer.paypal.com/dashboard/), sandbox:

| Account | Type | Role in the run | Setting |
| --- | --- | --- | --- |
| App owner | Business | The platform. The REST app must be a **Platform** (partner) app, so payees can connect and be paid directly | `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` |
| Funder | Personal or Business, **not** the app owner or grantee | Approves holds and pays pay-now links | `PAYPAL_TEST_PAYER_EMAIL`, `PAYPAL_TEST_PAYER_PASSWORD` |
| Grantee | Business, **not** the app owner | Connects PayPal through onboarding and receives the money | `PAYPAL_TEST_PAYEE_EMAIL`, `PAYPAL_TEST_PAYEE_PASSWORD` |

The passwords are optional. With both set, the script signs in to PayPal as each account in a Chrome window and approves each page (`scripts/paypal_browser.py`). Without them, you sign in as each account yourself during the run.

## Environment

In `.env`:

```sh
MODE=sandbox
LLM=mock                 # the walkthrough's criteria are deterministic checks; no model call is needed
PAYPAL_CLIENT_ID=...
PAYPAL_CLIENT_SECRET=...
PAYPAL_TEST_PAYER_EMAIL=...      # funder
PAYPAL_TEST_PAYER_PASSWORD=...   # optional: the script does the funder's PayPal pages
PAYPAL_TEST_PAYEE_EMAIL=...      # grantee (business, not the app owner)
PAYPAL_TEST_PAYEE_PASSWORD=...   # optional: the script does the grantee's PayPal pages
SECRET_KEY=...           # python -c "import secrets; print(secrets.token_urlsafe(48))"
EMAIL_PROVIDER=console   # sign-in and invitation links are printed in the server log
PUBLIC_BASE_URL=http://localhost:8000
PAYOUTS_INTERVAL_SECONDS=30   # so the platform-route payout doesn't wait an hour
```

`PAYPAL_WEBHOOK_ID` can stay unset for a local run. Use a fresh database (`./local.db` by default) so earlier runs don't get in the way.

## Run it

```sh
uv run python scripts/paypal_sandbox_check.py         # credentials, partner merchant id, and the webhook if PAYPAL_WEBHOOK_ID is set
uv run uvicorn app.main:app                           # terminal 1, leave it running
uv run --group sandbox python scripts/sandbox_walkthrough.py   # terminal 2
```

The script reads `.env` itself (anything already in the environment wins). `--group sandbox` installs Playwright for the PayPal pages. The browser uses your installed Google Chrome, or Playwright's Chromium after `uv run --group sandbox playwright install chromium`.

Options:

- `--flows escrow`, `--flows paynow` or `--flows platform` (comma-separated) runs part of it.
- `--headless` hides the browser window. `--manual-paypal` prints the PayPal links for you instead, even with the passwords set.
- `--base-url https://<host>` runs against a deployment in sandbox mode. Its emails really go out, so also pass `--payer-email` and `--payee-email` with inboxes you can read (the default `@example.org` addresses receive nothing). Set the service's `PAYOUTS_INTERVAL_SECONDS` low (10 is fine with one instance) so the platform-route payout doesn't wait for the hourly cron. The deployment the judges use is itself in sandbox mode (there is no other non-live mode), so this can run against it; its demo at `/demo` is separate from the walkthrough's agreements.

## Hands-off: reading the emails from Resend

Against a deployment that sends real email, the script can read the sign-in and invitation emails itself from a Resend receiving domain. In `.env`:

```sh
RESEND_INBOX_DOMAIN=mail.milestone-ledger.com   # a Resend domain with receiving enabled (its MX record points at Resend)
RESEND_INBOX_API_KEY=re_...                      # FULL-ACCESS key: send-only keys can't read received mail
```

With both set and no `--payer-email`/`--payee-email`, each run signs up `funder-<run>@` and `grantee-<run>@` that domain. When the app emails them, the script polls Resend's received-emails API, takes the `token=` link out of the email, and carries on. Add the PayPal passwords too and nobody needs to be at the keyboard. Keep this key in your local `.env` only. It can change anything in the Resend account, so it never goes on the deployment, which keeps its send-only `RESEND_API_KEY`.

## What happens, and what you do

The script prints each step and checks the tranche status and ledger after it. `>>` lines are yours. The app's sign-in and invitation links need pasting (from terminal 1, or the inbox against a deployment) unless the script reads them from a Resend inbox (below). The PayPal steps marked *(PayPal)* are done for you when the passwords are set.

1. **Funder signs in.** Paste the sign-in link from terminal 1 (the line with `email to funder+…: Sign in`).
2. **Platform route** (the grantee hasn't connected PayPal):
    1. Paste the grantee's sign-in link, then their invitation link, from terminal 1.
    2. *(PayPal)* Open the PayPal link, sign in as the **funder**, approve the hold. The script waits for `funded`.
    3. The grantee's evidence is submitted, checks pass, and the tranche is captured into the platform account. The payouts loop pays the grantee with PayPal Payouts. The script waits for `settled`.
3. **Escrow, direct route:**
    1. Paste the invitation link for the new agreement.
    2. *(PayPal)* Open the onboarding link, sign in as the **grantee**, and finish connecting. Press Enter back in the script.
    3. *(PayPal)* Approve the hold as the **funder**. Evidence goes in and the capture goes straight to the grantee: `settled`.
4. **Pay-now (invoice mode):**
    1. Paste the invitation link.
    2. *(PayPal)* Evidence goes in and the tranche is verified. Open the pay link and pay as the **funder**: `settled`.

On a PayPal page, don't cancel or go back to the app before approving. The app forgets an order once PayPal sends you to its cancel page, and approving that order afterwards ends on "unknown or superseded funding order". Stop the script and rerun the remaining flows. If the automated browser gets stuck, it says so and leaves the window open for you to finish.

Afterwards, check the money landed in the sandbox accounts (sandbox.paypal.com, as the grantee) and read each agreement's audit log at `/api/agreements/{id}/audit`.

## Record the result

Note on #33 what ran, the PayPal debug ids for anything that failed, and any status or ledger row that differed from the test suite's run with the stand-in PayPal. A difference is a bug in the app or in the stand-in, and the stand-in should be fixed to match PayPal.

## Known PayPal answers

| PayPal says | Means |
| --- | --- |
| `PAYEE_NOT_CONSENTED` (403) on funding | The grantee hasn't finished onboarding, or the REST app isn't a Platform app |
| `FEATURES_UNAUTHORIZED` on onboarding | The app asked for a partner feature it doesn't have. The app requests `PAYMENT`, `REFUND`, `DELAY_FUNDS_DISBURSEMENT` and `READ_SELLER_DISPUTE` (sandbox accepted all four on Nov 5) |
| "unknown or superseded funding order" after approving | PayPal sent the browser to the cancel page first (a cancel, or going back), so the app dropped the order. Fund again |
| `REAUTHORIZATION_TOO_SOON` | Expected before day 4. Sandbox holds don't expire, so the run never needs to reauthorize |
| `PERMISSION_DENIED` on void, capture or refund (direct route) | The `PayPal-Auth-Assertion` for the grantee's merchant id is missing or wrong |
| Onboarding returns but the account isn't ready | Its email isn't confirmed or it can't receive payments yet. The script shows what PayPal reports as missing |

## Webhook payload samples

`tests/fixtures/paypal_simulator/` holds PayPal's sample payload for every event the app handles, from the sandbox webhook simulator (`POST /v1/notifications/simulate-event`, `resource_version` 2.0 for authorization and capture events), and `tests/test_paypal_samples.py` runs each one through the handler. They are PayPal's canned samples, not events from a real flow; those are still #34. What the samples showed (Nov 5):

- Disputes, Payouts items, referenced payouts, refunds and `PAYMENT.CAPTURE.COMPLETED`/`DECLINED` carry every field the handler matches on.
- `MERCHANT.ONBOARDING.COMPLETED` has no `tracking_id` (only `merchant_id` and `partner_client_id`). The handler now matches the merchant id, or asks PayPal's merchant-integrations endpoint for the tracking id.
- The v2 `PAYMENT.AUTHORIZATION.CREATED` sample has no `supplementary_data.related_ids`. The handler now also matches on the authorization id.
- The v2 `PAYMENT.CAPTURE.PENDING`/`DENIED` samples have no `supplementary_data` either. Captures are matched by capture id first, and the payouts job follows pending captures by asking PayPal, so nothing depends on it; a real event will tell us whether PayPal sends it.
- The simulator's invoicing samples are the v1 shape (it refuses `resource_version` 2.0); only the invoice `id` is used.
