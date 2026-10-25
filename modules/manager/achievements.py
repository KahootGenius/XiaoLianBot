"""Badges members earn from their own activity. Shown by /成就 and in the weekly digest.

The badges themselves live in prompts/achievements.yaml, so adding one needs
no code: each is a threshold on one of the COUNTERS below, a streak, or a
comeback. Only a brand-new kind of thing to count needs code -- add it to
COUNTERS and counter_increments.
"""
from dataclasses import dataclass
from typing import Optional
import yaml
from core.config import content_path


BADGES_FILE = content_path("prompts", "achievements.yaml")
COMEBACK_DAYS = 20

# What each valid message can add to. Badges in achievements.yaml count these.
COUNTERS = ("valid", "question", "reply", "share", "night", "morning", "weekend", "long", "days")
LONG_MESSAGE_UNITS = 50


@dataclass(frozen=True)
class Badge:
    code: str
    icon: str
    name: str
    desc: str
    announce: bool = True    # congratulated in the group when earned
    counter: Optional[str] = None
    at_least: int = 0
    streak: int = 0
    comeback: bool = False

    def met(self, counters, streak, comeback):
        if self.comeback:
            return comeback
        if self.streak:
            return streak >= self.streak
        return counters.get(self.counter, 0) >= self.at_least


def load_badges(path=BADGES_FILE):
    """Read and check achievements.yaml. A mistake there stops the bot at startup, not mid-chat.

    Error messages are in Chinese: the file's editors read them, not developers.
    """
    with open(path, encoding="utf-8") as f:
        entries = yaml.safe_load(f) or []
    badges, seen = [], set()
    for i, entry in enumerate(entries, 1):
        where = f"achievements.yaml 第 {i} 个成就"
        missing = [k for k in ("code", "icon", "name", "desc") if not entry.get(k)]
        if missing:
            raise ValueError(f"{where}缺少：{'、'.join(missing)}")
        code = str(entry["code"])
        where = f"{where}（{code}）"
        if code in seen:
            raise ValueError(f"{where}：code 重复了")
        seen.add(code)
        conditions = [k for k in ("counter", "streak", "comeback") if entry.get(k)]
        if len(conditions) != 1:
            raise ValueError(f"{where}：counter、streak、comeback 必须写，而且只能写其中一种")
        counter = entry.get("counter")
        if counter is not None:
            if counter not in COUNTERS:
                raise ValueError(f"{where}：没有叫「{counter}」的计数，可用的有：{'、'.join(COUNTERS)}")
            if int(entry.get("at_least") or 0) < 1:
                raise ValueError(f"{where}：at_least 要写一个正整数")
        badges.append(Badge(
            code=code,
            icon=str(entry["icon"]),
            name=str(entry["name"]),
            desc=str(entry["desc"]),
            announce=bool(entry.get("announce", True)),
            counter=counter,
            at_least=int(entry.get("at_least") or 0),
            streak=int(entry.get("streak") or 0),
            comeback=bool(entry.get("comeback", False)),
        ))
    return tuple(badges)


BADGES = load_badges()
BY_CODE = {b.code: b for b in BADGES}


def counter_increments(judgement, when, first_of_day):
    """What one valid message adds to each counter. `when` is its local send time."""
    return {
        "valid": 1,
        "question": int(judgement.question),
        "reply": int(judgement.reply),
        "share": int(judgement.share),
        "night": int(0 <= when.hour < 5),
        "morning": int(5 <= when.hour < 8),
        "weekend": int(when.isoweekday() >= 6),
        "long": int(judgement.units >= LONG_MESSAGE_UNITS),
        "days": int(first_of_day),
    }


def qualified(counters, streak, comeback):
    """Codes of every badge whose condition currently holds."""
    return [b.code for b in BADGES if b.met(counters, streak, comeback)]
