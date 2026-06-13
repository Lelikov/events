#!/bin/sh
# Render alertmanager.tmpl.yml -> /tmp/alertmanager.yml with env substitution,
# then exec Alertmanager. Alertmanager itself does no general env interpolation
# in its YAML, so we do it here. The prom/alertmanager image is busybox-based
# and ships no `envsubst`, so we substitute our three whitelisted placeholders
# with `sed`. Go-template `{{ ... }}` in the message body is left untouched
# because it never matches a placeholder token.
set -eu

: "${ALERT_TELEGRAM_BOT_TOKEN:=0000000000:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA}"
# chat_id must be a non-zero int64: Alertmanager rejects 0 as "missing" at
# config load. 1 is a harmless fake that loads cleanly but yields only a
# Telegram send error in the log (graceful degrade) until a real id is set.
: "${ALERT_TELEGRAM_CHAT_ID:=1}"
: "${GRAFANA_PORT:=3001}"

# Placeholders in the template are ${ALERT_TELEGRAM_BOT_TOKEN} etc.; replace the
# literal tokens. Use a non-/ delimiter because the token contains no slash but
# values are kept simple regardless.
sed \
  -e "s|\${ALERT_TELEGRAM_BOT_TOKEN}|${ALERT_TELEGRAM_BOT_TOKEN}|g" \
  -e "s|\${ALERT_TELEGRAM_CHAT_ID}|${ALERT_TELEGRAM_CHAT_ID}|g" \
  -e "s|\${GRAFANA_PORT}|${GRAFANA_PORT}|g" \
  /etc/alertmanager/alertmanager.tmpl.yml \
  > /tmp/alertmanager.yml

exec /bin/alertmanager \
  --config.file=/tmp/alertmanager.yml \
  --storage.path=/alertmanager \
  "$@"
