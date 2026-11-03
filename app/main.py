from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import sessionmaker

from app import escrow_routes, routes
from app.config import Mode, Settings, get_settings
from app.db import Base, make_engine
from app.escrow import EscrowError
from app.paypal import MockPayPal, PayPalGateway, SandboxPayPal


def make_gateway(settings: Settings) -> PayPalGateway:
    if settings.mode is Mode.mock:
        return MockPayPal(base_url=settings.public_base_url)
    return SandboxPayPal(
        client_id=settings.paypal_client_id,
        client_secret=settings.paypal_client_secret,
        base_url=settings.paypal_base_url,
        webhook_id=settings.paypal_webhook_id,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        Base.metadata.create_all(engine)
        app.state.engine = engine
        app.state.sessionmaker = sessionmaker(engine, expire_on_commit=False)
        yield
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

    @app.exception_handler(EscrowError)
    async def _escrow_error(request: Request, exc: EscrowError) -> JSONResponse:
        return JSONResponse({"detail": exc.message}, status_code=exc.status_code)

    app.include_router(routes.router)
    app.include_router(escrow_routes.router)
    if settings.mode is Mode.mock:
        app.include_router(escrow_routes.mock_router)
    return app


app = create_app()
