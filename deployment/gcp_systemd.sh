#!/bin/bash
set -euo pipefail
# Create the ThetaForge Advisor systemd unit on the GCP VM (direct uvicorn).
sudo tee /etc/systemd/system/thetaforge-advisor.service >/dev/null <<'UNIT'
[Unit]
Description=ThetaForge Advisor (FastAPI, direct uvicorn)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=thetaforge
Group=thetaforge
WorkingDirectory=/opt/thetaforge
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=/opt/thetaforge/.env
ExecStart=/opt/thetaforge/venv/bin/uvicorn orchestrator.main:app --host 0.0.0.0 --port 8000 --workers 1 --timeout-keep-alive 30
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable thetaforge-advisor
sudo systemctl restart thetaforge-advisor
sleep 6
echo "ACTIVE: $(systemctl is-active thetaforge-advisor)"
echo "---LOG---"
sudo journalctl -u thetaforge-advisor --no-pager -n 30 | tail -25