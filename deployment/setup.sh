#!/bin/bash
# ══════════════════════════════════════════════════════════════════════════════
#  Voice Agent Platform — Server Setup Script
#  Run on a fresh Ubuntu 22.04 / 24.04 VPS as root or with sudo.
#
#  Usage:
#    chmod +x setup.sh
#    sudo ./setup.sh yourdomain.com your@email.com
# ══════════════════════════════════════════════════════════════════════════════

set -e

DOMAIN=${1:?"Usage: $0 <domain> <email>"}
EMAIL=${2:?"Usage: $0 <domain> <email>"}

echo "==> Setting up Voice Agent Platform for domain: $DOMAIN"


# ── 1. System packages ────────────────────────────────────────────────────────
apt-get update -q
apt-get install -y nginx certbot python3-certbot-nginx redis-server python3-pip python3-venv


# ── 2. nginx config ───────────────────────────────────────────────────────────
sed "s/yourdomain.com/$DOMAIN/g" nginx.conf > /etc/nginx/sites-available/voiceagent

# Remove default site if present
rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/voiceagent /etc/nginx/sites-enabled/voiceagent

# Certbot needs port 80 briefly — start nginx with no SSL block first
# Strip ssl lines for initial validation
grep -v "ssl_certificate\|listen 443\|listen \[::\]:443\|http2 on\|ssl_protocol\|ssl_cipher\|ssl_prefer\|ssl_session\|ssl_ticket\|Strict-Transport" \
    /etc/nginx/sites-available/voiceagent > /tmp/nginx_nossl.conf
cp /tmp/nginx_nossl.conf /etc/nginx/sites-available/voiceagent_temp
ln -sf /etc/nginx/sites-available/voiceagent_temp /etc/nginx/sites-enabled/voiceagent
nginx -t && systemctl reload nginx || systemctl start nginx


# ── 3. Let's Encrypt certificate ──────────────────────────────────────────────
certbot certonly \
    --nginx \
    --non-interactive \
    --agree-tos \
    --email "$EMAIL" \
    -d "$DOMAIN"

# Restore full config with SSL
ln -sf /etc/nginx/sites-available/voiceagent /etc/nginx/sites-enabled/voiceagent
rm -f /etc/nginx/sites-enabled/voiceagent_temp
nginx -t && systemctl reload nginx

echo "==> SSL certificate issued for $DOMAIN"


# ── 4. Auto-renewal ───────────────────────────────────────────────────────────
# Certbot installs a systemd timer — verify it exists
systemctl list-timers | grep certbot || echo "WARNING: certbot timer not found — add manual cron"

# Also reload nginx after renewal so new cert is picked up
echo "0 3 * * * root certbot renew --quiet --post-hook 'systemctl reload nginx'" \
    >> /etc/cron.d/certbot-renew


# ── 5. Redis ──────────────────────────────────────────────────────────────────
systemctl enable redis-server
systemctl start  redis-server
echo "==> Redis running"


# ── 6. Harden Python servers (bind to localhost only) ─────────────────────────
echo ""
echo "==> IMPORTANT: In ws_gateway.py, change the bind addresses:"
echo "    websockets.serve(ws_handler, '127.0.0.1', 8080)   # was 0.0.0.0"
echo "    asyncio.start_server(_handle_static, '127.0.0.1', 8081)   # was 0.0.0.0"
echo "    flask_app.run(host='127.0.0.1', port=5001, ...)   # was 0.0.0.0"
echo ""
echo "    This ensures Python is only reachable via nginx, not directly from the internet."
echo ""


# ── 7. Summary ────────────────────────────────────────────────────────────────
echo "══════════════════════════════════════════════════════"
echo "  Setup complete!"
echo ""
echo "  WSS:    wss://$DOMAIN/ws"
echo "  Widget: https://$DOMAIN/widget/chat-widget.js"
echo "  API:    https://$DOMAIN/api/"
echo "  Health: https://$DOMAIN/health"
echo ""
echo "  Start the backend:"
echo "    cd /path/to/POC/backend"
echo "    source venv/bin/activate"
echo "    python ws_gateway.py"
echo "══════════════════════════════════════════════════════"
