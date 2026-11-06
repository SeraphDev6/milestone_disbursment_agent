# Marketing site

The public site for Milestone Ledger, served from GitHub Pages. Static HTML and CSS, no framework, no build
dependencies beyond Node. It follows the app's design system, *Ledger & Stamp* ([`web/DESIGN.md`](../web/DESIGN.md)),
and the same WCAG 2.2 AA contract, checked the same way.

## Layout

```
site/
  build.mjs        assembles _site/: wraps pages in the layout, copies tokens, themes and fonts from web/
  layout.html      header, nav, theme picker, footer; {{title}} {{description}} {{nav}} {{content}}
  pages/           one file per page; starts with a <!-- title: … description: … --> comment
  css/site.css     the site's own styles; colour comes only from the app's tokens
  js/theme.js      the theme picker (same localStorage key as the app, "mde.theme")
  static/          copied as is: robots.txt, .nojekyll
```

The Pages workflow also renders the SDK's core spec (`sdk/openapi-core.json`) with Redocly into `_site/sdk/reference.html`
and copies the spec beside it. That page is Redocly's output, not one of ours, so it isn't part of the axe scan.

Colour is never declared here. `build.mjs` copies `web/src/design/themes.css` (generated from `palette.ts`, every pair
contrast-checked) and `tokens.css`, and the woff2 files for Newsreader, Atkinson Hyperlegible Next and Atkinson
Hyperlegible Mono from `web/node_modules/@fontsource`, so the site and the app cannot drift apart and no font CDN is
used.

## Build and preview

```sh
cd web && npm ci && cd ..            # fonts and Playwright come from the web app's packages
node site/build.mjs                  # -> _site/
python3 -m http.server 8008 --directory _site
```

## Accessibility check

Every page, in Paper, Carbon and Contrast: axe with the WCAG 2.0–2.2 A/AA rules, no console errors, one `h1`,
landmarks, the skip link, no horizontal scrolling at 320px, forced colours, the theme picker, and every internal
link resolving.

```sh
node site/build.mjs
cd web && npx playwright test -c playwright.site.config.ts
```

## Deploy

[`.github/workflows/pages.yml`](../.github/workflows/pages.yml) builds, runs the check and deploys to GitHub Pages on
every push to `main` that touches the site (pull requests get the build and the check only). Pages is enabled with
**Source: GitHub Actions** and the custom domain `milestone-ledger.com` (`site/static/CNAME` keeps it across deploys;
the apex has GitHub Pages' A/AAAA records and `www` is a CNAME to `seraphdev6.github.io`). All links are relative so
the site also works at any base path.

If a deploy ever sits in `deployment_queued` and times out, or fails at once with "Deployment cancelled": GitHub keys
Pages deployments by commit, and a freshly created Pages site can refuse its first deployment. What cleared it on
Nov 6, 2026: push the built `_site/` to a `gh-pages` branch, set the source to that branch, `POST /pages/builds`
once so the site builds, then set the source back to GitHub Actions and re-run the workflow.

### Custom domain (milestone-ledger.com)

Set up Nov 6, 2026: the apex carries GitHub Pages' `A`/`AAAA` records, `www` is a `CNAME` to `seraphdev6.github.io`,
the domain is set under **Settings → Pages → Custom domain**, and `site/static/CNAME` keeps it. `app` stays on
Render and `mail` on Resend. If the certificate ever lapses, **Enforce HTTPS** in the same settings page re-requests it.

## Contact addresses

The pages name these addresses; each needs a Resend inbox (or forwarding):

| Address | Used on |
| --- | --- |
| `support@milestone-ledger.com` | footer, home, terms, privacy, 404 |
| `security@milestone-ledger.com` | security page, footer |
| `privacy@milestone-ledger.com` | privacy policy, footer |
| `accessibility@milestone-ledger.com` | accessibility statement, footer |
| `legal@milestone-ledger.com` | terms of use |

Note that `EMAIL_FROM` sends from `mail.milestone-ledger.com`, which already has an MX record; receiving at the apex
needs the apex domain added to Resend and its MX records at the apex (Cloudflare allows MX beside the flattened
CNAME or beside A records).

## Before going live

The Terms and Privacy pages carry a "Sandbox stage" stamp. Before a live deployment:

- legal review of both documents;
- fill in the legal entity, the governing law and venue (Terms §19), and the fee schedule (Terms §8);
- confirm the retention setting and the log-retention wording in the Privacy Policy match the deployment;
- re-date both documents and email account holders about the change, as the documents promise.
