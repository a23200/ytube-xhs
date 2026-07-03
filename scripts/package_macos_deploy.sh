#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
DIST_DIR="$ROOT_DIR/dist"
STAMP="$(date +%Y%m%d-%H%M%S)"
PACKAGE_NAME="ytube-xhs-macmini-${STAMP}"
BUILD_DIR="$DIST_DIR/$PACKAGE_NAME"
ARCHIVE="$DIST_DIR/${PACKAGE_NAME}.tar.gz"
SHA_FILE="$DIST_DIR/${PACKAGE_NAME}.sha256"

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
shasum -a 256 "$ARCHIVE" > "$SHA_FILE"

rm -rf "$BUILD_DIR"

echo "Archive: $ARCHIVE"
echo "SHA256:  $SHA_FILE"
