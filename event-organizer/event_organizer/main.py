from logging import getLevelNamesMapping

import structlog
from dishka import make_async_container
from dishka.integrations.fastapi import FastapiProvider, setup_dishka
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from event_organizer.config import get_settings
from event_organizer.errors import (
    ConflictError,
    Forbidden,
    NotFoundError,
    Unauthorized,
    UpstreamError,
    ValidationError,
)
from event_organizer.ioc import AppProvider
from event_organizer.logger import setup_logger
from event_organizer.metrics import HttpMetricsMiddleware
from event_organizer.routes import root_router
from event_organizer.telemetry import instrument_fastapi, setup_tracing

container = make_async_container(AppProvider(), FastapiProvider())
logger = structlog.get_logger(__name__)

_settings = get_settings()
setup_logger(log_level=getLevelNamesMapping().get(_settings.log_level), console_render=_settings.debug)

app = FastAPI(title="event-organizer", version="0.1.0")
setup_tracing()
instrument_fastapi(app)
setup_dishka(container=container, app=app)
app.include_router(root_router)
app.add_middleware(HttpMetricsMiddleware)

_STATUS = {
    Unauthorized: 401,
    Forbidden: 403,
    NotFoundError: 404,
    ConflictError: 409,
    ValidationError: 422,
    UpstreamError: 502,
}


async def _domain_error_handler(_: Request, exc: Exception) -> JSONResponse:
    status = next((code for typ, code in _STATUS.items() if isinstance(exc, typ)), 500)
    return JSONResponse(status_code=status, content={"detail": str(exc)})


for _err in (Unauthorized, Forbidden, NotFoundError, ConflictError, ValidationError, UpstreamError):
    app.add_exception_handler(_err, _domain_error_handler)
