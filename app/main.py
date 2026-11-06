import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.ai.agents import claude_runtime, mock_runtime
from app.api import api_router, browser, demo_router, mock_router
from app.api.mcp import build_mcp
from app.api.spa import DIST, has_build, make_spa
from app.config import API_PREFIX, DEMO_EMAIL_DOMAIN, Settings, get_settings, use_claude
from app.db import make_engine, make_sessionmaker, migrate
from app.integrations import filecrypt, filestore
from app.integrations.crypto import SecretBox
from app.integrations.email import Mailer, SkipUndeliverable, make_mailer
from app.integrations.github import GitHubClient
from app.integrations.github_app import GitHubApp, MockGitHubApp, make_github_app
from app.integrations.oidc import OIDCClient, lookup_txt
from app.integrations.paypal import MockPayPal, PayPalGateway, make_gateway
from app.services import ServiceError
from app.services.github_app import publish_checks
from app.services.handover import run as run_handovers
from app.services.notifications import NOTIFIER, Notifier
from app.services.outbound import resolve_host, run_every, start_delivery_loop
from app.services.ratelimit import RateLimited, RateLimiter
from app.services.settlement import disburse_due

log = logging.getLogger("mde.app")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Browser hardening for the API's own HTML pages (sign-in, consent, PayPal returns) and the served web app."""

    def __init__(self, app, *, https: bool, csp: bool = True):
        super().__init__(app)
        self.https = https
        self.csp = csp  # the API's own pages are static forms; the web app sets its own policy

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if self.https:
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if (
            self.csp
            and response.headers.get("content-type", "").startswith("text/html")
            and not request.url.path.endswith(("/docs", "/redoc", "/docs/oauth2-redirect"))  # Swagger has scripts
        ):
            # Static forms: no scripts, no outside resources.
            h.setdefault("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'")
        return response


MCP_PATHS = ("/authorize", "/token", "/register", "/revoke")  # the MCP server's OAuth endpoints, at the root


def is_mcp_path(path: str) -> bool:
    return path == "/mcp" or path.startswith(("/mcp/", "/.well-known/")) or path in MCP_PATHS


class Site:
    """One host, three apps: the REST API under API_PREFIX, the MCP server (and its OAuth endpoints) at the root,
    and the web app for everything else. Lifespan belongs to the API app, which also runs the MCP server's."""

    def __init__(self, api: FastAPI, spa, prefix: str = API_PREFIX):
        self.api, self.spa, self.prefix = api, spa, prefix

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.api(scope, receive, send)
        path = scope.get("path", "")
        if path == self.prefix or path.startswith(self.prefix + "/"):
            # Starlette strips root_path from the path when routing, so the API sees its own paths.
            return await self.api({**scope, "root_path": scope.get("root_path", "") + self.prefix}, receive, send)
        if is_mcp_path(path):
            return await self.api(scope, receive, send)
        return await self.spa(scope, receive, send)


def create_app(
    settings: Settings | None = None,
    *,
    paypal: PayPalGateway | None = None,
    mailer: Mailer | None = None,
    github_app: GitHubApp | None = None,
) -> FastAPI:
    """The REST API and the MCP server. `paypal`, `mailer` and `github_app` replace the real integrations with
    stand-ins (tests, and the walkthrough's dry run); the deployment never passes them. When the PayPal or GitHub
    stand-in is in use, its pages (approval, onboarding, invoices, app install) are mounted under /mock."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        if settings.auto_migrate:
            migrate.upgrade(engine)
        app.state.engine = engine
        app.state.sessionmaker = make_sessionmaker(engine, info={NOTIFIER: app.state.notifier})
        stops = []
        if settings.outbound_interval_seconds > 0:
            stops.append(
                start_delivery_loop(
                    app.state.sessionmaker, settings.outbound_interval_seconds, allow_http=settings.local
                )
            )
        if settings.outbound_interval_seconds > 0 and app.state.github_app is not None:  # check runs, handovers
            stops.append(
                run_every(
                    "github-checks",
                    app.state.sessionmaker,
                    max(settings.outbound_interval_seconds, 10.0),
                    lambda session, now: publish_checks(
                        session, app.state.github_app, app.state.notifier.tranche_link, now
                    ),
                )
            )
            stops.append(
                run_every(
                    "github-handovers",
                    app.state.sessionmaker,
                    max(settings.outbound_interval_seconds, 30.0),
                    lambda session, now: run_handovers(session, app.state.github_app, now),
                )
            )
        if settings.payouts_interval_seconds > 0:  # pay tranches whose dispute window closed; follow Payouts
            stops.append(
                run_every(
                    "payouts",
                    app.state.sessionmaker,
                    settings.payouts_interval_seconds,
                    lambda session, now: disburse_due(session, app.state.paypal, now),
                )
            )
        _startup_warnings(settings)
        async with app.state.mcp_app.router.lifespan_context(app.state.mcp_app):
            yield
        for stop in stops:
            stop.set()
        engine.dispose()

    app = FastAPI(
        title="Milestone Disbursement Engine",
        version="0.1.0",
        description="Turns an agreement into checkable acceptance criteria, verifies evidence against them, "
        "and settles each tranche through PayPal.",
        license_info={"name": "AGPL-3.0-only", "identifier": "AGPL-3.0-only"},
        lifespan=lifespan,
    )
    app.state.settings = settings
    # Deployed behind the Site dispatcher at API_PREFIX. Setting it here (rather than on the server) makes the docs
    # and OpenAPI self-describe correctly and lets the app accept prefixed paths directly too (tests, TestClient).
    app.root_path = API_PREFIX
    # Bearer-token API (no cookies cross-origin), so any origin may call it. Browser-based MCP clients
    # (e.g. MCP Inspector) need preflight answers and these headers exposed.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["WWW-Authenticate", "Mcp-Session-Id", "Mcp-Protocol-Version", "Retry-After"],
        max_age=600,
    )
    app.add_middleware(SecurityHeadersMiddleware, https=settings.public_base_url.startswith("https://"))
    app.state.paypal = paypal or make_gateway(settings)
    key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else None
    app.state.ai = claude_runtime(key) if use_claude(settings) else mock_runtime()
    app.state.github = GitHubClient(token=settings.github_token)
    app.state.github_app = github_app or make_github_app(settings)
    app.state.resolve = resolve_host
    app.state.mailer = mailer or make_mailer(settings)
    if settings.demo_enabled:  # the demo's people can't receive mail; don't ask the provider to try
        app.state.mailer = SkipUndeliverable(app.state.mailer, DEMO_EMAIL_DOMAIN)
    # Emailed links open the web app when there is one (or PUBLIC_APP_URL names a separate one), else the API's pages.
    app.state.app_url = settings.public_app_url or (settings.public_base_url if _web_built(settings) else None)
    app.state.notifier = Notifier(app.state.mailer, settings.api_base_url, app.state.app_url)
    app.state.ratelimiter = RateLimiter()
    app.state.oidc = OIDCClient(allow_local=settings.local, resolve=resolve_host)
    app.state.txt_lookup = lookup_txt
    app.state.secrets = SecretBox(settings.secret_key.get_secret_value())
    filecrypt.configure(filecrypt.FileCipher.from_settings(settings))
    filestore.configure(filestore.from_settings(settings), settings.s3_prefix)

    @app.exception_handler(ServiceError)
    async def _service_error(request: Request, exc: ServiceError) -> JSONResponse:
        headers = {"Retry-After": str(exc.retry_after)} if isinstance(exc, RateLimited) else None
        return JSONResponse({"detail": exc.message}, status_code=exc.status_code, headers=headers)

    app.include_router(api_router)
    app.include_router(browser.router)
    if settings.demo_enabled:
        app.include_router(demo_router)
    if isinstance(app.state.paypal, MockPayPal) or isinstance(app.state.github_app, MockGitHubApp):
        app.include_router(mock_router)

    # MCP (tools generated from the routes above) and its OAuth endpoints. Mounted last at "/" so every
    # FastAPI route matches first; it serves /mcp, /.well-known/*, /authorize, /token, /register, /revoke.
    mcp, app.state.oauth = build_mcp(app, settings.public_base_url, settings.api_base_url)
    app.state.mcp_app = mcp.http_app(path="/mcp", stateless_http=True, json_response=True)
    app.mount("/", app.state.mcp_app)
    return app


def create_site(settings: Settings | None = None) -> Site:
    """What uvicorn serves: the API, the MCP server and the web app on one host (see Site)."""
    settings = settings or get_settings()
    api = create_app(settings)
    spa = make_spa() if _web_built(settings) else make_spa(DIST / "missing")
    return Site(api, SecurityHeadersMiddleware(spa, https=settings.public_base_url.startswith("https://"), csp=False))


def _web_built(settings: Settings) -> bool:
    return has_build() if settings.web_app_built is None else settings.web_app_built


def _startup_warnings(settings: Settings) -> None:
    """Things a deployment usually wants, logged once so they're visible in the host's logs."""
    log.info(
        "MODE=%s: PayPal %s%s",
        settings.mode.value,
        settings.paypal_api_url,
        "; demo at /demo" if settings.demo_enabled else "",
    )
    if not settings.paypal_webhook_id:
        log.warning(
            "PAYPAL_WEBHOOK_ID is unset: PayPal webhooks are rejected, so captures and payouts only settle "
            "when the app polls or the payer returns through the browser"
        )
    if not settings.paypal_partner_merchant_id:
        log.warning(
            "PAYPAL_PARTNER_MERCHANT_ID is unset: the platform's merchant id is looked up with a throwaway order"
        )
    if not settings.public_base_url.startswith("https://"):
        log.warning("PUBLIC_BASE_URL is not https: session cookies are sent without the Secure flag")
    if settings.auto_migrate and settings.payouts_interval_seconds > 0:
        log.info(
            "payouts run in-process every %.0fs; with several instances set PAYOUTS_INTERVAL_SECONDS=0 and "
            "schedule `python -m app.jobs payouts` once",
            settings.payouts_interval_seconds,
        )


app = create_site()
