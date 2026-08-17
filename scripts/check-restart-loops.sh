#!/bin/sh
set -eu

PROJECT="${COMPOSE_PROJECT_NAME:-lithium_bom}"
THRESHOLD="${RESTART_ALERT_THRESHOLD:-5}"
INTERVAL="${CHECK_INTERVAL_SECONDS:-60}"
ALERT_FILE="${RESTART_ALERT_FILE:-/alerts/restart-loops.log}"
SERVICES="web caddy db backup"
STATE_DIR="/tmp/restart-monitor"

mkdir -p "$(dirname "$ALERT_FILE")" "$STATE_DIR"

log_alert() {
  msg="$1"
  printf '%s\n' "$msg"
  printf '%s\n' "$msg" >> "$ALERT_FILE"
}

while true; do
  for svc in $SERVICES; do
    container_id=$(docker ps -aq \
      -f "label=com.docker.compose.project=${PROJECT}" \
      -f "label=com.docker.compose.service=${svc}" \
      | head -n 1)

    if [ -z "$container_id" ]; then
      continue
    fi

    count=$(docker inspect --format='{{.RestartCount}}' "$container_id" 2>/dev/null || echo 0)
    last=$(cat "${STATE_DIR}/${svc}" 2>/dev/null || echo 0)

    if [ "$count" -ge "$THRESHOLD" ] && [ "$count" -gt "$last" ]; then
      log_alert "$(date -u '+%Y-%m-%dT%H:%M:%SZ') ALERT service=${svc} container=${container_id} restart_count=${count} threshold=${THRESHOLD}"
    fi

    printf '%s' "$count" > "${STATE_DIR}/${svc}"
  done

  sleep "$INTERVAL"
done
