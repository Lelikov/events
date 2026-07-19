#!/bin/sh
# Regenerate env-config.js from VITE_* env vars at container start so one image
# serves every environment. Only names starting with VITE_ are injected.
set -e
OUT=/usr/share/nginx/html/env-config.js
echo "window._env_ = {" > "$OUT"
printenv | grep '^VITE_' | while read -r line; do
  key=$(echo "$line" | cut -d '=' -f 1)
  value=$(echo "$line" | cut -d '=' -f 2-)
  escaped=$(printf '%s' "$value" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')
  echo "  \"$key\": \"$escaped\"," >> "$OUT"
done
echo "};" >> "$OUT"
