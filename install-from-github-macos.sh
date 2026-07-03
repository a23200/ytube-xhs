#!/usr/bin/env bash
set -euo pipefail

REPO="${YTXHS_REPO:-a23200/ytube-xhs}"
REF="${YTXHS_REF:-main}"
APP_DIR="${YTXHS_APP_DIR:-/opt/ytube-xhs}"
HOST="${YTXHS_HOST:-0.0.0.0}"
PORT="${YTXHS_PORT:-8012}"
SERVICE_USER="${YTXHS_SERVICE_USER:-${SUDO_USER:-$(id -un)}}"
LAUNCHD_MODE="${YTXHS_LAUNCHD_MODE:-daemon}"
SKIP_BREW="${YTXHS_SKIP_BREW:-0}"
SKIP_HOMEBREW_INSTALL="${YTXHS_SKIP_HOMEBREW_INSTALL:-0}"
NO_WHISPER="${YTXHS_NO_WHISPER:-0}"
WITH_PADDLEOCR="${YTXHS_WITH_PADDLEOCR:-0}"
KEEP_DOWNLOAD="${YTXHS_KEEP_DOWNLOAD:-0}"

usage() {
  cat <<'EOF'
Usage:
  bash install-from-github-macos.sh [options]

One-command installer for ytube-xhs on a standalone Mac mini. It downloads the
project body from GitHub, then runs deploy/macos/install_macos.sh locally.

Options:
  --repo OWNER/REPO       GitHub repository. Default: a23200/ytube-xhs
  --ref REF              Branch, tag, or commit. Default: main
  --app-dir PATH         Install path. Default: /opt/ytube-xhs
  --host HOST            Uvicorn bind host. Default: 0.0.0.0
  --port PORT            Uvicorn port. Default: 8012
  --service-user USER    macOS user that runs the service. Default: current sudo caller
  --launchd-mode MODE    daemon | agent | none. Default: daemon
  --skip-brew            Do not install Homebrew packages
  --skip-homebrew-install Do not try to install Homebrew when it is missing
  --no-whisper           Do not install faster-whisper
  --with-paddleocr       Install PaddleOCR Python package
  --keep-download        Keep downloaded source directory under /tmp
  -h, --help             Show help

Remote one-liner example:
  YTXHS_REF=macmini-v20260704.3 bash -c "$(curl -fsSL https://raw.githubusercontent.com/a23200/ytube-xhs/macmini-v20260704.3/install-from-github-macos.sh)"

Private repository fallback:
  If this repository is private again later, set GH_TOKEN or authenticate gh CLI
  before running this script from a local copy.

After installation, edit:
  /opt/ytube-xhs/.env
EOF
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
    --skip-brew)
      SKIP_BREW=1
      shift
      ;;
    --skip-homebrew-install)
      SKIP_HOMEBREW_INSTALL=1
      shift
      ;;
    --no-whisper)
      NO_WHISPER=1
      shift
      ;;
    --with-paddleocr)
      WITH_PADDLEOCR=1
      shift
      ;;
    --keep-download)
      KEEP_DOWNLOAD=1
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

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required. Install Xcode Command Line Tools or Python 3 first." >&2
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required." >&2
  exit 1
fi

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ytube-xhs-install.XXXXXX")"
ARCHIVE="$WORK_DIR/source.tar.gz"
SOURCE_PARENT="$WORK_DIR/source"
mkdir -p "$SOURCE_PARENT"

cleanup() {
  if [ "$KEEP_DOWNLOAD" != "1" ]; then
    rm -rf "$WORK_DIR"
  else
    echo "Kept downloaded files at: $WORK_DIR"
  fi
}
trap cleanup EXIT

download_with_curl() {
  local url="$1"
  local output="$2"
  if [ -n "${GH_TOKEN:-}" ]; then
    curl -fL --retry 3 --connect-timeout 20 \
      -H "Authorization: Bearer ${GH_TOKEN}" \
      -H "X-GitHub-Api-Version: 2022-11-28" \
      "$url" -o "$output"
  else
    curl -fL --retry 3 --connect-timeout 20 "$url" -o "$output"
  fi
}

download_source() {
  local archive_url="https://api.github.com/repos/${REPO}/tarball/${REF}"
  echo "Downloading ${REPO}@${REF}"

  if [ -n "${GH_TOKEN:-}" ]; then
    download_with_curl "$archive_url" "$ARCHIVE"
    return
  fi

  if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    gh api \
      -H "Accept: application/vnd.github+json" \
      -H "X-GitHub-Api-Version: 2022-11-28" \
      "repos/${REPO}/tarball/${REF}" > "$ARCHIVE"
    return
  fi

  echo "No GH_TOKEN or authenticated gh CLI detected; trying unauthenticated download."
  echo "This only works for public repositories."
  download_with_curl "$archive_url" "$ARCHIVE"
}

download_source

tar -xzf "$ARCHIVE" -C "$SOURCE_PARENT"
SOURCE_DIR="$(find "$SOURCE_PARENT" -mindepth 1 -maxdepth 1 -type d | head -1)"
if [ -z "$SOURCE_DIR" ] || [ ! -x "$SOURCE_DIR/deploy/macos/install_macos.sh" ]; then
  echo "Downloaded source does not contain deploy/macos/install_macos.sh" >&2
  exit 1
fi

INSTALL_ARGS=(
  --app-dir "$APP_DIR"
  --host "$HOST"
  --port "$PORT"
  --service-user "$SERVICE_USER"
  --launchd-mode "$LAUNCHD_MODE"
)

if [ "$SKIP_BREW" = "1" ]; then
  INSTALL_ARGS+=(--skip-brew)
fi
if [ "$SKIP_HOMEBREW_INSTALL" = "1" ]; then
  INSTALL_ARGS+=(--skip-homebrew-install)
fi
if [ "$NO_WHISPER" = "1" ]; then
  INSTALL_ARGS+=(--no-whisper)
fi
if [ "$WITH_PADDLEOCR" = "1" ]; then
  INSTALL_ARGS+=(--with-paddleocr)
fi

echo "Running local installer from downloaded source..."
if [ "$LAUNCHD_MODE" = "daemon" ] && [ "$(id -u)" -ne 0 ]; then
  sudo "$SOURCE_DIR/deploy/macos/install_macos.sh" "${INSTALL_ARGS[@]}"
else
  "$SOURCE_DIR/deploy/macos/install_macos.sh" "${INSTALL_ARGS[@]}"
fi

echo
echo "ytube-xhs installed from GitHub."
echo "App dir:  $APP_DIR"
echo "Open:     http://<mac-mini-ip>:${PORT}"
echo "Config:   $APP_DIR/.env"
echo "Health:   $APP_DIR/deploy/macos/manage.sh health"
