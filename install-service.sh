#!/usr/bin/env bash
# Installs start.sh as a systemd service so it survives reboots and crashes.
# Run with sudo from wherever the app's repo lives (e.g. /srv/<project> in
# production). Generic by design: drop this file into any project with a
# start.sh entrypoint and it names the service after the checkout directory
# with no edits required. Tested against Debian 13 (trixie)'s systemd; no
# distro-specific behavior is used, so it should work unchanged on any
# reasonably current systemd-based Linux.
#
# Usage:
#   sudo ./install-service.sh             install + enable (not started)
#   sudo ./install-service.sh --start      install + enable + start now
#   sudo ./install-service.sh --no-enable  install only, skip enable/start
#   sudo ./install-service.sh --uninstall  stop, disable, remove the unit
#   sudo ./install-service.sh --help       show this usage
#
# Env overrides (all optional):
#   SERVICE_NAME=myapp          systemd unit name (default: sanitized checkout dirname)
#   SERVICE_DESCRIPTION="..."   unit Description= (default: "<SERVICE_NAME> service")
#   START_SCRIPT=run.sh         entrypoint relative to this script's directory (default: start.sh)
#   SERVICE_USER=someuser       user the service runs as (default: invoker of sudo)
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

sanitize_name() {
  # lowercase, non [a-z0-9-] -> '-', collapse/trim, so any directory name
  # becomes a safe systemd unit name.
  echo "$1" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9-]+/-/g; s/-+/-/g; s/^-|-$//g'
}

SERVICE_NAME="${SERVICE_NAME:-$(sanitize_name "$(basename "$APP_DIR")")}"
SERVICE_DESCRIPTION="${SERVICE_DESCRIPTION:-${SERVICE_NAME} service}"
START_SCRIPT="${APP_DIR}/${START_SCRIPT:-start.sh}"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"

ENABLE=1
START=0
UNINSTALL=0

for arg in "$@"; do
  case "$arg" in
    --start) START=1 ;;
    --no-enable) ENABLE=0 ;;
    --uninstall) UNINSTALL=1 ;;
    -h|--help)
      sed -n '2,21p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      exit 1
      ;;
  esac
done

if [[ "$(id -u)" -ne 0 ]]; then
  echo "This script must be run with sudo/root." >&2
  exit 1
fi

if [[ "$UNINSTALL" -eq 1 ]]; then
  echo "Stopping and disabling ${SERVICE_NAME}..."
  systemctl stop "${SERVICE_NAME}.service" 2>/dev/null || true
  systemctl disable "${SERVICE_NAME}.service" 2>/dev/null || true
  rm -f "$UNIT_PATH"
  systemctl daemon-reload
  echo "Removed ${UNIT_PATH}."
  exit 0
fi

if [[ ! -x "$START_SCRIPT" ]]; then
  echo "Expected executable entrypoint at ${START_SCRIPT}" >&2
  exit 1
fi

RUN_USER="${SERVICE_USER:-${SUDO_USER:-}}"
if [[ -z "$RUN_USER" || "$RUN_USER" == "root" ]]; then
  echo "Refusing to run the service as root. Set SERVICE_USER=<user> and re-run." >&2
  exit 1
fi

echo "Installing ${UNIT_PATH}"
echo "  Description: ${SERVICE_DESCRIPTION}"
echo "  ExecStart:   ${START_SCRIPT}"
echo "  User:        ${RUN_USER}"

cat > "$UNIT_PATH" <<EOF
[Unit]
Description=${SERVICE_DESCRIPTION}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${APP_DIR}
ExecStart=${START_SCRIPT}
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
ProtectSystem=full
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

chmod 644 "$UNIT_PATH"
systemctl daemon-reload

if [[ "$ENABLE" -eq 1 ]]; then
  systemctl enable "${SERVICE_NAME}.service"
fi

if [[ "$START" -eq 1 ]]; then
  systemctl start "${SERVICE_NAME}.service"
  systemctl status "${SERVICE_NAME}.service" --no-pager
else
  echo "Service installed. Start it with: sudo systemctl start ${SERVICE_NAME}"
fi
