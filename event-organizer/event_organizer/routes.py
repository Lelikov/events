from fastapi import APIRouter
from starlette.responses import Response

from event_organizer import metrics

root_router = APIRouter()


@root_router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@root_router.get("/ready")
async def ready() -> dict[str, str]:
    return {"status": "ready"}


@root_router.get("/metrics")
async def metrics_endpoint() -> Response:
    return metrics.metrics_response()
