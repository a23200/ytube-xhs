#!/usr/bin/env bash
set -euo pipefail

REPO="${YTXHS_REPO:-a23200/ytube-xhs}"
REF="${YTXHS_REF:-main}"
APP_DIR="${YTXHS_APP_DIR:-/opt/ytube-xhs}"
HOST="${YTXHS_HOST:-0.0.0.0}"
PORT="${YTXHS_PORT:-8012}"
SERVICE_USER="${YTXHS_SERVICE_USER:-${SUDO_USER:-$(id -un)}}"
LAUNCHD_MODE="${YTXHS_LAUNCHD_MODE:-daemon}"
POST_ACTION="${YTXHS_POST_UPDATE_ACTION:-start}"
KEEP_INSTALLER="${YTXHS_KEEP_UPDATE_INSTALLER:-0}"
PASSTHROUGH=()

usage() {
  cat <<'USAGE'
Usage:
  bash update-macos.sh [options]

Stable updater for standalone Mac mini deployments. It downloads the current
GitHub installer from the selected repo/ref, installs into /opt/ytube-xhs by
default, preserves /opt/ytube-xhs/.env and runtime data, then health-checks the
service and prints access URLs.

Fixed remote command:
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/a23200/ytube-xhs/main/update-macos.sh)"

Options:
  --repo OWNER/REPO       GitHub repository. Default: a23200/ytube-xhs
  --ref REF              Branch, tag, or commit to deploy. Default: main
  --app-dir PATH         Install path. Default: /opt/ytube-xhs
  --host HOST            Uvicorn bind host. Default: 0.0.0.0
  --port PORT            Uvicorn port. Default: 8012
  --service-user USER    macOS user that runs the service. Default: current sudo caller
  --launchd-mode MODE    daemon | agent | none. Default: daemon
  --start                After update, ensure service is healthy. Default.
  --restart              After update, force restart and wait until healthy
  --open                 After update, ensure service is healthy and open browser
  --no-start             Do not run post-update start/health action
  --skip-brew            Forwarded to installer
  --skip-homebrew-install Forwarded to installer
  --no-whisper           Forwarded to installer
  --with-paddleocr       Forwarded to installer
  --no-bootcheck         Forwarded to installer
  --keep-download        Forwarded to installer
  -h, --help             Show help

Examples:
  # Always deploy latest tested main from the fixed endpoint:
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/a23200/ytube-xhs/main/update-macos.sh)"

  # Deploy a frozen tag through the same fixed updater:
  YTXHS_REF=macmini-v20260707.5 bash -c "$(curl -fsSL https://raw.githubusercontent.com/a23200/ytube-xhs/main/update-macos.sh)"
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --repo)
      REPO="${2:?missing repo}"
      shift 2
      ;;
    --ref)
      REF="${2:?missing ref}"
      shift 2
      ;;
    --app-dir)
      APP_DIR="${2:?missing path}"
      shift 2
      ;;
    --host)
      HOST="${2:?missing host}"
      shift 2
      ;;
    --port)
      PORT="${2:?missing port}"
      shift 2
      ;;
    --service-user)
      SERVICE_USER="${2:?missing user}"
      shift 2
      ;;
    --launchd-mode)
      LAUNCHD_MODE="${2:?missing mode}"
      shift 2
      ;;
    --start)
      POST_ACTION="start"
      shift
      ;;
    --restart)
      POST_ACTION="restart"
      shift
      ;;
    --open)
      POST_ACTION="open"
      shift
      ;;
    --no-start|--no-healthcheck)
      POST_ACTION="none"
      shift
      ;;
    --skip-brew|--skip-homebrew-install|--no-whisper|--with-paddleocr|--no-bootcheck|--keep-download)
      PASSTHROUGH+=("$1")
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      PASSTHROUGH+=("$1")
      shift
      ;;
  esac
done

case "$POST_ACTION" in
  start|restart|open|none) ;;
  *)
    echo "YTXHS_POST_UPDATE_ACTION must be start, restart, open, or none." >&2
    exit 2
    ;;
esac

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required." >&2
  exit 1
fi

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ytube-xhs-updater.XXXXXX")"
INSTALLER="$WORK_DIR/install-from-github-macos.sh"
cleanup() {
  if [ "$KEEP_INSTALLER" = "1" ]; then
    echo "Kept updater files at: $WORK_DIR"
  else
    rm -rf "$WORK_DIR"
  fi
}
trap cleanup EXIT

installer_url="https://raw.githubusercontent.com/${REPO}/${REF}/install-from-github-macos.sh"
echo "Updating ytube-xhs from ${REPO}@${REF}"
echo "Installer: ${installer_url}"

if [ -n "${GH_TOKEN:-}" ]; then
  curl -fsSL --retry 3 --connect-timeout 20 \
    -H "Authorization: Bearer ${GH_TOKEN}" \
    "$installer_url" -o "$INSTALLER"
else
  curl -fsSL --retry 3 --connect-timeout 20 "$installer_url" -o "$INSTALLER"
fi
chmod +x "$INSTALLER"

YTXHS_REPO="$REPO" \
YTXHS_REF="$REF" \
YTXHS_APP_DIR="$APP_DIR" \
YTXHS_HOST="$HOST" \
YTXHS_PORT="$PORT" \
YTXHS_SERVICE_USER="$SERVICE_USER" \
YTXHS_LAUNCHD_MODE="$LAUNCHD_MODE" \
bash "$INSTALLER" "${PASSTHROUGH[@]}"

if [ "$POST_ACTION" != "none" ]; then
  if [ ! -x "$APP_DIR/start.sh" ]; then
    echo "Post-update script is missing: $APP_DIR/start.sh" >&2
    exit 1
  fi
  echo
  echo "Post-update action: $POST_ACTION"
  YTXHS_APP_DIR="$APP_DIR" YTXHS_PORT="$PORT" "$APP_DIR/start.sh" "$POST_ACTION"
fi

echo
echo "Update complete."
echo "Fixed updater URL for future use:"
echo "  bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/${REPO}/main/update-macos.sh)\""
