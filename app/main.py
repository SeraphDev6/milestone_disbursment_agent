from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.ai.agents import claude_runtime, mock_runtime
from app.api import api_router, browser, mock_router
from app.api.mcp import build_mcp
from app.api.spa import mount_spa
from app.config import Mode, Settings, get_settings, use_claude
from app.db import make_engine, make_sessionmaker, migrate
from app.integrations.crypto import SecretBox
from app.integrations.email import ConsoleMailer
from app.integrations.github import GitHubClient
from app.integrations.oidc import OIDCClient, lookup_txt
from app.integrations.paypal import make_gateway
from app.services import ServiceError
from app.services.outbound import resolve_host, run_every, start_delivery_loop
from app.services.settlement import disburse_due


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        if settings.auto_migrate:
            migrate.upgrade(engine)
        app.state.engine = engine
        app.state.sessionmaker = make_sessionmaker(engine)
        stops = []
        if settings.outbound_interval_seconds > 0:
            stops.append(
                start_delivery_loop(
                    app.state.sessionmaker, settings.outbound_interval_seconds, allow_http=settings.mode is Mode.mock
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
        expose_headers=["WWW-Authenticate", "Mcp-Session-Id", "Mcp-Protocol-Version"],
        max_age=600,
    )
    app.state.paypal = make_gateway(settings)
    key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else None
    app.state.ai = claude_runtime(key) if use_claude(settings) else mock_runtime()
    app.state.github = GitHubClient(token=settings.github_token)
    app.state.resolve = resolve_host
    app.state.mailer = ConsoleMailer()
    app.state.oidc = OIDCClient()
    app.state.txt_lookup = lookup_txt
    app.state.secrets = SecretBox(settings.secret_key.get_secret_value())

    @app.exception_handler(ServiceError)
    async def _service_error(request: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse({"detail": exc.message}, status_code=exc.status_code)

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


app = create_app()
