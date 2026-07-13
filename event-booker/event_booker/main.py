from logging import getLevelNamesMapping

import structlog
from dishka import make_async_container
from dishka.integrations.fastapi import FastapiProvider, setup_dishka
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from event_booker.config import get_settings
from event_booker.errors import (
    ConflictError,
    NotFoundError,
    SlotUnavailableError,
    UpstreamError,
    ValidationError,
)
from event_booker.ioc import AppProvider
from event_booker.logger import setup_logger
from event_booker.metrics import HttpMetricsMiddleware
from event_booker.routers.public import public_router
from event_booker.routes import root_router
from event_booker.telemetry import instrument_fastapi, setup_tracing


container = make_async_container(AppProvider(), FastapiProvider())
logger = structlog.get_logger(__name__)

_settings = get_settings()
setup_logger(log_level=getLevelNamesMapping().get(_settings.log_level), console_render=_settings.debug)

app = FastAPI(title="event-booker", version="0.1.0")
setup_tracing()
instrument_fastapi(app)
setup_dishka(container=container, app=app)
app.include_router(root_router)
app.include_router(public_router)
app.add_middleware(HttpMetricsMiddleware)

if _settings.cors_origins_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_settings.cors_origins_list,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

_STATUS = {
    ValidationError: 422,
    NotFoundError: 404,
    ConflictError: 409,
    SlotUnavailableError: 409,
    UpstreamError: 502,
}


async def _domain_error_handler(_: Request, exc: Exception) -> JSONResponse:
    status = next((code for typ, code in _STATUS.items() if isinstance(exc, typ)), 500)
    return JSONResponse(status_code=status, content={"detail": str(exc)})


for _err in (ValidationError, NotFoundError, ConflictError, SlotUnavailableError, UpstreamError):
    app.add_exception_handler(_err, _domain_error_handler)
