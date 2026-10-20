#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root: sudo scripts/install_service.sh" >&2
  exit 1
fi

if ! id -u sensor-monitor >/dev/null 2>&1; then
  useradd --system --home-dir /opt/sensor-monitor --shell /usr/sbin/nologin sensor-monitor
fi
if getent group dialout >/dev/null 2>&1; then
  usermod -aG dialout sensor-monitor
fi
install -d -o sensor-monitor -g dialout /opt/sensor-monitor /var/log/sensor-monitor
python3 -m venv /opt/sensor-monitor/.venv
/opt/sensor-monitor/.venv/bin/pip install /opt/sensor-monitor
install -m 0644 deploy/sensor-monitor.service /etc/systemd/system/sensor-monitor.service
systemctl daemon-reload
systemctl enable --now sensor-monitor.service
systemctl --no-pager status sensor-monitor.service
