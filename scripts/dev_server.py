"""The app with every outside service replaced by a stand-in, for working on the web app and for the e2e suite.

    uv run python scripts/dev_server.py                      # http://localhost:8000 (set PORT, PUBLIC_BASE_URL)
    uv run python scripts/dev_server.py --port 8765          # with the Vite dev server: PUBLIC_BASE_URL=http://localhost:5173

It is the sandbox-mode app (MODE has only sandbox and live) built the way the tests build it (tests/conftest.py):
PayPal is MockPayPal, whose approval, onboarding and invoice pages are served under /api/mock; email is logged, so
sign-in and invitation links are in this terminal; the GitHub App is a stand-in with its own install page; the
model is the deterministic stand-in. The demo (/demo) is on and its cast is not rate-limited. Nothing here is a
deployment: a deployment runs `uvicorn app.main:app` with real PayPal credentials.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn

from app.api.spa import make_spa
from app.config import hermetic_settings
from app.integrations.email import ConsoleMailer
from app.integrations.github_app import MockGitHubApp
from app.integrations.paypal import MockPayPal
from app.main import DIST, SecurityHeadersMiddleware, Site, create_app


def build(public_base_url: str, database_url: str):
    settings = hermetic_settings(
        public_base_url=public_base_url,
        database_url=database_url,
        web_app_built=None,  # serve web/dist when it exists
        outbound_interval_seconds=5.0,
        payouts_interval_seconds=30.0,
        rate_limit_demo_per_hour=0,
    )
    api = create_app(
        settings,
        paypal=MockPayPal(base_url=settings.api_base_url),
        mailer=ConsoleMailer(),
        github_app=MockGitHubApp(base_url=settings.api_base_url),
    )
    spa = make_spa() if (DIST / "index.html").exists() else make_spa(DIST / "missing")
    return Site(api, SecurityHeadersMiddleware(spa, https=False, csp=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--database-url", default=os.environ.get("DATABASE_URL", "sqlite:///./dev.db"), help="default: ./dev.db"
    )
    args = parser.parse_args()
    public = os.environ.get("PUBLIC_BASE_URL", f"http://localhost:{args.port}")
    print(f"stand-in server: {public} (PayPal, email and GitHub are stand-ins; links are printed here)")
    uvicorn.run(build(public, args.database_url), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
