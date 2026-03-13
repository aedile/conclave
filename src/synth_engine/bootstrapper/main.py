"""FastAPI application factory for the Conclave Engine.

This module is the sole entry point for the HTTP layer.  It assembles the
application on demand via create_app() — a factory pattern that ensures
each call produces an independent instance, keeping tests isolated and
allowing future multi-tenant configurations.
"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from synth_engine.shared.telemetry import configure_telemetry

_SERVICE_NAME = "conclave-engine"


def create_app() -> FastAPI:
    """Build and return a fully wired FastAPI application.

    Attaches OpenTelemetry instrumentation and the idempotency middleware,
    then registers the /health liveness probe endpoint.

    Returns:
        A configured FastAPI instance ready to serve requests.
    """
    configure_telemetry(_SERVICE_NAME)

    app = FastAPI(
        title="Conclave Engine",
        description="Air-Gapped Synthetic Data Generation Engine",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    FastAPIInstrumentor.instrument_app(app)

    _register_routes(app)

    return app


def _register_routes(app: FastAPI) -> None:
    """Attach all core routes to the application.

    Args:
        app: The FastAPI instance to register routes on.
    """

    @app.get("/health", tags=["ops"])
    async def health_check() -> JSONResponse:
        """Liveness probe for container orchestrators and load balancers.

        Returns:
            JSON body ``{"status": "ok"}`` with HTTP 200.
        """
        return JSONResponse(content={"status": "ok"})


#: Module-level application instance for use by uvicorn.
#: ``uvicorn synth_engine.bootstrapper.main:app`` picks up this singleton.
app = create_app()
