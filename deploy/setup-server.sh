#!/usr/bin/env bash
set -euo pipefail

# Run this ONCE on the server to set up the deployment target.
# Usage: ssh your-server 'bash -s' < deploy/setup-server.sh

APP_DIR="/home/deploy/odds-scraper"
APP_USER="deploy"

# Create service user (no login shell, no home dir)
if ! id "$APP_USER" &>/dev/null; then
    sudo useradd --system --no-create-home --shell /usr/sbin/nologin "$APP_USER"
    echo "Created user: $APP_USER"
fi

# Create app directory
sudo mkdir -p "$APP_DIR/data"
sudo chown -R "$APP_USER:$APP_USER" "$APP_DIR"

# Clone repo (first time only)
if [ ! -d "$APP_DIR/.git" ]; then
    sudo -u "$APP_USER" git clone https://github.com/lorenzosntr-pawa/odds-scraper.git "$APP_DIR"
fi

# Create venv and install deps
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -e "$APP_DIR"

# Install systemd services
sudo cp "$APP_DIR/deploy/odds-scraper.service" /etc/systemd/system/
sudo cp "$APP_DIR/deploy/odds-scraper-web.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable odds-scraper odds-scraper-web
sudo systemctl start odds-scraper odds-scraper-web

echo ""
echo "Done. Services running:"
echo "  odds-scraper  — scraper (port: none, writes to $APP_DIR/data/odds.db)"
echo "  odds-scraper-web — web UI  (port: 8081)"
echo ""
echo "Next: copy your existing odds.db to $APP_DIR/data/odds.db"
echo "  scp data/odds.db your-server:$APP_DIR/data/"
echo "  ssh your-server 'sudo systemctl restart odds-scraper odds-scraper-web'"
