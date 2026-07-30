#!/usr/bin/env bash
# Sets up a Cloudflare Tunnel so a domain/subdomain always points at this
# app, even on free/ephemeral VPS providers where the IP address changes
# every time the VPS restarts.
#
# Unlike a normal nginx + DNS "A record" setup, this needs NO static IP:
# the tunnel is an outbound connection from this VPS to Cloudflare, so it
# survives IP changes and reconnects automatically on its own.
#
# Prerequisites:
#   - A domain added to a free Cloudflare account (Cloudflare must be
#     managing its DNS — you can transfer just the nameservers over even
#     if you bought the domain elsewhere, e.g. Namecheap/GoDaddy).
#
# Usage:
#   sudo bash deploy/cloudflare-tunnel-setup.sh yourdomain.com upload.yourdomain.com
#
set -euo pipefail

ROOT_DOMAIN="${1:-}"
SUBDOMAIN="${2:-}"

if [[ -z "$ROOT_DOMAIN" || -z "$SUBDOMAIN" ]]; then
  echo "Usage: sudo bash deploy/cloudflare-tunnel-setup.sh <root-domain> <full-subdomain>"
  echo "Example: sudo bash deploy/cloudflare-tunnel-setup.sh example.com upload.example.com"
  exit 1
fi

echo "==> Installing cloudflared..."
if ! command -v cloudflared &>/dev/null; then
  ARCH=$(dpkg --print-architecture)
  curl -L -o /tmp/cloudflared.deb \
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${ARCH}.deb"
  dpkg -i /tmp/cloudflared.deb
fi

echo "==> Logging into Cloudflare (this opens a URL you paste into your browser)..."
echo "    Log in and select: ${ROOT_DOMAIN}"
cloudflared tunnel login

echo "==> Creating tunnel 'smart-upload-engine'..."
cloudflared tunnel create smart-upload-engine || true
TUNNEL_ID=$(cloudflared tunnel list | grep smart-upload-engine | awk '{print $1}')

mkdir -p /etc/cloudflared
cat > /etc/cloudflared/config.yml <<EOF
tunnel: ${TUNNEL_ID}
credentials-file: /root/.cloudflared/${TUNNEL_ID}.json

ingress:
  - hostname: ${SUBDOMAIN}
    service: http://127.0.0.1:7860
  - service: http_status:404
EOF

echo "==> Pointing ${SUBDOMAIN} at this tunnel..."
cloudflared tunnel route dns smart-upload-engine "${SUBDOMAIN}"

echo "==> Installing cloudflared as a systemd service (auto-reconnects on IP change/reboot)..."
cloudflared service install
systemctl enable cloudflared
systemctl restart cloudflared

echo ""
echo "==> Done. Once the app itself is running (systemctl start smart-upload-engine),"
echo "    your dashboard will be reachable at: https://${SUBDOMAIN}"
echo "    This keeps working even if the VPS gets a new IP on restart —"
echo "    cloudflared reconnects automatically, no DNS changes needed."
