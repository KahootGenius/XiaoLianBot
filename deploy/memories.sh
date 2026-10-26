#!/usr/bin/env bash
# Look at, rewrite or delete what AI chat remembers about members.
#
#   sudo /home/xiaolian/XiaoLianBot/deploy/memories.sh                  # every memory
#   sudo /home/xiaolian/XiaoLianBot/deploy/memories.sh 爸爸             # memories or nicknames containing 爸爸
#   sudo /home/xiaolian/XiaoLianBot/deploy/memories.sh delete 12 15     # delete memories 12 and 15
#   sudo /home/xiaolian/XiaoLianBot/deploy/memories.sh edit 12 新的内容  # rewrite memory 12
#
# Changes apply at once; the bot doesn't need a restart.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run it with sudo: sudo $0 $*" >&2
    exit 1
fi
# As the bot's user, so the database keeps its owner.
cd "$APP_DIR"
exec sudo -u "$(stat -c %U "$APP_DIR")" -H "$APP_DIR/.venv/bin/python" -m modules.ai.memory_admin "$@"
