#!/usr/bin/env bash
# Print the member table's share link: the tunnel's address plus the token from
# config.yaml. A quick tunnel's address changes whenever it restarts; a named
# tunnel (deploy/tunnel-fixed.sh) keeps its address.
#
#   sudo /home/xiaolian/XiaoLianBot/deploy/tunnel-link.sh
#
# The link contains the token: send it to admins privately, never in the main group.
set -euo pipefail

SERVICE="${SERVICE:-xiaolian-tunnel}"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-/etc/xiaolian/tunnel.env}"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run it with sudo: sudo $0" >&2
    exit 1
fi
if ! systemctl is-active --quiet "$SERVICE"; then
    echo "!! $SERVICE isn't running. Start it: sudo systemctl start $SERVICE" >&2
    exit 1
fi

# Only this run of the tunnel: older lines in the log are about a dead one.
run="$(systemctl show -p InvocationID --value "$SERVICE")"
this_run() { journalctl _SYSTEMD_INVOCATION_ID="$run" -o cat --no-pager; }
quick_url() { this_run | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -n 1 || true; }

url=""
if [ -f "$ENV_FILE" ] && grep -q '^TUNNEL_TOKEN=.' "$ENV_FILE"; then
    url="$(sed -n 's/^XIAOLIAN_ADDRESS=//p' "$ENV_FILE" | tail -n 1 | tr -d "\"' \r")"
    if [ -z "$url" ]; then
        echo "!! $ENV_FILE has no address. Run: sudo $APP_DIR/deploy/tunnel-fixed.sh" >&2
        exit 1
    fi
    connected=false
    for _ in $(seq 30); do
        if this_run | grep -q 'Registered tunnel connection'; then
            connected=true
            break
        fi
        sleep 1
    done
    if [ -n "$(quick_url)" ]; then
        echo "!! The tunnel is still running as a quick tunnel. Run: sudo $APP_DIR/deploy/tunnel-fixed.sh" >&2
        exit 1
    fi
    if [ "$connected" = false ]; then
        echo "!! The tunnel hasn't connected to Cloudflare. Its log: journalctl -u $SERVICE -n 30" >&2
        echo "   A wrong token shows up there; run tunnel-fixed.sh again to paste it again." >&2
        exit 1
    fi
else
    for _ in $(seq 30); do
        url="$(quick_url)"
        [ -n "$url" ] && break
        sleep 1
    done
    if [ -z "$url" ]; then
        echo "!! The tunnel hasn't reported an address yet. Its log: journalctl -u $SERVICE -n 30" >&2
        exit 1
    fi
fi

token="$("$APP_DIR/.venv/bin/python" - "$APP_DIR/config.yaml" <<'EOF'
import sys, yaml
with open(sys.argv[1], encoding="utf-8") as f:
    config = yaml.safe_load(f) or {}
print(((config.get("manager") or {}).get("dashboard") or {}).get("token") or "")
EOF
)"
if [ -z "$token" ]; then
    echo "!! config.yaml has no manager.dashboard.token, so the page refuses everyone coming through the tunnel." >&2
    echo "   Address only: $url" >&2
    exit 1
fi
echo "$url/?token=$token"
