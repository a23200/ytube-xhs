#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
DIST_DIR="$ROOT_DIR/dist"
STAMP="$(date +%Y%m%d-%H%M%S)"
PACKAGE_NAME="ytube-xhs-macmini-${STAMP}"
BUILD_DIR="$DIST_DIR/$PACKAGE_NAME"
ARCHIVE="$DIST_DIR/${PACKAGE_NAME}.tar.gz"
SHA_FILE="$DIST_DIR/${PACKAGE_NAME}.sha256"

verify_archive() {
  local list_file
  list_file="$(mktemp "${TMPDIR:-/tmp}/ytube-xhs-package-list.XXXXXX")"
  trap 'rm -f "$list_file"' RETURN
  tar -tzf "$ARCHIVE" > "$list_file"

  local forbidden='(^|/)(\.git|\.venv|runtime|dist|output|\.playwright-cli|\.pytest_cache|\.ruff_cache|__pycache__)(/|$)|(^|/)\.env$|(^|/)\.env\.(local|production|development|test)$|(^|/)(cookies?(\.txt)?|.*cookie.*\.(txt|json|sqlite)|.*secret.*|.*api[-_]?key.*)(/|$)|\.pyc$|\.DS_Store$'
  if grep -Eiq "$forbidden" "$list_file"; then
    echo "Deployment package contains forbidden local/secrets path(s):" >&2
    grep -Ei "$forbidden" "$list_file" >&2 || true
    exit 1
  fi
  for required in \
    "${PACKAGE_NAME}/app/main.py" \
    "${PACKAGE_NAME}/requirements.txt" \
    "${PACKAGE_NAME}/install-from-github-macos.sh" \
    "${PACKAGE_NAME}/update-macos.sh" \
    "${PACKAGE_NAME}/start.sh" \
    "${PACKAGE_NAME}/deploy/macos/install_macos.sh" \
    "${PACKAGE_NAME}/PACKAGE-MANIFEST.txt"; do
    if ! grep -Fqx "$required" "$list_file"; then
      echo "Deployment package is missing required path: $required" >&2
      exit 1
    fi
  done
  echo "Archive content verification passed."
}

mkdir -p "$DIST_DIR"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

echo "Building deployment package: $PACKAGE_NAME"

rsync -a \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude '.env' \
  --exclude 'runtime/' \
  --exclude 'dist/' \
  --exclude '.pytest_cache/' \
  --exclude '.ruff_cache/' \
  --exclude '.playwright-cli/' \
  --exclude 'output/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '.DS_Store' \
  "$ROOT_DIR/" "$BUILD_DIR/"

cat > "$BUILD_DIR/PACKAGE-MANIFEST.txt" <<EOF
Package: $PACKAGE_NAME
Built: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
Source: $ROOT_DIR

Excluded from package:
  - .env and secrets
  - .venv
  - runtime projects, videos, generated cards, logs
  - .git
  - local test/lint caches

Install on target Mac mini:
  tar -xzf ${PACKAGE_NAME}.tar.gz
  cd ${PACKAGE_NAME}
  sudo deploy/macos/install_macos.sh --app-dir /opt/ytube-xhs --port 8012 --service-user "\$USER"
EOF

chmod +x "$BUILD_DIR/deploy/macos/"*.sh "$BUILD_DIR/scripts/"*.sh 2>/dev/null || true

tar -C "$DIST_DIR" -czf "$ARCHIVE" "$PACKAGE_NAME"
verify_archive
(
  cd "$DIST_DIR"
  shasum -a 256 "$(basename "$ARCHIVE")" > "$(basename "$SHA_FILE")"
  shasum -a 256 -c "$(basename "$SHA_FILE")"
)

rm -rf "$BUILD_DIR"

echo "Archive: $ARCHIVE"
echo "SHA256:  $SHA_FILE"
