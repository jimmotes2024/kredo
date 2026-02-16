#!/usr/bin/env bash
# Kredo Discovery API — Server provisioning script
# Target: Linode Nanode, Ubuntu 24.04, 198.58.117.117
# Run as root: bash setup.sh
set -euo pipefail

echo "=== Kredo API Server Setup ==="

# System packages
apt-get update
apt-get install -y python3 python3-pip python3-venv nginx certbot python3-certbot-nginx git ufw

# Firewall
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# Create service user
if ! id -u kredo &>/dev/null; then
    useradd --system --shell /bin/false --home-dir /opt/kredo --create-home kredo
fi

# Clone or update repo
if [ ! -d /opt/kredo/repo ]; then
    git clone https://github.com/jimmotes/kredo.git /opt/kredo/repo
else
    cd /opt/kredo/repo && git pull
fi

# Python virtualenv + install
python3 -m venv /opt/kredo/venv
/opt/kredo/venv/bin/pip install --upgrade pip
/opt/kredo/venv/bin/pip install /opt/kredo/repo

# Data directory for SQLite DB
mkdir -p /opt/kredo/data
chown -R kredo:kredo /opt/kredo/data

# Environment: point DB to data dir
mkdir -p /opt/kredo/etc
cat > /opt/kredo/etc/env <<'EOF'
KREDO_DB_PATH=/opt/kredo/data/kredo.db
EOF
chown -R kredo:kredo /opt/kredo/etc

# Systemd service
cp /opt/kredo/repo/deploy/kredo.service /etc/systemd/system/kredo.service
# Inject environment file
sed -i '/^\[Service\]/a EnvironmentFile=/opt/kredo/etc/env' /etc/systemd/system/kredo.service
systemctl daemon-reload
systemctl enable kredo
systemctl start kredo

# Nginx config
cp /opt/kredo/repo/deploy/nginx.conf /etc/nginx/sites-available/kredo
ln -sf /etc/nginx/sites-available/kredo /etc/nginx/sites-enabled/kredo
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

# SSL via Let's Encrypt
echo ""
echo "=== Run this manually after DNS is pointing to this server: ==="
echo "certbot --nginx -d api.aikredo.com --non-interactive --agree-tos -m trustwrit@gmail.com"
echo ""
echo "=== Setup complete ==="
echo "Test: curl http://localhost:8000/health"
systemctl status kredo --no-pager
