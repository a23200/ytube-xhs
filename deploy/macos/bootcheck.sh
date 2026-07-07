#!/usr/bin/env bash
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${YTXHS_APP_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
PORT="${YTXHS_PORT:-8012}"
BASE_URL="${YTXHS_BASE_URL:-http://127.0.0.1:${PORT}}"
MANAGE="$APP_DIR/deploy/macos/manage.sh"
HEALTHCHECK="$APP_DIR/deploy/macos/healthcheck.sh"
DISABLE_FILE="$APP_DIR/runtime/.ytube-xhs-disabled"
WAIT_SECONDS="${YTXHS_BOOTCHECK_WAIT_SECONDS:-180}"
INTERVAL_SECONDS="${YTXHS_BOOTCHECK_INTERVAL_SECONDS:-5}"
INSTALL_DEPS="${YTXHS_BOOTCHECK_INSTALL_DEPS:-0}"
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/local/sbin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

log() {
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

brew_path() {
  if command -v brew >/dev/null 2>&1; then
    command -v brew
    return 0
  fi
  if [ -x /opt/homebrew/bin/brew ]; then
    echo /opt/homebrew/bin/brew
    return 0
  fi
  if [ -x /usr/local/bin/brew ]; then
    echo /usr/local/bin/brew
    return 0
  fi
  return 1
}

install_missing_brew_package() {
  local package="$1"
  local brew_bin="$2"
  if "$brew_bin" list "$package" >/dev/null 2>&1; then
    return 0
  fi
  log "Installing missing dependency with Homebrew: $package"
  "$brew_bin" install "$package"
}

ensure_dependencies() {
  local missing=0
  if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
    log "Missing dependency: $APP_DIR/.venv/bin/python"
    missing=1
  fi
  if [ ! -x "$MANAGE" ]; then
    log "Missing manage script: $MANAGE"
    missing=1
  fi
  if [ ! -x "$HEALTHCHECK" ]; then
    log "Missing healthcheck script: $HEALTHCHECK"
    missing=1
  fi

  if ! command -v ffmpeg >/dev/null 2>&1; then
    log "Missing command dependency: ffmpeg"
    missing=1
  fi
  if ! command -v ffprobe >/dev/null 2>&1; then
    log "Missing command dependency: ffprobe"
    missing=1
  fi
  if ! command -v tesseract >/dev/null 2>&1; then
    log "Warning: tesseract is not available; OCR readiness may be reduced."
  fi

  if [ "$missing" -eq 1 ] && [ "$INSTALL_DEPS" = "1" ]; then
    local brew_bin
    if brew_bin="$(brew_path)"; then
      install_missing_brew_package ffmpeg "$brew_bin" || true
      install_missing_brew_package tesseract "$brew_bin" || true
      install_missing_brew_package tesseract-lang "$brew_bin" || true
      missing=0
      [ -x "$APP_DIR/.venv/bin/python" ] || missing=1
      command -v ffmpeg >/dev/null 2>&1 || missing=1
      command -v ffprobe >/dev/null 2>&1 || missing=1
    else
      log "Homebrew is not available; cannot auto-install missing command dependencies."
    fi
  fi

  return "$missing"
}

health_ok() {
  YTXHS_BASE_URL="$BASE_URL" "$HEALTHCHECK" >/dev/null 2>&1
}

print_urls() {
  log "Local URL: http://127.0.0.1:${PORT}"
  {
    ipconfig getifaddr en0 2>/dev/null || true
    ipconfig getifaddr en1 2>/dev/null || true
    ifconfig 2>/dev/null | awk '/inet / && $2 != "127.0.0.1" {print $2}'
  } | awk 'NF && !seen[$0]++' | while IFS= read -r ip; do
    [ -n "$ip" ] && log "LAN URL: http://${ip}:${PORT}"
  done
}

log "bootcheck start app_dir=$APP_DIR base_url=$BASE_URL wait=${WAIT_SECONDS}s"

if [ -f "$DISABLE_FILE" ]; then
  log "Bootcheck skipped because service is intentionally stopped: $DISABLE_FILE"
  exit 0
fi

if ! ensure_dependencies; then
  log "Dependency check failed. Re-run the fixed updater to repair installation: bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/a23200/ytube-xhs/main/update-macos.sh)\""
  exit 1
fi

if [ ! -d "$APP_DIR" ]; then
  log "App directory does not exist: $APP_DIR"
  exit 1
fi

cd "$APP_DIR" || exit 1

log "Ensuring launchd service is loaded and started..."
if ! "$MANAGE" start; then
  log "manage start failed; trying restart once..."
  "$MANAGE" restart || true
fi

waited=0
restart_attempted=0
while [ "$waited" -le "$WAIT_SECONDS" ]; do
  if health_ok; then
    log "Service healthcheck passed."
    "$MANAGE" recover >/dev/null 2>&1 || true
    print_urls
    exit 0
  fi

  if [ "$restart_attempted" -eq 0 ] && [ "$waited" -ge 30 ]; then
    log "Healthcheck still failing after ${waited}s; forcing service restart..."
    "$MANAGE" restart || true
    restart_attempted=1
  fi

  sleep "$INTERVAL_SECONDS"
  waited=$((waited + INTERVAL_SECONDS))
done

log "Service did not become healthy within ${WAIT_SECONDS}s. Recent logs:"
if [ -f "$APP_DIR/runtime/logs/uvicorn.err.log" ]; then
  tail -n 80 "$APP_DIR/runtime/logs/uvicorn.err.log" || true
fi
exit 1
