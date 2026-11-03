from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlmodel import SQLModel

from app.ai.agents import claude_runtime, mock_runtime
from app.api import api_router, mock_router
from app.config import Mode, Settings, get_settings, use_claude
from app.db import make_engine, make_sessionmaker
from app.integrations.github import GitHubClient
from app.integrations.paypal import make_gateway
from app.services import ServiceError
from app.services.outbound import resolve_host, start_delivery_loop


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        SQLModel.metadata.create_all(engine)
        app.state.engine = engine
        app.state.sessionmaker = make_sessionmaker(engine)
        stop = None
        if settings.outbound_interval_seconds > 0:
            stop = start_delivery_loop(
                app.state.sessionmaker, settings.outbound_interval_seconds, allow_http=settings.mode is Mode.mock
            )
        yield
        if stop:
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
    app.state.paypal = make_gateway(settings)
    key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else None
    app.state.ai = claude_runtime(key) if use_claude(settings) else mock_runtime()
    app.state.github = GitHubClient(token=settings.github_token)
    app.state.resolve = resolve_host

    @app.exception_handler(ServiceError)
    async def _service_error(request: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse({"detail": exc.message}, status_code=exc.status_code)

    app.include_router(api_router)
    if settings.mode is Mode.mock:
        app.include_router(mock_router)
    return app


app = create_app()
