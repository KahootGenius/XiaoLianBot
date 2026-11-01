"""A speed limit on games, so nobody floods the group by tapping 再来一局 over and over.

config.yaml's games.per_minute is how many game commands one member can send in a minute: a
round of 21点 takes 3 to 5, so normal play never meets it. Past it they're told once how long to
wait, and further tries that minute get no reply, since answering each one would flood too.
"""
import math
import time
from collections import deque
from botpy import logging
from core.reply import reply
from core.style import t


_log = logging.get_logger()

# Matched the way the game modules match them: anywhere in the message.
COMMANDS = ("/21点", "/抽卡", "/停止", "/摇骰子", "/发癫")
PER_MINUTE = 10
WINDOW = 60


class SpeedLimit:
    def __init__(self, gateway=None, config=None, clock=time.monotonic):
        self.gw = gateway
        self.clock = clock
        games = (config or {}).get("games")
        raw = games.get("per_minute", PER_MINUTE) if isinstance(games, dict) else PER_MINUTE
        try:
            self.per_minute = int(raw)
        except (TypeError, ValueError):
            _log.warning(f"[Games] config.yaml 的 games.per_minute 应该是数字，现在是「{raw}」，改用 {PER_MINUTE}")
            self.per_minute = PER_MINUTE
        self.recent = {}        # (group, member) -> when their latest game commands came
        self.told = set()       # those already told to slow down this time

    async def handle_speed_limit(self, message):
        """True when this game command is over the limit, so nothing else should answer it."""
        if self.per_minute <= 0 or not any(command in message.content for command in COMMANDS):
            return False
        key = (message.group_openid, message.member_openid)
        now = self.clock()
        recent = self.recent.setdefault(key, deque())
        while recent and now - recent[0] >= WINDOW:
            recent.popleft()
        if len(recent) < self.per_minute:
            recent.append(now)
            self.told.discard(key)
            return False
        if key not in self.told:
            self.told.add(key)
            await reply(message, t("games.too_fast", seconds=math.ceil(WINDOW - (now - recent[0]))), self.gw)
        return True
