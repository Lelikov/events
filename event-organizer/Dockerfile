# Build context is the event-organizer directory itself (this service has NO
# dependency on event-schemas):
#   docker build -f event-organizer/Dockerfile event-organizer
ARG BASE_IMAGE="python:3.14.0"

FROM ${BASE_IMAGE} AS base

ENV APP_PATH="/app/event-organizer"
ENV PATH="${APP_PATH}/.venv/bin:${PATH}"

WORKDIR ${APP_PATH}

FROM base AS deps

RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir --upgrade uv==0.11.3

COPY pyproject.toml uv.lock ${APP_PATH}/
RUN uv sync --frozen --no-install-project --no-dev

FROM deps AS development

COPY alembic.ini ${APP_PATH}/
COPY alembic ${APP_PATH}/alembic
COPY event_organizer ${APP_PATH}/event_organizer
COPY uvicorn_config.json ${APP_PATH}/
COPY entrypoint.sh ${APP_PATH}/entrypoint.sh
RUN chmod +x ${APP_PATH}/entrypoint.sh

EXPOSE 8888

ENTRYPOINT ["./entrypoint.sh"]
