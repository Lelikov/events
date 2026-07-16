#!/bin/sh
# Apply migrations, then start the service. event-organizer owns its own
# database schema, so the container is the single migration runner.
set -e

alembic upgrade head

exec uvicorn event_organizer.main:app --host 0.0.0.0 --port 8888 --log-config uvicorn_config.json
