#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="${YQ_QA_ROOT:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"

OPENVIKING_PORT="${YQ_OPENVIKING_PORT:-20100}"
GATEWAY_PORT="${YQ_VIKINGBOT_GATEWAY_PORT:-21100}"
MANAGER_PORT="${YQ_RAG_MANAGER_PORT:-18081}"
BACKEND_PORT="${YQ_QA_BACKEND_PORT:-18082}"
WORKER_BASE_PORT="${YQ_RAG_WORKER_BASE_PORT:-18100}"
WORKER_PORT_COUNT="${YQ_RAG_WORKER_PORT_COUNT:-100}"
CLEAN_WORKER_PORTS="${YQ_CLEAN_WORKER_PORTS:-1}"
DISABLE_UNITS="${YQ_DISABLE_UNITS:-1}"

SERVICES=(
  yq-qa-backend.service
  yq-rag-manager.service
  yq-vikingbot-gateway.service
  yq-openviking.service
)

die() {
  echo "[yq-qa:stop] ERROR: $*" >&2
  exit 1
}

info() {
  echo "[yq-qa:stop] $*"
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    die "please run with sudo/root; systemd and port cleanup require root"
  fi
}

cleanup_port() {
  local port="$1"
  local pids=""
  if command -v lsof >/dev/null 2>&1; then
    pids="$(lsof -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"
  elif command -v fuser >/dev/null 2>&1; then
    pids="$(fuser "${port}/tcp" 2>/dev/null | tr ' ' '\n' || true)"
  elif command -v ss >/dev/null 2>&1; then
    pids="$(ss -lntp "sport = :${port}" 2>/dev/null | awk -F'pid=' 'NR>1 { for (i=2; i<=NF; i++) { split($i,a,","); print a[1] } }' || true)"
  fi
  [[ -n "${pids//[[:space:]]/}" ]] || return 0

  while read -r pid; do
    [[ -n "${pid}" ]] || continue
    [[ "${pid}" =~ ^[0-9]+$ ]] || continue
    info "terminating pid ${pid} on port ${port}"
    kill -TERM "${pid}" 2>/dev/null || true
  done <<< "${pids}"
  sleep 1
  while read -r pid; do
    [[ -n "${pid}" ]] || continue
    [[ "${pid}" =~ ^[0-9]+$ ]] || continue
    if kill -0 "${pid}" 2>/dev/null; then
      info "force killing pid ${pid} on port ${port}"
      kill -KILL "${pid}" 2>/dev/null || true
    fi
  done <<< "${pids}"
}

cleanup_ports() {
  for port in "${OPENVIKING_PORT}" "${GATEWAY_PORT}" "${MANAGER_PORT}" "${BACKEND_PORT}"; do
    cleanup_port "${port}"
  done
  if [[ "${CLEAN_WORKER_PORTS}" == "1" ]]; then
    local start="${WORKER_BASE_PORT}"
    local end=$((WORKER_BASE_PORT + WORKER_PORT_COUNT - 1))
    info "cleaning worker port range ${start}-${end}"
    for ((port=start; port<=end; port++)); do
      cleanup_port "${port}"
    done
  fi
}

main() {
  require_root
  info "APP_ROOT=${APP_ROOT}"

  for service in "${SERVICES[@]}"; do
    if [[ "${DISABLE_UNITS}" == "1" ]]; then
      info "disable --now ${service}"
      systemctl disable --now "${service}" >/dev/null 2>&1 || true
    else
      info "stop ${service}"
      systemctl stop "${service}" >/dev/null 2>&1 || true
    fi
    systemctl reset-failed "${service}" >/dev/null 2>&1 || true
  done

  cleanup_ports
  info "services stopped"
}

main "$@"
