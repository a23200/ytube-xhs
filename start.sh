#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${YTXHS_APP_DIR:-/opt/ytube-xhs}"
PORT="${YTXHS_PORT:-8012}"
HOST="${YTXHS_HOST:-0.0.0.0}"
BASE_URL="${YTXHS_BASE_URL:-http://127.0.0.1:${PORT}}"
MANAGE="$APP_DIR/deploy/macos/manage.sh"
HEALTHCHECK="$APP_DIR/deploy/macos/healthcheck.sh"
WAIT_SECONDS="${YTXHS_WAIT_SECONDS:-45}"

usage() {
  cat <<EOF
Usage: ./start.sh [command]

Commands:
  start     Check service; start it if unhealthy. Default.
  restart   Force restart service and wait until healthy.
  status    Print service status and access URLs.
  health    Run HTTP healthcheck only.
  logs      Tail service logs.

Environment:
  YTXHS_APP_DIR=$APP_DIR
  YTXHS_PORT=$PORT
EOF
}

cmd="${1:-start}"
if [ "$cmd" = "-h" ] || [ "$cmd" = "--help" ]; then
  usage
  exit 0
fi

if [ ! -x "$MANAGE" ]; then
  echo "Cannot find manage script: $MANAGE" >&2
  echo "Set YTXHS_APP_DIR or reinstall ytube-xhs." >&2
  exit 1
fi

local_ips() {
  {
    ipconfig getifaddr en0 2>/dev/null || true
    ipconfig getifaddr en1 2>/dev/null || true
    ifconfig 2>/dev/null | awk '/inet / && $2 != "127.0.0.1" {print $2}'
  } | awk 'NF && !seen[$0]++'
}

print_urls() {
  echo
  echo "访问地址："
  echo "  本机: http://127.0.0.1:${PORT}"
  local ip
  while IFS= read -r ip; do
    [ -n "$ip" ] && echo "  局域网: http://${ip}:${PORT}"
  done < <(local_ips)
  echo
}

health_ok() {
  curl -fsS --max-time 5 "${BASE_URL}/api/health" >/dev/null 2>&1
}

wait_for_health() {
  local waited=0
  while [ "$waited" -lt "$WAIT_SECONDS" ]; do
    if health_ok; then
      echo "服务健康检查通过。"
      print_urls
      return 0
    fi
    sleep 2
    waited=$((waited + 2))
  done
  echo "服务在 ${WAIT_SECONDS}s 内未恢复，请查看日志：" >&2
  echo "  sudo $MANAGE status" >&2
  echo "  $MANAGE logs" >&2
  return 1
}

case "$cmd" in
  start)
    if health_ok; then
      echo "服务已正常运行。"
      print_urls
      exit 0
    fi
    echo "服务未通过健康检查，正在启动/拉起 launchd 服务..."
    sudo "$MANAGE" start
    wait_for_health
    ;;
  restart)
    echo "正在重启服务..."
    sudo "$MANAGE" restart
    wait_for_health
    ;;
  status)
    sudo "$MANAGE" status || true
    if health_ok; then
      echo "HTTP 健康检查：正常"
    else
      echo "HTTP 健康检查：失败"
    fi
    print_urls
    ;;
  health)
    "$HEALTHCHECK" --base-url "$BASE_URL"
    print_urls
    ;;
  logs)
    "$MANAGE" logs
    ;;
  -h|--help)
    usage
    ;;
  *)
    echo "Unknown command: $cmd" >&2
    usage >&2
    exit 2
    ;;
esac
