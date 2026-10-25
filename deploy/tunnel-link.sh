#!/usr/bin/env bash
# Print the member table's current share link: the tunnel's address plus the
# token from config.yaml. The address changes whenever the tunnel restarts.
#
#   sudo /home/xiaolian/XiaoLianBot/deploy/tunnel-link.sh
#
# The link contains the token: send it to admins privately, never in the main group.
set -euo pipefail

SERVICE="${SERVICE:-xiaolian-tunnel}"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run it with sudo: sudo $0" >&2
    exit 1
fi
if ! systemctl is-active --quiet "$SERVICE"; then
    echo "!! $SERVICE isn't running. Start it: sudo systemctl start $SERVICE" >&2
    exit 1
fi

# Only this run of the tunnel: an older address in the log is dead.
run="$(systemctl show -p InvocationID --value "$SERVICE")"
url=""
for _ in $(seq 30); do
    url="$(journalctl _SYSTEMD_INVOCATION_ID="$run" -o cat --no-pager \
           | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -n 1 || true)"
    [ -n "$url" ] && break
    sleep 1
done
if [ -z "$url" ]; then
    echo "!! The tunnel hasn't reported an address yet. Its log: journalctl -u $SERVICE -n 30" >&2
    exit 1
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
