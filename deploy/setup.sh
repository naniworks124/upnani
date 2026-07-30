#!/usr/bin/env bash
# One-shot setup script for a fresh Ubuntu/Debian VPS.
# Run this from inside the project folder after uploading it, e.g.:
#   scp -r vps-deploy/ user@your-vps-ip:/opt/smart-upload-engine
#   ssh user@your-vps-ip
#   cd /opt/smart-upload-engine
#   sudo bash deploy/setup.sh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

echo "==> Installing system packages (python3, venv, nginx)..."
apt-get update -y
apt-get install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx

echo "==> Creating virtual environment..."
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r backend/requirements.txt

if [ ! -f .env ]; then
  echo "==> No .env found — copying .env.example. EDIT THIS BEFORE STARTING THE SERVICE."
  cp .env.example .env
fi

echo "==> Installing systemd service..."
cp deploy/smart-upload-engine.service /etc/systemd/system/smart-upload-engine.service
# Point the service file at wherever this project actually lives.
sed -i "s#/opt/smart-upload-engine#${PROJECT_DIR}#g" /etc/systemd/system/smart-upload-engine.service
systemctl daemon-reload
systemctl enable smart-upload-engine

echo ""
echo "==> Setup complete."
echo "1. Edit ${PROJECT_DIR}/.env with your real MongoDB/Google/GoFile credentials."
echo "2. Start the app:   sudo systemctl start smart-upload-engine"
echo "3. Check logs:      sudo journalctl -u smart-upload-engine -f"
echo "4. To expose it on a domain, edit deploy/nginx-smart-upload-engine.conf"
echo "   with your domain, then:"
echo "     sudo cp deploy/nginx-smart-upload-engine.conf /etc/nginx/sites-available/smart-upload-engine"
echo "     sudo ln -s /etc/nginx/sites-available/smart-upload-engine /etc/nginx/sites-enabled/"
echo "     sudo nginx -t && sudo systemctl reload nginx"
echo "     sudo certbot --nginx -d yourdomain.com"
