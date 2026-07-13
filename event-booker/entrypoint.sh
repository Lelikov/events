#!/bin/sh
set -e
exec uvicorn event_booker.main:app --host 0.0.0.0 --port 8888 --log-config uvicorn_config.json
