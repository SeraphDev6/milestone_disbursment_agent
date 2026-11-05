# Outstanding issues

Logged Nov 3, 2026; updated the same day after the hardening pass (items marked **Done**). **Reconciled Nov 4:** every open item now has its own GitHub issue (P0, P1, P2 and stretch labels); this file keeps the record of what was found and done, and the issues track what's left (submission closes Nov 12, 12:00 PT). Each item says what's wrong or unverified, why it matters, and the next step. Priority: **P0** blocks the demo or submission; **P1** should be done before submission; **P2** is after the hackathon or a nice-to-have.

## PayPal: verified only in parts

| # | Pri | Issue | Next step |
| --- | --- | --- | --- |
| 1 | P0 | **The full wallet flow has never run through the app in sandbox.** Every PayPal call was checked on its own (card path, or a created order), but nobody has approved a hold in PayPal and returned to `/paypal/return`, or paid through `/pay/{id}` → `/paypal/paid`. | **Moved to #33.** |
| 2 | P0 | **No live webhooks.** These need a public URL and `PAYPAL_WEBHOOK_ID`. The resource fields we match on come from PayPal docs and have never been seen in a real event: payouts-item `payout_item.sender_item_id` and `payout_batch_id`; referenced-payout `item_id` and `reference_id`; `MERCHANT.ONBOARDING.COMPLETED` `merchant_id` and `tracking_id`; consent-revoked `merchant_id`; capture `supplementary_data.related_ids.order_id`. | **Moved to #34** (with the dispute-event subscription from #6). |
| 3 | P1 | **Renewing a hold with the "acting for the payee" header is untested past day 3.** Sandbox only ever returned `REAUTHORIZATION_TOO_SOON`. Sandbox holds don't expire, so the demo is safe, but production renews on day 4 or later. | **Moved to #38.** |
| 4 | P1 | **Retried Payouts are recognised by message text.** PayPal answers a reused `sender_batch_id` with HTTP 400 "Batch with given sender_batch_id already exists", and we detect it with a substring match on `sender_batch_id`. | **Done (Nov 3).** `tests/test_hardening.py` covers the 400 body with the field name in either case; detection is now case-insensitive. |
| 5 | P1 | **PayPal's automatic release after 28 days is assumed, not observed.** The payouts job marks a contested direct tranche as settled 28 days after capture, based on PayPal's docs. No webhook for it is confirmed. | **Moved to #39.** |
| 6 | P1 | **Customer disputes and chargebacks aren't handled.** `CUSTOMER.DISPUTE.*` events and a reversal after a payout are ignored. Another team saw no dispute webhooks in sandbox. | **Done in part (Nov 3).** `CUSTOMER.DISPUTE.CREATED/UPDATED/RESOLVED` are recorded on the tranche; a dispute against a tranche still in its window pauses the payout as a contest. Polling the Disputes API is not built; subscribe the webhook to the dispute events. |
| 7 | P1 | **Unclaimed payouts don't notify anyone.** The tranche stays `held` and the audit log notes it, but the payee isn't told to claim or create a PayPal account. | **Done (Nov 3).** The payee side is emailed to claim it (and the payer side when a payout fails). |
| 8 | P2 | **Invoicing as the payee isn't available.** `INVOICE_READ_WRITE` returns `FEATURES_UNAUTHORIZED`, there's no dashboard switch, and it needs PayPal's partner approval. Invoice mode uses pay links or a platform invoice plus Payouts instead. | **Moved to #42.** |
| 9 | P2 | **Going live needs PayPal partner approval and a BN code** (`PAYPAL_BN_CODE`). The platform route holds money in transit, which raises money-transmission questions. | **Covered by #28** (Compliance epic #21). |
| 10 | P2 | **A contested platform-route tranche can sit indefinitely.** Unlike direct captures, PayPal never releases it on its own. | **Done (Nov 3).** After 28 days (the same period PayPal allows a direct capture) the `payouts` job pays the payee, with a `contest_timed_out` audit event. A refund under way is left alone. |
| 11 | P2 | **A failed refund leaves the tranche `contested`.** The audit log notes it, but the UI has nothing to act on. | **Done (Nov 3).** `MilestoneRead.refund_failed` flags it; resolving again with `refund` retries (attempt 2). |
| 12 | P2 | **The payee's two PayPal identities can differ.** `Party.paypal_email` (used for Payouts) and the connected account's email are never compared. | **Done (Nov 3).** `GET /orgs/{id}/paypal` returns both `primary_email` (from PayPal) and `paypal_email` (the org's), with a note when they differ. Needs the new migration. |
| 13 | P2 | **The platform's merchant ID is found with a throwaway order** when `PAYPAL_PARTNER_MERCHANT_ID` is unset. It costs nothing and expires, but it's hacky. | **Done (Nov 3)** in `render.yaml` (`sync: false`); `scripts/paypal_sandbox_check.py` prints the value to set. |
| 14 | P2 | **Any PayPal error while reading a payee's status at funding falls back to the platform route.** That avoids blocking funding, but it means a PayPal outage moves money through the platform. | **Done (Nov 3).** A PayPal error while checking a payee that started connecting refuses funding (502, retry); only a payee that never connected falls back to the platform. |
| 15 | P2 | **The sandbox check scripts live only in the session scratchpad.** | **Done (Nov 3)** as `scripts/paypal_sandbox_check.py` (settings, token, partner merchant id, webhook events, optional $1 order; prints no secrets). |
| 16 | P2 | **The APIMatic PayPal Server SDK swap hasn't been done** (a prize item). The gateway is our own httpx client. | **Done as a hybrid (Nov 3).** `paypal-server-sdk` 2.4.0 covers Orders and Payments only, so the gateway calls the SDK for create/authorize/capture orders and reauthorize/capture/void/refund, and keeps httpx for Payouts, referenced payouts, Partner Referrals, Invoicing and webhook verification (none of which are in the SDK). Both share one httpx client (`tests/test_paypal_sdk.py`). |

## App behaviour gaps

| # | Pri | Issue | Next step |
| --- | --- | --- | --- |
| 17 | P1 | **The `overdue` job isn't built.** The spec says a passed `due_date` with no evidence moves the tranche to `overdue` and notifies both sides; only the enum value exists. | **Done (Nov 3).** `mark_overdue` in the `daily` job; both sides are emailed; evidence and funding are still accepted, and an overdue hold can be voided. |
| 18 | P1 | **There's no email provider.** `ConsoleMailer` only logs. Outside mock mode, magic links, invitations, pay links and rejection or unclaimed notices aren't delivered. | **Done (Nov 3).** `EMAIL_PROVIDER=resend|smtp` behind one `send`; pay links, review requests, rejections, overdue and payout notices are emailed to the side's assignee or the party org's members. |
| 19 | P1 | **No rate limits** on magic-link requests, extraction (which spends model credits), pre-check or `/pay/{id}` (each visit creates a PayPal order). | **Done (Nov 3).** Per-IP and per-email limits on magic links, per-caller on extraction and pre-check, per-IP on `/pay/{id}` (`RATE_LIMIT_*_PER_HOUR`), in memory per instance, 429 with Retry-After. |
| 20 | P1 | **`/pay/{settlement_id}` needs no sign-in;** the unguessable ID is the only protection. Anyone with the link can pay the payee, which is harmless but also creates PayPal orders. | Rate-limited per IP (Nov 3). Signed, expiring links **moved to #41.** |
| 21 | P1 | **Rejection notifications aren't sent.** The payee is told what's missing only through the API and audit log. | **Done (Nov 3).** The payee side is emailed each unmet criterion's `missing` text and the reviewer's note. |
| 22 | P1 | **No security review yet** of the auth, OAuth, SSO and money paths. | **Done (Nov 3).** Review of auth, OAuth, SSO and money paths. Fixed: MCP access tokens could call human-only REST endpoints and mint API keys (now limited to the tools shown at consent); a payee could be paid while a refund to the payer was in flight on the platform route (now refused); SSO discovery could reach private addresses (endpoints are vetted like outbound webhooks). The two low-severity notes are closed too: outbound webhooks post to the vetted address (TLS still checks the hostname), and the magic-link page and browser SSO flows finish only in the browser that started them (SameSite cookie binding). |
| 23 | P2 | **Several app instances could race on payouts.** The in-process payouts loop runs in every instance. Retry-safe keys prevent double payment, but two instances can collide when creating the same attempt (a unique-key error, which is retried). | **Done (Nov 3).** `render.yaml` sets `PAYOUTS_INTERVAL_SECONDS=0` on the web service and runs `payouts` as an hourly cron job. |
| 24 | P2 | **Invoice-mode rows from before the payout routes have `payout_route = NULL`.** They settle as before, but `/pay/{id}` can't redirect them because they have no stored `payer_url`. | **Won't fix:** dev data only; reset the dev database. |

## Deployment and submission

| # | Pri | Issue | Next step |
| --- | --- | --- | --- |
| 25 | P0 | **Deployment is on hold (your call).** The live MCP vendor checklist, live webhooks and the hosted demo URL all depend on it. | **Done (Nov 4):** deployed at https://app.milestone-ledger.com (#32). |
| 26 | P0 | **No git remote.** Nothing is pushed. The rules need a public repo with the license visible (planned: public under SeraphDev6). | **Done (Nov 4).** Public at `SeraphDev6/milestone_disbursment_agent`, AGPL-3.0 shown in the About section. |
| 27 | P1 | **`render.yaml` has no cron jobs** for `python -m app.jobs daily` and `payouts`, and no worker. The frontend session also changed its build command (uncommitted). | **Done (Nov 3).** `render.yaml` has the `payouts` and `daily` cron jobs sharing an env group, `--proxy-headers` on the web start command, and `AUTO_MIGRATE=false` on the jobs. |
| 28 | P1 | **Render free Postgres may not outlive judging** (winners announced Dec 21). | Decided in `render.yaml` (Nov 3): `basic-256mb`. Deployed (#32). |
| 29 | P1 | **The live MCP vendor checks haven't been run:** claude.ai, Claude Code, ChatGPT, VS Code, Cursor, Gemini CLI and MCP Inspector (see `docs/MCP_CLIENTS.md`). | **Moved to #35.** |
| 30 | P1 | **No demo seed or scripted run.** | **Partly done:** `/demo` seeds a grant with three people in mock mode. The rest **moved to #36.** |
| 31 | P1 | **The demo video isn't recorded.** | **Moved to #37.** |

## Coordination

| # | Pri | Issue | Next step |
| --- | --- | --- | --- |
| 32 | P1 | **The frontend session (`ledger-stamp-frontend`) has uncommitted changes in shared files:** `app/main.py`, `app/api/__init__.py`, `app/api/orgs.py`, `tests/test_orgs.py`, `README.md` and `render.yaml`, plus its own new files. My commits deliberately left these out. | **Done (Nov 3).** The frontend landed in `1ddcb13`. |
| 33 | P1 | **The frontend hasn't confirmed it read the API contract update** (payout statuses, `pay_path`, connect PayPal, contest and resolve). | **Done.** The UI covers payout statuses, `pay_path`, connect PayPal, contest and resolve. |

## Stretch and cut list

| Item | Where |
| --- | --- |
| Skeptic verification pass | #9 (Verified Delivery epic #1) |
| Agent as payee | #43 |
| Email evidence adapter | #44 |
| Zapier | #45 |
| Generated SDKs | SDK epic #12 |
| Weighted partial release | #46 |
| Co-funded grants | #47 |
| ChatGPT `private_key_jwt` and mTLS | #48 |
