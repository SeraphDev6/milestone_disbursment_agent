# Business plan: verified delivery on PayPal

As of Nov 4, 2026. This describes the business model, what is open and what is not, and the road from hackathon to live. Pricing figures and go-to-market specifics are kept out of the public repo.

## The problem

Work paid by milestone runs on trust and chasing. A contractor delivers and then waits weeks for the client to sign off. A grantee files a report and the next tranche sits until a program officer reads it. Whoever moves first takes the risk. If the contractor hands over the work first, the client can stop paying. If the client pays first, the contractor can stop delivering. Escrow solves half of that: it protects the money, but not the work.

## What we sell

**Verified delivery.** Both sides agree on checkable acceptance criteria up front. The payer's money is held at PayPal and the payee's work is held by the engine. When the evidence meets the criteria, PayPal captures the payment and the work is released at the same moment, followed by a short window for the payer to contest. Neither side has to go first.

- **For payees** (contractors, agencies, grantees): guaranteed funds before you start, criteria you can pre-check before you submit, and work that stays yours until you're paid.
- **For payers** (clients, funders): you pay only against evidence that meets what you agreed to, every decision comes with citations, and the audit trail is ready for your finance team or auditors.
- **For PayPal:** volume that moves on a verified event, through partner onboarding, payer to payee directly.

## Who it's for

We start where PayPal is already the way money moves, and where tranches are small enough that card and wallet fees are acceptable.

1. **Independent contractors and small agencies** working off a SOW: software, design, content, accessibility remediation. Code and digital deliverables are the easiest to verify and the most exposed to non-payment.
2. **Clients of those contractors** who want proof of delivery before paying, without hiring someone to check it.
3. **Microgrants and small community grants** (community foundations, participatory grantmaking, sponsorship programs), where reporting is the bottleneck and the amounts suit PayPal.

Out of scope for now: large institutional grants paid by ACH or wire. That market is bigger, but it needs other settlement rails. The engine keeps settlement behind an adapter so that path stays open later. See [Later](#later).

## How it makes money

| Stream | Who pays | How |
| --- | --- | --- |
| Platform fee | Payer, on each captured tranche | A percentage with a cap, collected as a PayPal partner platform fee on the capture. We earn when money actually moves on verified work. |
| Funder plans | Funders running grant programs | A subscription per active agreement for portfolio dashboards, reviewer workflows and compliance packets. Grantees use it free. |
| Closed modules | Organizations that need them | Separate services over the API: reporting formats for grant compliance (Candid, Vestri), extended custody and retention of held deliverables, enterprise identity (SSO enforcement, access reviews). |
| Reviewer network | Paid per review, by the side that requests it | Neutral reviewers who can see withheld work when the payer can't, and who decide low-confidence verdicts. A fee per review, shared with the reviewer. |

Payees never pay to get paid. Pre-check and the criteria lint are free because they bring payees in, and payees bring their clients.

## Why this lasts

- **PayPal partner status.** Paying payees directly needs PayPal partner approval and a BN code. That approval comes from PayPal, not from code.
- **Trust in the verdict.** Deterministic checks first, a model with citations for the rest, a skeptic pass before auto-release, and a human gate below the threshold. Each verdict can be replayed. Trust here builds slowly and is hard to copy quickly.
- **Two-sided protection.** Competing escrow products protect the payer's money. Holding the work as well, and releasing it at capture, is what lets a contractor agree to the terms.
- **The record.** Every agreement leaves an audit trail and a compliance packet. Funders and finance teams come to depend on it.

## Open and closed

| Open (AGPL-3.0, this repo) | Permissive (Apache-2.0 or MIT) | Closed (separate services over the API) |
| --- | --- | --- |
| The engine: agreements, criteria and lint, verification, the release gate, PayPal settlement, evidence adapters, the MCP server, the web app | The client SDK for the core flow ([SDK epic](https://github.com/SeraphDev6/milestone_disbursment_agent/issues/12)) | Compliance reporting formats, extended custody and retention, enterprise identity, the reviewer network |

Closed modules talk to the engine over the REST API and are never linked into it. Outside contributions require a contributor license agreement, so the code can be licensed in more than one way.

## From hackathon to live

1. **Hackathon (Nov 12, 2026):** sandbox end to end, with `/demo` on the sandbox deployment for judges.
2. **Verified delivery** ([epic #1](https://github.com/SeraphDev6/milestone_disbursment_agent/issues/1)): withheld work, reveal at capture, hash commitments, a contractor-owned code mode, and the skeptic pass.
3. **SDK** ([epic #12](https://github.com/SeraphDev6/milestone_disbursment_agent/issues/12)): the core flow on npm and PyPI, so other products can embed verified delivery.
4. **Go live on PayPal:** partner approval and BN code, direct route only (the platform never holds funds), and the platform fee on captures.
5. **Compliance** ([epic #21](https://github.com/SeraphDev6/milestone_disbursment_agent/issues/21)): SOC 2 policies, then Type I and Type II. Reconciliation and a tamper-evident audit log come first, because SOC 1 for public-company customers depends on them.
6. **Design partners:** a handful of contractors and their clients, plus one small grant program, running real tranches before wider launch.

## Risks

| Risk | Response |
| --- | --- |
| PayPal partner approval is slow or refused | Sandbox and the demo don't depend on it. Apply right after the hackathon. Package the policies, the money-flow description and the sandbox checks with the application. |
| Money transmission | Production uses the direct route only, so payer pays payee and the platform never holds funds. The platform route stays sandbox-only (enforced in live mode). Legal review before launch. |
| A verdict is wrong and money moves | Checks before the model, citations required, the skeptic pass, human review below the threshold, a contest window after capture, and verdict replay for every decision. |
| Chargebacks after work is revealed | The pinned hash, the reveal timestamp and the payer's access log go into the dispute pack. |
| PayPal fees put off larger payers | Start where tranches are small. Keep settlement behind an adapter for other rails later. |
| Holding other people's work | Custody policy, encryption, retention limits, and access by role ([#11](https://github.com/SeraphDev6/milestone_disbursment_agent/issues/11)). |

## Later

- **Other settlement rails** (ACH, wire) for institutional grants, behind the same settlement adapter.
- **Partial release** by criterion weight, and **co-funded grants** with several funders per agreement.
- **Agents as payees:** an AI agent takes a milestone over MCP, submits its work and gets paid when it verifies.
