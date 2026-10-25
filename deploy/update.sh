#!/usr/bin/env bash
# Update the bot on the server to the latest code on GitHub and restart it.
#
#   sudo /home/xiaolian/XiaoLianBot/deploy/update.sh            # pull; restart if anything changed
#   sudo /home/xiaolian/XiaoLianBot/deploy/update.sh --restart  # restart even if nothing changed
#
# Steps: git pull (fast-forward only) -> reinstall packages if requirements.txt
# changed -> run the tests -> restart the service. If the tests fail, the code
# goes back to what was running and the bot is left alone.
# Settings: SERVICE (default xiaolian). Git runs as the user who owns the folder.
set -euo pipefail

SERVICE="${SERVICE:-xiaolian}"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FORCE_RESTART=false
[ "${1:-}" = "--restart" ] && FORCE_RESTART=true

if [ "$(id -u)" -ne 0 ]; then
    echo "Run it with sudo: sudo $0 $*" >&2
    exit 1
fi

APP_USER="$(stat -c %U "$APP_DIR")"
# Git, pip and the tests run as the bot's user, with its home (and its GitHub deploy key).
as_app() { sudo -u "$APP_USER" -H "$@"; }
git_app() { as_app git -C "$APP_DIR" "$@"; }

before="$(git_app rev-parse HEAD)"
echo "==> Pulling the latest code ($(git_app rev-parse --abbrev-ref HEAD))"
if ! git_app pull --ff-only; then
    echo "!! git pull failed. If files were edited on the server, see 'git status' in $APP_DIR." >&2
    exit 1
fi
after="$(git_app rev-parse HEAD)"

if [ "$before" = "$after" ] && [ "$FORCE_RESTART" = false ]; then
    echo "==> Already up to date: $(git_app log -1 --format='%h %s'). Nothing restarted."
    exit 0
fi

if [ "$before" != "$after" ]; then
    echo "==> New commits:"
    git_app --no-pager log --format='    %h %s' "$before..$after"
    if ! git_app diff --quiet "$before" "$after" -- requirements.txt; then
        echo "==> requirements.txt changed: installing packages"
        as_app "$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
    fi
fi

echo "==> Running the tests"
test_log="$(mktemp)"
trap 'rm -f "$test_log"' EXIT
if ! (cd "$APP_DIR" && as_app "$APP_DIR/.venv/bin/python" -m unittest discover -s tests -t . >"$test_log" 2>&1); then
    tail -n 30 "$test_log" >&2
    if [ "$before" != "$after" ]; then
        git_app reset --keep "$before"
        echo "!! Tests failed, so the code is back at $(git_app log -1 --format='%h %s') and the bot was not restarted." >&2
    else
        echo "!! Tests failed; the bot was not restarted." >&2
    fi
    exit 1
fi
grep -E '^(Ran|OK)' "$test_log" | sed 's/^/    /' || true

echo "==> Restarting $SERVICE"
systemctl restart "$SERVICE"
sleep 5
if systemctl is-active --quiet "$SERVICE"; then
    echo "==> Running $(git_app log -1 --format='%h %s'). Recent log:"
    journalctl -u "$SERVICE" -n 12 --no-pager --output=cat
else
    echo "!! $SERVICE did not stay up. Recent log:" >&2
    journalctl -u "$SERVICE" -n 40 --no-pager --output=cat >&2
    exit 1
fi
