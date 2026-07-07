#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${YTXHS_APP_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
LABEL="${YTXHS_LAUNCHD_LABEL:-com.ytube-xhs.service}"
DOMAIN="${YTXHS_LAUNCHD_DOMAIN:-system}"
PORT="${YTXHS_PORT:-8012}"
BASE_URL="${YTXHS_BASE_URL:-http://127.0.0.1:${PORT}}"
DISABLE_FILE="$APP_DIR/runtime/.ytube-xhs-disabled"

usage() {
  cat <<EOF
Usage: deploy/macos/manage.sh <command>

Commands:
  status       Show launchd status
  start        Start/restart via launchd
  stop         Unload service and pause bootcheck auto-restart until start/restart
  restart      Restart via launchd
  logs         Tail service logs
  doctor       Run scripts/doctor.py --require-full
  health       Run HTTP healthcheck
  self-test    Run LLM and image configuration self-tests
  bootcheck    Run boot-time dependency, launchd, health, and self-heal check
  recover      Mark stale running projects failed after threshold

Environment:
  YTXHS_APP_DIR=${APP_DIR}
  YTXHS_PORT=${PORT}
  YTXHS_LAUNCHD_DOMAIN=${DOMAIN}
EOF
}

launchctl_cmd() {
  if [ "$DOMAIN" = "system" ] && [ "$(id -u)" -ne 0 ]; then
    sudo launchctl "$@"
  else
    launchctl "$@"
  fi
}

plist_path() {
  if [ "$DOMAIN" = "system" ]; then
    echo "/Library/LaunchDaemons/${LABEL}.plist"
  else
    echo "$HOME/Library/LaunchAgents/${LABEL}.plist"
  fi
}

ensure_loaded() {
  if launchctl_cmd print "${DOMAIN}/${LABEL}" >/dev/null 2>&1; then
    return 0
  fi
  local plist
  plist="$(plist_path)"
  if [ ! -f "$plist" ]; then
    echo "Service is not loaded and plist does not exist: $plist" >&2
    return 1
  fi
  launchctl_cmd bootstrap "$DOMAIN" "$plist" 2>/dev/null || true
  launchctl_cmd enable "${DOMAIN}/${LABEL}" 2>/dev/null || true
}

cd "$APP_DIR"

cmd="${1:-}"
case "$cmd" in
  status)
    launchctl_cmd print "${DOMAIN}/${LABEL}" || {
      echo "Service is not loaded in ${DOMAIN}/${LABEL}."
      exit 1
    }
    ;;
  start|restart)
    rm -f "$DISABLE_FILE"
    ensure_loaded
    launchctl_cmd kickstart -k "${DOMAIN}/${LABEL}"
    ;;
  stop)
    mkdir -p "$APP_DIR/runtime"
    date -u +"%Y-%m-%dT%H:%M:%SZ" > "$DISABLE_FILE"
    launchctl_cmd bootout "${DOMAIN}/${LABEL}" 2>/dev/null || launchctl_cmd kill TERM "${DOMAIN}/${LABEL}" 2>/dev/null || true
    ;;
  logs)
    mkdir -p runtime/logs
    touch runtime/logs/uvicorn.out.log runtime/logs/uvicorn.err.log
    tail -n 120 -f runtime/logs/uvicorn.out.log runtime/logs/uvicorn.err.log
    ;;
  doctor)
    .venv/bin/python scripts/doctor.py --require-full
    ;;
  health)
    YTXHS_BASE_URL="$BASE_URL" "$SCRIPT_DIR/healthcheck.sh"
    ;;
  self-test)
    YTXHS_BASE_URL="$BASE_URL" "$SCRIPT_DIR/healthcheck.sh" --llm --image
    ;;
  bootcheck)
    YTXHS_APP_DIR="$APP_DIR" YTXHS_PORT="$PORT" "$SCRIPT_DIR/bootcheck.sh"
    ;;
  recover)
    .venv/bin/python scripts/recover_stale_projects.py --older-than-seconds "${YTXHS_STALE_SECONDS:-3600}"
    ;;
  -h|--help|"")
    usage
    ;;
  *)
    echo "Unknown command: $cmd" >&2
    usage >&2
    exit 2
    ;;
esac
