# Build context is the event-booker directory itself (no event-schemas dependency):
#   docker build -f event-booker/Dockerfile event-booker
ARG BASE_IMAGE="python:3.14.0"

FROM ${BASE_IMAGE} AS base
ENV APP_PATH="/app/event-booker"
ENV PATH="${APP_PATH}/.venv/bin:${PATH}"
WORKDIR ${APP_PATH}

FROM base AS deps
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir --upgrade uv==0.11.3
COPY pyproject.toml uv.lock ${APP_PATH}/
RUN uv sync --frozen --no-install-project --no-dev

FROM deps AS development
COPY event_booker ${APP_PATH}/event_booker
COPY uvicorn_config.json ${APP_PATH}/
COPY entrypoint.sh ${APP_PATH}/entrypoint.sh
RUN chmod +x ${APP_PATH}/entrypoint.sh
EXPOSE 8888
ENTRYPOINT ["./entrypoint.sh"]
