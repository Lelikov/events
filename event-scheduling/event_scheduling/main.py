from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from logging import getLevelNamesMapping

import structlog
from dishka import make_async_container
from dishka.integrations.fastapi import FastapiProvider, setup_dishka
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from event_scheduling.config import Settings
from event_scheduling.errors import ConflictError, NotFoundError, ValidationError
from event_scheduling.ioc import AppProvider
from event_scheduling.logger import setup_logger
from event_scheduling.metrics import HttpMetricsMiddleware
from event_scheduling.routers.booking import booking_router
from event_scheduling.routers.event_type import event_type_router
from event_scheduling.routers.schedule import schedule_router
from event_scheduling.routers.slots import slots_router
from event_scheduling.routes import root_router
from event_scheduling.telemetry import instrument_asyncpg, instrument_fastapi, setup_tracing


container = make_async_container(AppProvider(), FastapiProvider())
logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
    settings = await container.get(Settings)
    setup_logger(log_level=getLevelNamesMapping().get(settings.log_level), console_render=settings.debug)
    logger.info("Starting event-scheduling", log_level=settings.log_level, debug=settings.debug)
    yield
    await container.close()
    logger.info("event-scheduling shutdown complete")


app = FastAPI(title="event-scheduling", version="0.1.0", lifespan=lifespan)
setup_tracing()
instrument_fastapi(app)
instrument_asyncpg()
setup_dishka(container=container, app=app)
app.include_router(root_router)
app.include_router(schedule_router)
app.include_router(event_type_router)
app.include_router(slots_router)
app.include_router(booking_router)
app.add_middleware(HttpMetricsMiddleware)

_STATUS = {ValidationError: 422, NotFoundError: 404, ConflictError: 409}


async def _domain_error_handler(_: Request, exc: Exception) -> JSONResponse:
    status = next((code for typ, code in _STATUS.items() if isinstance(exc, typ)), 500)
    return JSONResponse(status_code=status, content={"detail": str(exc)})


for _err in (ValidationError, NotFoundError, ConflictError):
    app.add_exception_handler(_err, _domain_error_handler)
