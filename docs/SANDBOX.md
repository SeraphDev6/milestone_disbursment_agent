# Running the wallet flow against PayPal sandbox

`scripts/sandbox_walkthrough.py` drives the whole flow through the app against PayPal's sandbox and stops wherever a person acts in PayPal. A run that reaches "Done" shows that funding, release, direct payment, Payouts and pay-now links all work through the app, not just as separate API calls (issue #33).

It needs neither webhooks nor a public URL. PayPal's return pages are browser redirects, so `localhost` works. Anything PayPal finishes later (a pending capture, a paid invoice, a payout) is picked up by the payouts job asking PayPal, so webhooks only make it faster. Live webhooks are issue #34.

## Accounts

In the [PayPal developer dashboard](https://developer.paypal.com/dashboard/), sandbox:

| Account | Type | Role in the run | Setting |
| --- | --- | --- | --- |
| App owner | Business | The platform. The REST app must be a **Platform** (partner) app, so payees can connect and be paid directly | `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` |
| Funder | Personal | Approves holds and pays pay-now links | `PAYPAL_TEST_PAYER_EMAIL` |
| Grantee | Business, **not** the app owner | Connects PayPal through onboarding and receives the money | `PAYPAL_TEST_PAYEE_EMAIL` |

Keep the sandbox password for the funder and grantee to hand. You sign in to PayPal as each of them during the run.

## Environment

In `.env`:

```sh
MODE=sandbox
LLM=mock                 # the walkthrough's criteria are deterministic checks; no model call is needed
PAYPAL_CLIENT_ID=...
PAYPAL_CLIENT_SECRET=...
PAYPAL_TEST_PAYER_EMAIL=...   # funder (personal)
PAYPAL_TEST_PAYEE_EMAIL=...   # grantee (business, not the app owner)
SECRET_KEY=...           # python -c "import secrets; print(secrets.token_urlsafe(48))"
EMAIL_PROVIDER=console   # sign-in and invitation links are printed in the server log
PUBLIC_BASE_URL=http://localhost:8000
PAYOUTS_INTERVAL_SECONDS=30   # so the platform-route payout doesn't wait an hour
```

`PAYPAL_WEBHOOK_ID` can stay unset for this run. Use a fresh database (`./local.db` by default) so earlier runs don't get in the way.

## Run it

```sh
uv run python scripts/paypal_sandbox_check.py        # credentials and partner merchant id; "webhook" fails until #34
uv run uvicorn app.main:app                           # terminal 1, leave it running
uv run python scripts/sandbox_walkthrough.py          # terminal 2
```

To run part of it: `--flows escrow`, `--flows paynow`, or `--flows platform` (comma-separated). Against a deployment that is in sandbox mode: `--base-url https://<host>`. The deployment the judges use stays in mock mode, so run this locally or against a second, sandbox-mode service.

## What happens, and what you do

The script prints each step and checks the tranche status and ledger after it. `>>` lines are yours.

1. **Funder signs in.** Paste the sign-in link from terminal 1 (the line with `email to funder+…: Sign in`).
2. **Platform route** (the grantee hasn't connected PayPal):
    1. Paste the grantee's sign-in link, then their invitation link, from terminal 1.
    2. Open the PayPal link, sign in as the **funder**, approve the hold. The script waits for `funded`.
    3. The grantee's evidence is submitted, checks pass, and the tranche is captured into the platform account. The payouts loop pays the grantee with PayPal Payouts. The script waits for `settled`.
3. **Escrow, direct route:**
    1. Paste the invitation link for the new agreement.
    2. Open the onboarding link, sign in as the **grantee**, and finish connecting. Press Enter back in the script.
    3. Approve the hold as the **funder**. Evidence goes in and the capture goes straight to the grantee: `settled`.
4. **Pay-now (invoice mode):**
    1. Paste the invitation link.
    2. Evidence goes in and the tranche is verified. Open the pay link and pay as the **funder**: `settled`.

Afterwards, check the money landed in the sandbox accounts (sandbox.paypal.com, as the grantee) and read each agreement's audit log at `/api/agreements/{id}/audit`.

## Record the result

Note on #33 what ran, the PayPal debug ids for anything that failed, and any status or ledger row that differed from mock mode. A difference is a bug in the app or in the mock, and the mock should be fixed to match PayPal.

## Known PayPal answers

| PayPal says | Means |
| --- | --- |
| `PAYEE_NOT_CONSENTED` (403) on funding | The grantee hasn't finished onboarding, or the REST app isn't a Platform app |
| `FEATURES_UNAUTHORIZED` on onboarding | The app asked for a partner feature it doesn't have. The app requests `PAYMENT`, `REFUND` and `DELAY_FUNDS_DISBURSEMENT` only |
| `REAUTHORIZATION_TOO_SOON` | Expected before day 4. Sandbox holds don't expire, so the run never needs to reauthorize |
| `PERMISSION_DENIED` on void, capture or refund (direct route) | The `PayPal-Auth-Assertion` for the grantee's merchant id is missing or wrong |
| Onboarding returns but the account isn't ready | Its email isn't confirmed or it can't receive payments yet. The script shows what PayPal reports as missing |
