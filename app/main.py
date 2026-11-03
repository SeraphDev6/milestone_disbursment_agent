import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.ai.agents import claude_runtime, mock_runtime
from app.api import api_router, browser, mock_router
from app.api.mcp import build_mcp
from app.api.spa import mount_spa
from app.config import Mode, Settings, get_settings, use_claude
from app.db import make_engine, make_sessionmaker, migrate
from app.integrations.crypto import SecretBox
from app.integrations.email import make_mailer
from app.integrations.github import GitHubClient
from app.integrations.oidc import OIDCClient, lookup_txt
from app.integrations.paypal import make_gateway
from app.services import ServiceError
from app.services.notifications import NOTIFIER, Notifier
from app.services.outbound import resolve_host, run_every, start_delivery_loop
from app.services.ratelimit import RateLimited, RateLimiter
from app.services.settlement import disburse_due

log = logging.getLogger("mde.app")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Browser hardening for the few HTML pages (sign-in, consent, PayPal returns) and the served web app."""

    def __init__(self, app, *, https: bool):
        super().__init__(app)
        self.https = https

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if self.https:
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if response.headers.get("content-type", "").startswith("text/html") and not request.url.path.startswith("/app"):
            # The API's own pages are static forms: no scripts, no outside resources. The web app sets its own.
            h.setdefault("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'")
        return response


def create_app(settings: Settings | None = None) -> FastAPI:
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
                    app.state.sessionmaker, settings.outbound_interval_seconds, allow_http=settings.is_mock
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
    app.state.paypal = make_gateway(settings)
    key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else None
    app.state.ai = claude_runtime(key) if use_claude(settings) else mock_runtime()
    app.state.github = GitHubClient(token=settings.github_token)
    app.state.resolve = resolve_host
    app.state.mailer = make_mailer(settings)
    app.state.notifier = Notifier(app.state.mailer, settings.public_base_url, settings.public_app_url)
    app.state.ratelimiter = RateLimiter()
    app.state.oidc = OIDCClient(allow_local=settings.is_mock, resolve=resolve_host)
    app.state.txt_lookup = lookup_txt
    app.state.secrets = SecretBox(settings.secret_key.get_secret_value())

    @app.exception_handler(ServiceError)
    async def _service_error(request: Request, exc: ServiceError) -> JSONResponse:
        headers = {"Retry-After": str(exc.retry_after)} if isinstance(exc, RateLimited) else None
        return JSONResponse({"detail": exc.message}, status_code=exc.status_code, headers=headers)

    app.include_router(api_router)
    app.include_router(browser.router)
    if settings.mode is Mode.mock:
        app.include_router(mock_router)
    mount_spa(app)  # the web app at /app, when web/dist has been built

    # MCP (tools generated from the routes above) and its OAuth endpoints. Mounted last at "/" so every
    # FastAPI route matches first; it serves /mcp, /.well-known/*, /authorize, /token, /register, /revoke.
    mcp, app.state.oauth = build_mcp(app, settings.public_base_url)
    app.state.mcp_app = mcp.http_app(path="/mcp", stateless_http=True, json_response=True)
    app.mount("/", app.state.mcp_app)
    return app


def _startup_warnings(settings: Settings) -> None:
    """Things a deployment outside mock mode usually wants, logged once so they're visible in the host's logs."""
    if settings.is_mock:
        return
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


app = create_app()
