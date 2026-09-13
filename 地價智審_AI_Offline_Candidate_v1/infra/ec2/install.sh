#!/usr/bin/env bash
# Ubuntu 24.04 EC2; place the project directory at /opt/ntpc/app first.
set -euo pipefail
if [[ $(id -u) != 0 ]]; then
    echo 'Run with sudo on the deployment EC2 instance.' >&2
    exit 1
fi
cd /opt/ntpc/app
test -f scripts/serve_app.py
# Do not inherit developer credentials or workstation configuration.
if [[ -e .env ]]; then
    echo 'Remove the workstation .env from the deployment package first.' >&2
    exit 1
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3-venv fonts-noto-cjk nginx curl
id ntpc >/dev/null 2>&1 || useradd --system --home-dir /var/lib/ntpc --create-home ntpc
install -d -o ntpc -g ntpc /var/lib/ntpc/cases
python3 -m venv /opt/ntpc/venv
/opt/ntpc/venv/bin/python -m pip install -r backend/requirements-local-app.txt
# The collector also writes local caches; keep these with persistent case data.
install -d -o ntpc -g ntpc /var/lib/ntpc/public-cache
install -m 644 infra/ec2/ntpc-app.service /etc/systemd/system/ntpc-app.service
install -m 644 infra/ec2/nginx.conf /etc/nginx/sites-available/ntpc-app
ln -sf /etc/nginx/sites-available/ntpc-app /etc/nginx/sites-enabled/ntpc-app
if [[ -L /etc/nginx/sites-enabled/default ]]; then unlink /etc/nginx/sites-enabled/default; fi
nginx -t
systemctl daemon-reload
systemctl enable --now ntpc-app
systemctl restart ntpc-app
systemctl enable --now nginx
systemctl reload nginx
curl --fail --retry 10 --retry-all-errors --retry-delay 2 http://127.0.0.1/api/cases
