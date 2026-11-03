# Outstanding issues

Logged Nov 3, 2026 (submission closes Nov 12, 12:00 PT). Each item says what's wrong or unverified, why it matters, and the next step. Priority: **P0** blocks the demo or submission; **P1** should be done before submission; **P2** is after the hackathon or a nice-to-have.

## PayPal: verified only in parts

| # | Pri | Issue | Next step |
| --- | --- | --- | --- |
| 1 | P0 | **The full wallet flow has never run through the app in sandbox.** Every PayPal call was checked on its own (card path, or a created order), but nobody has approved a hold in PayPal and returned to `/paypal/return`, or paid through `/pay/{id}` → `/paypal/paid`. | Run the app with `MODE=sandbox` and a temporary `SECRET_KEY`. Fund one escrow tranche and pay one invoice-mode tranche as the sandbox personal account. Confirm the ledger and statuses. |
| 2 | P0 | **No live webhooks.** These need a public URL and `PAYPAL_WEBHOOK_ID`. The resource fields we match on come from PayPal docs and have never been seen in a real event: payouts-item `payout_item.sender_item_id` and `payout_batch_id`; referenced-payout `item_id` and `reference_id`; `MERCHANT.ONBOARDING.COMPLETED` `merchant_id` and `tracking_id`; consent-revoked `merchant_id`; capture `supplementary_data.related_ids.order_id`. | Once deployed (or through a tunnel), subscribe to the authorization, capture, payouts-item, referenced-payout, invoicing and merchant events, trigger each one, and save a sample of every payload as a test fixture. |
| 3 | P1 | **Renewing a hold with the "acting for the payee" header is untested past day 3.** Sandbox only ever returned `REAUTHORIZATION_TOO_SOON`. Sandbox holds don't expire, so the demo is safe, but production renews on day 4 or later. | Can't be faked in sandbox. Note it as unverified, or ask PayPal support. |
| 4 | P1 | **Retried Payouts are recognised by message text.** PayPal answers a reused `sender_batch_id` with HTTP 400 "Batch with given sender_batch_id already exists", and we detect it with a substring match on `sender_batch_id`. | Keep it, but add a test fixture with the real response body. If PayPal changes the wording, the fallback is the webhook matched on `sender_item_id`. |
| 5 | P1 | **PayPal's automatic release after 28 days is assumed, not observed.** The payouts job marks a contested direct tranche as settled 28 days after capture, based on PayPal's docs. No webhook for it is confirmed. | Check what the capture or referenced-payout status looks like after an automatic release (sandbox may not simulate it). |
| 6 | P1 | **Customer disputes and chargebacks aren't handled.** `CUSTOMER.DISPUTE.*` events and a reversal after a payout are ignored. Another team saw no dispute webhooks in sandbox. | At minimum, record the dispute on the tranche and in the audit log, and poll the Disputes API on a schedule. |
| 7 | P1 | **Unclaimed payouts don't notify anyone.** The tranche stays `held` and the audit log notes it, but the payee isn't told to claim or create a PayPal account. | Send a notification through the mailer and outbound events. |
| 8 | P2 | **Invoicing as the payee isn't available.** `INVOICE_READ_WRITE` returns `FEATURES_UNAUTHORIZED`, there's no dashboard switch, and it needs PayPal's partner approval. Invoice mode uses pay links or a platform invoice plus Payouts instead. | Ask PayPal to enable it if the product continues, then add the permission to onboarding and send invoice calls with `PayPal-Auth-Assertion`. |
| 9 | P2 | **Going live needs PayPal partner approval and a BN code** (`PAYPAL_BN_CODE`). The platform route holds money in transit, which raises money-transmission questions. | Apply after the hackathon. Production should run the direct route only. |
| 10 | P2 | **A contested platform-route tranche can sit indefinitely.** Unlike direct captures, PayPal never releases it on its own. | Add a contest deadline and an escalation (for example, reviewer decides or auto-refund after N days). |
| 11 | P2 | **A failed refund leaves the tranche `contested`.** The audit log notes it, but the UI has nothing to act on. | Add a `refund_failed` attention state, or retry the refund through `resolve`. |
| 12 | P2 | **The payee's two PayPal identities can differ.** `Party.paypal_email` (used for Payouts) and the connected account's email are never compared. | Show both on the payee's PayPal status, or read the connected account's primary email. |
| 13 | P2 | **The platform's merchant ID is found with a throwaway order** when `PAYPAL_PARTNER_MERCHANT_ID` is unset. It costs nothing and expires, but it's hacky. | Set `PAYPAL_PARTNER_MERCHANT_ID` in every deployed environment. |
| 14 | P2 | **Any PayPal error while reading a payee's status at funding falls back to the platform route.** That avoids blocking funding, but it means a PayPal outage moves money through the platform. | Fall back only when the account isn't connected; on PayPal errors, refuse to fund and ask to retry. |
| 15 | P2 | **The sandbox check scripts live only in the session scratchpad.** | Move a cleaned version into `scripts/paypal_sandbox_check.py` that reads `.env` and never prints secrets. |
| 16 | P2 | **The APIMatic PayPal Server SDK swap hasn't been done** (a prize item). The gateway is our own httpx client. | Check that the SDK covers Orders, Authorizations, Payouts, referenced payouts and Partner Referrals; swap behind the `PayPalGateway` interface. |

## App behaviour gaps

| # | Pri | Issue | Next step |
| --- | --- | --- | --- |
| 17 | P1 | **The `overdue` job isn't built.** The spec says a passed `due_date` with no evidence moves the tranche to `overdue` and notifies both sides; only the enum value exists. | Add it to the `daily` job, with an audit event. |
| 18 | P1 | **There's no email provider.** `ConsoleMailer` only logs. Outside mock mode, magic links, invitations, pay links and rejection or unclaimed notices aren't delivered. | Add a Resend or SMTP sender behind `send()`, and email the payer their pay link. |
| 19 | P1 | **No rate limits** on magic-link requests, extraction (which spends model credits), pre-check or `/pay/{id}` (each visit creates a PayPal order). | Add per-IP and per-org limits. |
| 20 | P1 | **`/pay/{settlement_id}` needs no sign-in;** the unguessable ID is the only protection. Anyone with the link can pay the payee, which is harmless but also creates PayPal orders. | Acceptable for now. Consider signed, expiring links and a rate limit. |
| 21 | P1 | **Rejection notifications aren't sent.** The payee is told what's missing only through the API and audit log. | Send it through the mailer and outbound events. |
| 22 | P1 | **No security review yet** of the auth, OAuth, SSO and money paths. | Run `/security-review` on main before submission. |
| 23 | P2 | **Several app instances could race on payouts.** The in-process payouts loop runs in every instance. Retry-safe keys prevent double payment, but two instances can collide when creating the same attempt (a unique-key error, which is retried). | Run one worker, or set `PAYOUTS_INTERVAL_SECONDS=0` and use the `payouts` cron job. |
| 24 | P2 | **Invoice-mode rows from before the payout routes have `payout_route = NULL`.** They settle as before, but `/pay/{id}` can't redirect them because they have no stored `payer_url`. | Dev data only; drop or backfill. |

## Deployment and submission

| # | Pri | Issue | Next step |
| --- | --- | --- | --- |
| 25 | P0 | **Deployment is on hold (your call).** The live MCP vendor checklist, live webhooks and the hosted demo URL all depend on it. | Deploy to Render when you're ready. |
| 26 | P0 | **No git remote.** Nothing is pushed. The rules need a public repo with the license visible (planned: public under SeraphDev6). | Create the repo, push `main`, and check the license shows in the About section. |
| 27 | P1 | **`render.yaml` has no cron jobs** for `python -m app.jobs daily` and `payouts`, and no worker. The frontend session also changed its build command (uncommitted). | Add the cron services; agree the build with the frontend session. |
| 28 | P1 | **Render free Postgres may not outlive judging** (winners announced Dec 21). | Check its retention and budget a paid tier. |
| 29 | P1 | **The live MCP vendor checks haven't been run:** claude.ai, Claude Code, ChatGPT, VS Code, Cursor, Gemini CLI and MCP Inspector (see `docs/MCP_CLIENTS.md`). | Do it after deploying. |
| 30 | P1 | **No demo seed or scripted run.** | Add a seed script for the grants-led demo (agreements, orgs, a connected payee, evidence files) and rehearse the 3-minute script. |
| 31 | P1 | **The demo video isn't recorded.** | Nov 11. |

## Coordination

| # | Pri | Issue | Next step |
| --- | --- | --- | --- |
| 32 | P1 | **The frontend session (`ledger-stamp-frontend`) has uncommitted changes in shared files:** `app/main.py`, `app/api/__init__.py`, `app/api/orgs.py`, `tests/test_orgs.py`, `README.md` and `render.yaml`, plus its own new files. My commits deliberately left these out. | They commit their own work. Check that it rebases cleanly on `c0945dd`. |
| 33 | P1 | **The frontend hasn't confirmed it read the API contract update** (payout statuses, `pay_path`, connect PayPal, contest and resolve). | Wait for their reply; confirm the UI covers the new statuses. |

## Stretch and cut list (unchanged from the spec)

ChatGPT `private_key_jwt` and mTLS, skeptic verification pass, agent-as-payee, email evidence adapter, Zapier, generated SDKs, weighted partial release.
