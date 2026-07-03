#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(cd "$SCRIPT_DIR/../.." && pwd -P)"

APP_DIR="/opt/ytube-xhs"
HOST="0.0.0.0"
PORT="8012"
SERVICE_USER="${SUDO_USER:-$(id -un)}"
SERVICE_GROUP=""
LAUNCHD_MODE="daemon"
INSTALL_WHISPER=1
INSTALL_PADDLEOCR=0
INSTALL_BREW_PACKAGES=1

usage() {
  cat <<'EOF'
Usage: sudo deploy/macos/install_macos.sh [options]

Options:
  --app-dir PATH          Install path. Default: /opt/ytube-xhs
  --host HOST             Uvicorn bind host. Default: 0.0.0.0
  --port PORT             Uvicorn port. Default: 8012
  --service-user USER     macOS user that runs the service. Default: sudo caller
  --launchd-mode MODE     daemon | agent | none. Default: daemon
  --no-whisper            Do not install faster-whisper
  --with-paddleocr        Install PaddleOCR Python package; PaddlePaddle runtime may still be required
  --skip-brew             Do not install Homebrew packages
  --skip-homebrew-install Accepted for bootstrap compatibility; Homebrew install happens before sudo
  -h, --help              Show help

Recommended production command:
  sudo deploy/macos/install_macos.sh --app-dir /opt/ytube-xhs --port 8012 --service-user "$USER"

Notes:
  - The package .env is not copied. A fresh .env is created from deploy/macos/env.production.example if missing.
  - Fill API keys in /opt/ytube-xhs/.env on the target Mac mini, then restart the service.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
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
    --no-optional|--no-whisper)
      INSTALL_WHISPER=0
      shift
      ;;
    --with-paddleocr)
      INSTALL_PADDLEOCR=1
      shift
      ;;
    --skip-brew)
      INSTALL_BREW_PACKAGES=0
      shift
      ;;
    --skip-homebrew-install)
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  echo "Service user does not exist: $SERVICE_USER" >&2
  exit 1
fi
SERVICE_GROUP="$(id -gn "$SERVICE_USER")"

case "$LAUNCHD_MODE" in
  daemon|agent|none) ;;
  *)
    echo "--launchd-mode must be daemon, agent, or none" >&2
    exit 2
    ;;
esac

if [ "$LAUNCHD_MODE" = "daemon" ] && [ "$(id -u)" -ne 0 ]; then
  echo "LaunchDaemon install requires sudo/root. Re-run with sudo, or use --launchd-mode agent/none." >&2
  exit 1
fi

run_as_service_user() {
  if [ "$(id -u)" -eq 0 ] && [ "$SERVICE_USER" != "root" ]; then
    sudo -u "$SERVICE_USER" "$@"
  else
    "$@"
  fi
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
  if [ "$SERVICE_USER" != "root" ]; then
    sudo -u "$SERVICE_USER" /bin/zsh -lc 'command -v brew' 2>/dev/null || true
  fi
}

install_brew_package() {
  local package="$1"
  local brew_bin="$2"
  if [ -z "$brew_bin" ]; then
    return 0
  fi
  if run_as_service_user "$brew_bin" list "$package" >/dev/null 2>&1; then
    echo "Homebrew package already installed: $package"
  else
    echo "Installing Homebrew package: $package"
    run_as_service_user "$brew_bin" install "$package"
  fi
}

echo "Installing ytube-xhs"
echo "  source:       $SOURCE_DIR"
echo "  app dir:      $APP_DIR"
echo "  service user: $SERVICE_USER:$SERVICE_GROUP"
echo "  bind:         $HOST:$PORT"
echo "  launchd:      $LAUNCHD_MODE"

mkdir -p "$APP_DIR"

if [ "$SOURCE_DIR" != "$(cd "$APP_DIR" 2>/dev/null && pwd -P || echo "$APP_DIR")" ]; then
  echo "Copying application files..."
  rsync -a --delete \
    --exclude '.git/' \
    --exclude '.venv/' \
    --exclude '.env' \
    --exclude 'runtime/' \
    --exclude 'dist/' \
    --exclude '.pytest_cache/' \
    --exclude '.ruff_cache/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude '.DS_Store' \
    "$SOURCE_DIR/" "$APP_DIR/"
fi

mkdir -p "$APP_DIR/runtime/logs" "$APP_DIR/secrets"
chown -R "$SERVICE_USER:$SERVICE_GROUP" "$APP_DIR"
chmod +x "$APP_DIR/start.sh" "$APP_DIR/deploy/macos/"*.sh "$APP_DIR/scripts/"*.sh 2>/dev/null || true

if [ "$INSTALL_BREW_PACKAGES" -eq 1 ]; then
  BREW_BIN="$(brew_path)"
  if [ -z "$BREW_BIN" ]; then
    echo "Homebrew not found. Install Homebrew first, or re-run the GitHub bootstrap installer without --skip-homebrew-install." >&2
  else
    install_brew_package ffmpeg "$BREW_BIN"
    install_brew_package tesseract "$BREW_BIN"
    install_brew_package tesseract-lang "$BREW_BIN" || true
  fi
fi

cd "$APP_DIR"

if [ ! -d ".venv" ]; then
  echo "Creating Python virtual environment..."
  run_as_service_user python3 -m venv .venv
fi

echo "Installing Python dependencies..."
run_as_service_user .venv/bin/python -m pip install --upgrade pip
run_as_service_user .venv/bin/python -m pip install -r requirements.txt
if [ "$INSTALL_WHISPER" -eq 1 ]; then
  run_as_service_user .venv/bin/python -m pip install 'faster-whisper>=1.1,<2.0'
fi
if [ "$INSTALL_PADDLEOCR" -eq 1 ]; then
  run_as_service_user .venv/bin/python -m pip install 'paddleocr>=2.7,<3.0'
  echo "PaddleOCR installed. If OCR provider errors, install a compatible PaddlePaddle runtime for this Mac."
fi

if [ ! -f ".env" ]; then
  echo "Creating .env from production template..."
  python3 - "$APP_DIR" deploy/macos/env.production.example .env <<'PY'
import sys

app_dir, template_path, output_path = sys.argv[1:4]
with open(template_path, "r", encoding="utf-8") as fh:
    template = fh.read()
with open(output_path, "w", encoding="utf-8") as fh:
    fh.write(template.replace("__APP_DIR__", app_dir))
PY
  chown "$SERVICE_USER:$SERVICE_GROUP" .env
  chmod 600 .env
  echo "Created $APP_DIR/.env. Fill API keys before full business validation."
else
  echo "Keeping existing .env"
fi

echo "Running default doctor..."
run_as_service_user .venv/bin/python scripts/doctor.py

if [ "$LAUNCHD_MODE" != "none" ]; then
  TEMPLATE="$APP_DIR/deploy/macos/com.ytube-xhs.service.plist.template"
  PLIST_TMP="$APP_DIR/runtime/com.ytube-xhs.service.plist"
  if [ "$LAUNCHD_MODE" = "daemon" ]; then
    USER_BLOCK="  <key>UserName</key>
  <string>${SERVICE_USER}</string>
  <key>GroupName</key>
  <string>${SERVICE_GROUP}</string>"
    PLIST_DEST="/Library/LaunchDaemons/com.ytube-xhs.service.plist"
    BOOTSTRAP_DOMAIN="system"
  else
    USER_BLOCK=""
    USER_HOME="$(dscl . -read "/Users/${SERVICE_USER}" NFSHomeDirectory | awk '{print $2}')"
    PLIST_DEST="${USER_HOME}/Library/LaunchAgents/com.ytube-xhs.service.plist"
    BOOTSTRAP_DOMAIN="gui/$(id -u "$SERVICE_USER")"
    mkdir -p "$(dirname "$PLIST_DEST")"
  fi

  python3 - "$TEMPLATE" "$PLIST_TMP" "$APP_DIR" "$HOST" "$PORT" "$USER_BLOCK" <<'PY'
import sys

template_path, output_path, app_dir, host, port, user_block = sys.argv[1:7]
text = open(template_path, "r", encoding="utf-8").read()
text = text.replace("__APP_DIR__", app_dir)
text = text.replace("__HOST__", host)
text = text.replace("__PORT__", port)
text = text.replace("__USER_BLOCK__", user_block)
open(output_path, "w", encoding="utf-8").write(text)
PY

  if [ "$LAUNCHD_MODE" = "daemon" ]; then
    cp "$PLIST_TMP" "$PLIST_DEST"
    chown root:wheel "$PLIST_DEST"
    chmod 644 "$PLIST_DEST"
    launchctl bootout system/com.ytube-xhs.service >/dev/null 2>&1 || true
    launchctl bootstrap system "$PLIST_DEST"
    launchctl enable system/com.ytube-xhs.service
    launchctl kickstart -k system/com.ytube-xhs.service
  else
    cp "$PLIST_TMP" "$PLIST_DEST"
    chown "$SERVICE_USER:$SERVICE_GROUP" "$PLIST_DEST"
    chmod 644 "$PLIST_DEST"
    echo "LaunchAgent plist written to $PLIST_DEST"
    echo "If the user GUI session is active, load with:"
    echo "  launchctl bootstrap ${BOOTSTRAP_DOMAIN} ${PLIST_DEST}"
  fi
fi

echo
echo "Install complete."
echo "Open:    http://<mac-mini-ip>:${PORT}"
echo "Start:   $APP_DIR/start.sh"
echo "Restart: $APP_DIR/start.sh restart"
echo "Manage:  $APP_DIR/deploy/macos/manage.sh status"
echo "Logs:    $APP_DIR/deploy/macos/manage.sh logs"
echo "Health:  YTXHS_PORT=${PORT} $APP_DIR/deploy/macos/healthcheck.sh --llm"
echo
echo "Next required step: edit $APP_DIR/.env with real LLM/image keys, then restart:"
echo "  sudo $APP_DIR/deploy/macos/manage.sh restart"
