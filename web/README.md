# Web app

React 19 + TypeScript + Vite, served at the site root; the REST API is under `/api` and the MCP server at `/mcp`. The design system and accessibility contract are in [DESIGN.md](DESIGN.md).

## Run it

Everything at once: `docker compose up --build` at the repo root serves the built app at http://localhost:8000/. For development with hot reload:

```sh
# terminal 1: the API with stand-in PayPal, email and GitHub (no keys needed; sign-in links print here)
PUBLIC_BASE_URL=http://localhost:5173 uv run python scripts/dev_server.py --port 8765

# terminal 2: the web app, proxying API paths to it
cd web && npm install && MDE_API=http://localhost:8765 npm run dev
# open http://localhost:5173/
```

The app is the same against every server: it talks to the real API and reads `GET /health` for the mode (`sandbox` or `live`, shown as a ribbon on sandbox) and whether the demo is on. The dev server above only swaps PayPal, email and GitHub for stand-ins; with it:

- **Sign-in and invitation links** are printed in terminal 1 (nothing is emailed anywhere).
- **PayPal approval, onboarding and invoice pages** are local stand-ins under `/api/mock`.

Everything demo-only lives at **`/demo`** (sandbox servers with the demo on):
- loading a ready-made grant with a funder, a grantee and a reviewer signed in (the server signs them in: `POST /demo/cast`)
- one-click "act as" for each
- the PayPal sandbox accounts to use, when the deployment publishes them
- sample evidence files
- shortcuts the product deliberately doesn't have (mark verified, connect the grantee's PayPal), demo grants only

`PUBLIC_BASE_URL` points at Vite so PayPal's redirects come back through the same origin.

In production, `npm run build` writes `web/dist`, and the server serves it at `/` (`app/api/spa.py`) on the same origin as the API (`/api`) and the MCP server (`/mcp`).

## Scripts

| Script | What it does |
| --- | --- |
| `npm run dev` | Vite dev server with the API proxy (`MDE_API`, default `http://localhost:8000`) |
| `npm run build` | Typecheck and build to `dist/` |
| `npm run api:types` | Regenerate `openapi.json` and `src/api/schema.d.ts` from the FastAPI app (run after backend changes) |
| `npm run tokens` | Regenerate `src/design/themes.css` from `palette.ts` and check contrast |
| `npm run check` | Typecheck, lint, token check, unit tests |
| `npm run e2e` | Playwright against a running API + dev server (see `playwright.config.ts`); `E2E_BASE_URL` to target elsewhere |

## Layout

```
src/
  api/        generated schema, typed client (bearer + X-Org-Id), errors, TanStack Query hooks
  auth/       sessions (several at once, for "switch person")
  app/        router, layout, guards (session, org, agreement seat), route announcer
  design/     palette -> themes.css, tokens, base styles, components (Stamp, Seal, Totals, fields, DataGrid…)
  domain/     status -> stamp map, permission rules (mirror of app/services/access.py), formatting
  features/   one folder per area: auth, home, portfolio, draft, agreement, tranche, org, account, design
e2e/          Playwright: money flows, accessibility
```

Every org-scoped URL carries the org: `/o/:orgId/a/:agreementId/m/:milestoneId`. That org is sent as `X-Org-Id`, so a person who sits on several parties of one agreement is never ambiguous.
