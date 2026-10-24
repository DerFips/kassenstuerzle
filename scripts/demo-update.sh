#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
# Optionale Einstellungsdatei (nicht eingecheckt): DEMO_PORT, DEMO_PASSWORD,
# DEMO_HTTPS, DEMO_RESET_HOUR, DEMO_RESET_MINUTE. Ohne sie gingen Werte aus der
# Shell beim Neuerstellen des Containers verloren. Mit --env-file liest Compose
# die .env der echten Installation nicht mehr zur Interpolation.
ENV_ARGS=()
if [ -f demo.env ]; then
  ENV_ARGS=(--env-file demo.env)
fi
# ${ENV_ARGS[@]+...}: ein leeres Array ist unter `set -u` sonst ein Fehler.
docker compose ${ENV_ARGS[@]+"${ENV_ARGS[@]}"} -f docker-compose.demo.yml up -d --build
