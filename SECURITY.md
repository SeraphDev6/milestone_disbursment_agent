# Security policy

Milestone Ledger decides when other people's money moves and holds the work they deliver. Please report problems
privately so we can fix them before they're public.

## Reporting a vulnerability

Use GitHub's private reporting: **Security → Report a vulnerability** on this repository. Please don't open a
public issue, pull request or discussion for a security problem.

Include what you found, how to reproduce it (requests, accounts or agreement setup), and what an attacker gains. We
aim to acknowledge reports within 3 business days and to tell you what we'll do within 10. We'll credit you in the
fix unless you'd rather we didn't.

Test against your own local or mock-mode instance (`MODE=mock`, see the README). Don't test against
https://app.milestone-ledger.com or other people's data, and don't run load or denial-of-service tests against it.

## Supported versions

Only the latest commit on `main` and the hosted deployment it runs are supported. There are no release branches
yet; fixes land on `main`.

## Scope

In scope:

- The engine (`app/`): REST API, MCP server and OAuth, magic-link sign-in, org SSO (OIDC), API keys.
- Money paths: funding, holds, capture, release, refunds, Payouts, invoice mode and pay links, PayPal webhooks.
- Access between orgs and roles: anything that lets one party see another party's evidence or take a step that
  belongs to someone else, or lets an agent (MCP or API key) take a human-only step (confirm, fund, review,
  release, dispute).
- Verification integrity: getting a tranche marked verified or released without meeting its criteria, including
  prompt injection through evidence.
- The web app (`web/`) and outbound webhooks (SSRF, signature bypass).

Out of scope:

- PayPal's, Anthropic's, GitHub's or Render's own services (report to them).
- Findings that need a compromised device, browser or org admin account.
- Missing best-practice headers or rate limits with no demonstrated impact.
- Mock mode's stand-ins (`MockPayPal`, the mock verifier), which are for local use only.
