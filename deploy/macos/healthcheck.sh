#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${YTXHS_BASE_URL:-http://127.0.0.1:${YTXHS_PORT:-8012}}"
CHECK_LLM=0
CHECK_IMAGE=0

usage() {
  cat <<'EOF'
Usage: deploy/macos/healthcheck.sh [--base-url URL] [--llm] [--image]

Checks:
  - /api/health
  - /api/diagnostics core readiness
  - optional /api/llm/self-test when --llm is passed
  - optional /api/image/self-test when --image is passed

No jq dependency is required.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --base-url)
      BASE_URL="${2:?missing URL}"
      shift 2
      ;;
    --llm)
      CHECK_LLM=1
      shift
      ;;
    --image)
      CHECK_IMAGE=1
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

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

curl_json() {
  local path="$1"
  local output="$2"
  curl -fsS --max-time 30 "${BASE_URL}${path}" -o "$output"
}

python_json_check() {
  local file="$1"
  local code="$2"
  python3 - "$file" "$code" <<'PY'
import json
import sys

path, mode = sys.argv[1], sys.argv[2]
with open(path, "r", encoding="utf-8") as fh:
    data = json.load(fh)

if mode == "health":
    if data.get("ok") is not True:
        raise SystemExit(f"health check failed: {data}")
elif mode == "diagnostics":
    ready = data.get("ready_for", {})
    required = ["ingest", "subtitle_transcript", "frame_extraction"]
    missing = [name for name in required if not ready.get(name)]
    if missing:
        raise SystemExit(f"diagnostics missing required readiness: {missing}")
elif mode == "llm":
    if data.get("ok") is not True:
        raise SystemExit(f"LLM self-test failed: {data}")
elif mode == "image":
    if data.get("ok") is not True:
        raise SystemExit(f"image self-test failed: {data}")
else:
    raise SystemExit(f"unknown mode: {mode}")
PY
}

echo "Checking ${BASE_URL}"
curl_json "/api/health" "$tmpdir/health.json"
python_json_check "$tmpdir/health.json" health
echo "OK /api/health"

curl_json "/api/diagnostics" "$tmpdir/diagnostics.json"
python_json_check "$tmpdir/diagnostics.json" diagnostics
echo "OK /api/diagnostics core readiness"

if [ "$CHECK_LLM" -eq 1 ]; then
  curl_json "/api/llm/self-test" "$tmpdir/llm.json"
  python_json_check "$tmpdir/llm.json" llm
  echo "OK /api/llm/self-test"
fi

if [ "$CHECK_IMAGE" -eq 1 ]; then
  curl_json "/api/image/self-test" "$tmpdir/image.json"
  python_json_check "$tmpdir/image.json" image
  echo "OK /api/image/self-test"
fi

echo "healthcheck passed"
