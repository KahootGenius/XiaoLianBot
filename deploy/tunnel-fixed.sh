#!/usr/bin/env bash
# Give the member table a fixed address through a Cloudflare named tunnel, or go
# back to a quick tunnel with a random address. See docs/DEPLOY.md, "A fixed address".
#
#   sudo /home/xiaolian/XiaoLianBot/deploy/tunnel-fixed.sh        # asks for the token and the address
#   sudo /home/xiaolian/XiaoLianBot/deploy/tunnel-fixed.sh --off  # back to a quick tunnel
#
# The token is saved in /etc/xiaolian/tunnel.env, which only root can read.
set -euo pipefail

SERVICE="${SERVICE:-xiaolian-tunnel}"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-/etc/xiaolian/tunnel.env}"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run it with sudo: sudo $0 $*" >&2
    exit 1
fi
if ! command -v cloudflared >/dev/null; then
    echo "!! cloudflared isn't installed yet. See docs/DEPLOY.md, \"The member table for other admins\"." >&2
    exit 1
fi

restart_tunnel() {
    # The service file chooses the kind of tunnel, so install the current copy first.
    cp "$APP_DIR/deploy/$SERVICE.service" /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable --quiet "$SERVICE"
    systemctl restart "$SERVICE"
}

if [ "${1:-}" = "--off" ]; then
    rm -f "$ENV_FILE"
    restart_tunnel
    echo "==> Back to a quick tunnel. Its address is random and changes whenever it restarts."
    exec "$APP_DIR/deploy/tunnel-link.sh"
fi

echo "Paste the install command Cloudflare showed you (or just the token in it), then press Enter."
echo "Nothing shows on screen while you paste."
# The command can span several lines; the token is the long word starting with eyJ.
token=""
while [ -z "$token" ] && IFS= read -r -s line; do
    token="$(printf '%s\n' "$line" | tr -s ' \t' '\n\n' | grep -E '^eyJ[A-Za-z0-9+/=_-]{40,}$' | tail -n 1 || true)"
done
while IFS= read -r -s -t 1 _; do :; done     # swallow the rest of a multi-line paste
echo
if [ -z "$token" ]; then
    echo "!! No token found. It's the long word starting with eyJ at the end of the install command." >&2
    exit 1
fi
echo "==> Got the token (${#token} characters)."

read -r -p "The address you'll give it in Cloudflare, like panel.example.com: " address
address="${address#http://}"
address="${address#https://}"
address="${address%%/*}"
if ! [[ "$address" =~ ^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$ ]]; then
    echo "!! \"$address\" isn't an address like panel.example.com." >&2
    exit 1
fi

install -d -m 700 "$(dirname "$ENV_FILE")"
(umask 077 && printf 'TUNNEL_TOKEN=%s\nXIAOLIAN_ADDRESS=https://%s\n' "$token" "$address" > "$ENV_FILE")
chmod 600 "$ENV_FILE"
restart_tunnel
echo "==> Saved. The tunnel now runs as your named tunnel."
echo "    If you haven't yet: in Cloudflare, add the route https://$address -> http://127.0.0.1:8765"
if systemctl cat cloudflared.service >/dev/null 2>&1; then
    echo "!! Cloudflare's own cloudflared service is installed too (from running its install command)."
    echo "   It's a second copy of the tunnel. Remove it: sudo cloudflared service uninstall"
fi
exec "$APP_DIR/deploy/tunnel-link.sh"
