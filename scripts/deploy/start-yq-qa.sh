#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="${YQ_QA_ROOT:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"

find_uv_bin() {
  if [[ -n "${YQ_UV_BIN:-}" ]]; then
    printf '%s\n' "${YQ_UV_BIN}"
    return 0
  fi

  local found=""
  found="$(command -v uv 2>/dev/null || true)"
  if [[ -n "${found}" ]]; then
    printf '%s\n' "${found}"
    return 0
  fi

  local candidates=(
    "/usr/local/bin/uv"
    "/usr/bin/uv"
    "/bin/uv"
    "/opt/uv/uv"
  )

  if [[ -n "${HOME:-}" ]]; then
    candidates+=("${HOME}/.local/bin/uv" "${HOME}/.cargo/bin/uv")
  fi

  if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
    local sudo_home=""
    sudo_home="$(getent passwd "${SUDO_USER}" 2>/dev/null | cut -d: -f6 || true)"
    if [[ -n "${sudo_home}" ]]; then
      candidates+=("${sudo_home}/.local/bin/uv" "${sudo_home}/.cargo/bin/uv")
    fi
  fi

  local path
  for path in "${candidates[@]}"; do
    if [[ -x "${path}" ]]; then
      printf '%s\n' "${path}"
      return 0
    fi
  done
}

OV_CONF="${YQ_OV_CONF:-${APP_ROOT}/config/ov.conf}"
UV_BIN="$(find_uv_bin)"
PYTHON_BIN="${YQ_PYTHON_BIN:-$(command -v python3 || command -v python || true)}"

OPENVIKING_HOST="${YQ_OPENVIKING_HOST:-127.0.0.1}"
OPENVIKING_PORT="${YQ_OPENVIKING_PORT:-20100}"
GATEWAY_HOST="${YQ_VIKINGBOT_GATEWAY_HOST:-127.0.0.1}"
GATEWAY_PORT="${YQ_VIKINGBOT_GATEWAY_PORT:-21100}"
MANAGER_HOST="${YQ_RAG_MANAGER_HOST:-127.0.0.1}"
MANAGER_PORT="${YQ_RAG_MANAGER_PORT:-18081}"
BACKEND_HOST="${YQ_QA_BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${YQ_QA_BACKEND_PORT:-18082}"
WORKER_BASE_PORT="${YQ_RAG_WORKER_BASE_PORT:-18100}"
WORKER_PORT_COUNT="${YQ_RAG_WORKER_PORT_COUNT:-100}"

MANAGER_DB="${YQ_RAG_MANAGER_DB:-${APP_ROOT}/apps/rag-manager/data/rag-manager.sqlite3}"
MANAGER_LOGS_DIR="${YQ_RAG_MANAGER_LOGS_DIR:-${APP_ROOT}/apps/rag-manager/logs}"
BACKEND_DB="${YQ_QA_DB:-${APP_ROOT}/data/yq-qa.sqlite3}"
BACKEND_ENV_FILE="${YQ_QA_ENV_FILE:-${APP_ROOT}/.env.yq-qa}"

METHOD_ID="${YQ_OPENVIKING_METHOD_ID:-openviking-bot-default}"
METHOD_DISPLAY_NAME="${YQ_OPENVIKING_METHOD_DISPLAY_NAME:-OpenViking Bot}"
REGISTER_METHOD="${YQ_REGISTER_OPENVIKING_METHOD:-1}"
START_METHOD="${YQ_START_OPENVIKING_METHOD:-1}"

SYSTEMD_DIR="${YQ_SYSTEMD_DIR:-/etc/systemd/system}"
RESTART_SEC="${YQ_SERVICE_RESTART_SEC:-5}"
SYNC_MODE="${YQ_UV_SYNC:-auto}"
CLEAN_WORKER_PORTS="${YQ_CLEAN_WORKER_PORTS:-1}"

SERVICES=(
  yq-qa-backend.service
  yq-rag-manager.service
  yq-vikingbot-gateway.service
  yq-openviking.service
)

die() {
  echo "[yq-qa:start] ERROR: $*" >&2
  exit 1
}

info() {
  echo "[yq-qa:start] $*"
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    die "please run with sudo/root; systemd unit installation and port cleanup require root"
  fi
}

require_command() {
  local name="$1"
  local value="$2"
  [[ -n "${value}" ]] || die "${name} not found; install it or set YQ_${name^^}_BIN"
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
  local ports=("${OPENVIKING_PORT}" "${GATEWAY_PORT}" "${MANAGER_PORT}" "${BACKEND_PORT}")
  for port in "${ports[@]}"; do
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

sync_project() {
  local project_dir="$1"
  [[ -d "${project_dir}" ]] || die "project directory not found: ${project_dir}"
  if [[ "${SYNC_MODE}" == "0" ]]; then
    return 0
  fi
  if [[ "${SYNC_MODE}" == "1" || ! -d "${project_dir}/.venv" ]]; then
    info "uv sync: ${project_dir}"
    (cd "${project_dir}" && "${UV_BIN}" sync)
  fi
}

write_unit() {
  local unit_name="$1"
  local content="$2"
  local unit_path="${SYSTEMD_DIR}/${unit_name}"
  info "writing ${unit_path}"
  printf '%s\n' "${content}" > "${unit_path}"
}

install_units() {
  mkdir -p "$(dirname "${MANAGER_DB}")" "${MANAGER_LOGS_DIR}" "$(dirname "${BACKEND_DB}")" "${APP_ROOT}/logs/openviking-bot-worker"

  write_unit "yq-openviking.service" "[Unit]
Description=YQ OpenViking Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${APP_ROOT}/apps/rag-openviking-bot
Environment=OPENVIKING_CONFIG_FILE=${OV_CONF}
Environment=PYTHONUNBUFFERED=1
ExecStart=${UV_BIN} run openviking-server --config ${OV_CONF} --host ${OPENVIKING_HOST} --port ${OPENVIKING_PORT}
Restart=always
RestartSec=${RESTART_SEC}
KillMode=control-group
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target"

  write_unit "yq-vikingbot-gateway.service" "[Unit]
Description=YQ Vikingbot Gateway
After=network-online.target yq-openviking.service
Requires=yq-openviking.service

[Service]
Type=simple
WorkingDirectory=${APP_ROOT}/apps/rag-openviking-bot
Environment=OPENVIKING_CONFIG_FILE=${OV_CONF}
Environment=PYTHONUNBUFFERED=1
ExecStart=${UV_BIN} run vikingbot gateway --config ${OV_CONF} --port ${GATEWAY_PORT}
Restart=always
RestartSec=${RESTART_SEC}
KillMode=control-group
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target"

  write_unit "yq-rag-manager.service" "[Unit]
Description=YQ RAG Manager
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${APP_ROOT}/apps/rag-manager
Environment=PYTHONUNBUFFERED=1
ExecStart=${UV_BIN} run rag-manager --host ${MANAGER_HOST} --port ${MANAGER_PORT} --db ${MANAGER_DB} --logs-dir ${MANAGER_LOGS_DIR} --worker-host 127.0.0.1 --worker-base-port ${WORKER_BASE_PORT}
Restart=always
RestartSec=${RESTART_SEC}
KillMode=control-group
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target"

  write_unit "yq-qa-backend.service" "[Unit]
Description=YQ-QA Backend
After=network-online.target yq-rag-manager.service
Requires=yq-rag-manager.service

[Service]
Type=simple
WorkingDirectory=${APP_ROOT}
EnvironmentFile=-${BACKEND_ENV_FILE}
Environment=PYTHONUNBUFFERED=1
Environment=RAG_BACKEND=ovbot
Environment=OVBOT_BASE_URL=http://${GATEWAY_HOST}:${GATEWAY_PORT}
Environment=YQ_RAG_MANAGER_BASE_URL=http://${MANAGER_HOST}:${MANAGER_PORT}
ExecStart=${UV_BIN} run rag-server --host ${BACKEND_HOST} --port ${BACKEND_PORT} --manager-url http://${MANAGER_HOST}:${MANAGER_PORT} --db ${BACKEND_DB}
Restart=always
RestartSec=${RESTART_SEC}
KillMode=control-group
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target"
}

wait_http() {
  local name="$1"
  local url="$2"
  local timeout="${3:-90}"
  local deadline=$((SECONDS + timeout))
  info "waiting for ${name}: ${url}"
  while (( SECONDS < deadline )); do
    if curl -fsS --max-time 3 "${url}" >/dev/null 2>&1; then
      info "${name} is ready"
      return 0
    fi
    sleep 1
  done
  die "${name} did not become ready: ${url}"
}

register_method() {
  [[ "${REGISTER_METHOD}" == "1" ]] || return 0
  require_command "python" "${PYTHON_BIN}"
  info "registering manager method ${METHOD_ID}"
  "${PYTHON_BIN}" - "${MANAGER_HOST}" "${MANAGER_PORT}" "${METHOD_ID}" "${METHOD_DISPLAY_NAME}" "${APP_ROOT}" "${OV_CONF}" "${OPENVIKING_HOST}" "${OPENVIKING_PORT}" "${GATEWAY_HOST}" "${GATEWAY_PORT}" "${START_METHOD}" <<'PY'
import json
import sys
import urllib.error
import urllib.request

manager_host, manager_port, method_id, display_name, app_root, ov_conf, ov_host, ov_port, gateway_host, gateway_port, start_method = sys.argv[1:]
base = f"http://{manager_host}:{manager_port}"
payload = {
    "method_id": method_id,
    "backend_type": "openviking_bot",
    "display_name": display_name,
    "enabled": True,
    "config": {
        "project_path": f"{app_root}/apps/rag-openviking-bot",
        "ov_conf": ov_conf,
        "server_mode": "external",
        "server_url": f"http://{ov_host}:{ov_port}",
        "server_with_bot": False,
        "bot_route": "gateway",
        "gateway_mode": "external",
        "gateway_url": f"http://{gateway_host}:{gateway_port}",
        "cleanup_on_start": True,
        "logs_dir": f"{app_root}/logs/openviking-bot-worker",
    },
}

def request(method, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}

try:
    result = request("POST", "/v1/rag-methods", payload)
    print(json.dumps({"action": "created", "method": result}, ensure_ascii=False))
except urllib.error.HTTPError as exc:
    if exc.code != 409:
        raise
    result = request(
        "PATCH",
        f"/v1/rag-methods/{method_id}",
        {
            "display_name": display_name,
            "enabled": True,
            "config": payload["config"],
        },
    )
    print(json.dumps({"action": "updated", "method": result}, ensure_ascii=False))

if start_method == "1":
    result = request("POST", f"/v1/rag-methods/{method_id}/start")
    print(json.dumps({"action": "started", "runtime": result}, ensure_ascii=False))
PY
}

main() {
  require_root
  require_command "uv" "${UV_BIN}"
  require_command "curl" "$(command -v curl || true)"
  [[ -d "${APP_ROOT}" ]] || die "APP_ROOT not found: ${APP_ROOT}"
  [[ -f "${OV_CONF}" ]] || die "ov.conf not found: ${OV_CONF}; set YQ_OV_CONF or create config/ov.conf"

  info "APP_ROOT=${APP_ROOT}"
  info "OV_CONF=${OV_CONF}"
  info "ports: openviking=${OPENVIKING_PORT}, gateway=${GATEWAY_PORT}, manager=${MANAGER_PORT}, backend=${BACKEND_PORT}, workers=${WORKER_BASE_PORT}-$((WORKER_BASE_PORT + WORKER_PORT_COUNT - 1))"

  for service in "${SERVICES[@]}"; do
    systemctl stop "${service}" >/dev/null 2>&1 || true
  done
  cleanup_ports

  sync_project "${APP_ROOT}/apps/rag-openviking-bot"
  sync_project "${APP_ROOT}/apps/rag-manager"
  sync_project "${APP_ROOT}"

  install_units
  systemctl daemon-reload

  systemctl enable --now yq-openviking.service
  wait_http "OpenViking" "http://${OPENVIKING_HOST}:${OPENVIKING_PORT}/health" 120

  systemctl enable --now yq-vikingbot-gateway.service
  wait_http "vikingbot gateway" "http://${GATEWAY_HOST}:${GATEWAY_PORT}/bot/v1/health" 120

  systemctl enable --now yq-rag-manager.service
  wait_http "rag-manager" "http://${MANAGER_HOST}:${MANAGER_PORT}/health" 60

  register_method

  systemctl enable --now yq-qa-backend.service
  wait_http "yq-qa backend" "http://${BACKEND_HOST}:${BACKEND_PORT}/health" 60

  info "all services are started"
  systemctl --no-pager --full status yq-openviking.service yq-vikingbot-gateway.service yq-rag-manager.service yq-qa-backend.service || true
}

main "$@"
