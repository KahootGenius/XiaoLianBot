"""Badges members earn from their own activity. Shown by /成就 and in the weekly digest.

The badges themselves live in prompts/achievements.yaml, so adding one needs
no code: each is a threshold on one of the COUNTERS below, a streak, a
comeback, or chatting on given days of the year. Only a brand-new kind of
thing to count needs code -- add it to COUNTERS, then count it in
counter_increments (each valid message) or through ManagerModule.count
(anything else, like a vote).
"""
import re
from dataclasses import dataclass
from datetime import date
from typing import Optional
import yaml
from core.config import content_path


BADGES_FILE = content_path("prompts", "achievements.yaml")
COMEBACK_DAYS = 20
# The group's first valid message from this hour on opens its day ("opener"). Not from
# midnight, so staying up doesn't win it.
OPENER_HOUR = 5

# What badges in achievements.yaml can count. Each valid message adds to the first ten;
# other features count the rest: a birthday, a poll voted in, a 征集 joined, a night's sleep.
COUNTERS = ("valid", "question", "reply", "share", "night", "morning", "weekend", "long", "days", "opener",
            "birthday", "vote", "opinion", "sleep")
LONG_MESSAGE_UNITS = 50

_EVERY_YEAR = re.compile(r"\d{2}-\d{2}")
_ONE_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class Badge:
    code: str
    icon: str
    name: str
    desc: str
    announce: bool = True    # congratulated in the group when earned
    hidden: bool = False     # /成就 doesn't name it until it's earned
    counter: Optional[str] = None
    at_least: int = 0
    streak: int = 0
    comeback: bool = False
    dates: tuple = ()        # "01-01" every year, "2027-02-06" only then

    def met(self, counters, streak, comeback, today=None):
        """`today` is the local date of a valid message; without one, dates never match."""
        if self.comeback:
            return comeback
        if self.streak:
            return streak >= self.streak
        if self.dates:
            return today is not None and (today.isoformat() in self.dates or today.strftime("%m-%d") in self.dates)
        return counters.get(self.counter, 0) >= self.at_least

    def progress(self, counters, streak):
        """(have, need) for a count or a streak; None for a comeback or a day of the year."""
        if self.streak:
            return min(streak, self.streak), self.streak
        if self.counter:
            return min(counters.get(self.counter, 0), self.at_least), self.at_least
        return None


def _read_dates(value, where):
    """YAML reads an unquoted 2027-02-06 as a date and 01-01 as text; both work."""
    if not value:
        return ()
    days = []
    for item in value if isinstance(value, list) else [value]:
        text = item.isoformat() if isinstance(item, date) else str(item).strip()
        try:
            if _EVERY_YEAR.fullmatch(text):
                date.fromisoformat(f"2000-{text}")      # 2000 was a leap year, so 02-29 passes
            elif _ONE_DAY.fullmatch(text):
                date.fromisoformat(text)
            else:
                raise ValueError(text)
        except ValueError:
            raise ValueError(f"{where}：「{text}」不是日期，写成 01-01（每年这天）或 2027-02-06（只有那一天）") from None
        days.append(text)
    return tuple(days)


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
        conditions = [k for k in ("counter", "streak", "comeback", "dates") if entry.get(k)]
        if len(conditions) != 1:
            raise ValueError(f"{where}：counter、streak、comeback、dates 必须写，而且只能写其中一种")
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
            hidden=bool(entry.get("hidden", False)),
            counter=counter,
            at_least=int(entry.get("at_least") or 0),
            streak=int(entry.get("streak") or 0),
            comeback=bool(entry.get("comeback", False)),
            dates=_read_dates(entry.get("dates"), where),
        ))
    return tuple(badges)


BADGES = load_badges()
BY_CODE = {b.code: b for b in BADGES}


def counter_increments(judgement, when, first_of_day, opener=False):
    """What one valid message adds to each counter. `when` is its local send time; `opener`
    says it's the group's first valid message of the day."""
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
        "opener": int(opener),
    }


def qualified(counters, streak, comeback, today=None):
    """Codes of every badge whose condition currently holds."""
    return [b.code for b in BADGES if b.met(counters, streak, comeback, today)]


def next_goal(earned, counters, streak):
    """(badge, (have, need)) for the locked badge someone is closest to, or None. Hidden
    badges stay secret, and a comeback or a day of the year has nothing to count."""
    best = None
    for badge in BADGES:
        progress = None if badge.code in earned or badge.hidden else badge.progress(counters, streak)
        if progress and (best is None or progress[0] / progress[1] > best[1][0] / best[1][1]):
            best = badge, progress
    return best
