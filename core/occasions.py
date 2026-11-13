"""Special days, read from prompts/occasions.yaml: 元旦, 春节, 中秋, the group's own anniversary.

On such a day the bot behaves a little differently, without telling anyone why:
- every line in a style file can have a version for the day, written next to it as key@元旦, the
  same way a line has versions for particular people. That alone covers most of it: with
  social.fortune.levels@元旦: [大吉], everyone's 今日运势 that day is 大吉;
- the first few members to speak get a line of their own (`greet`, `first`);
- AI chat is told what day it is, with a note if the day has one (`chat`);
- the team game's die is kinder (`die`).

A day is given by `when`: 01-01 (every year), 2027-02-06 (only then), or a lunar date such as
农历 正月初一, 农历 八月十五 or 农历 除夕, which core/lunar.py turns into this year's date. `lasts`
makes it run for several days.

    python -m core.occasions     # each day in the file and when it next comes round
"""
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import pytz
import yaml
from core import lunar
from core.config import content_path


DAYS_FILE = content_path("prompts", "occasions.yaml")
FIRST = 10              # members per group who get a day's greeting, unless the day says otherwise
MAX_LASTS = 31          # days one occasion can run
MAX_DIE = 5             # the most a day can add to the team game's die
GREET_FIELDS = ("member", "n", "day", "name")
_EVERY_YEAR = re.compile(r"\d{2}-\d{2}")
_ONE_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_LUNAR = re.compile(r"农历\s*(.+)")
_LUNAR_NUMBERS = re.compile(r"(\d{1,2})-(\d{1,2})")
EVE = "除夕"
WHEN_HELP = "01-01（每年这天）、2027-02-06（只有那一天）、农历 正月初一、农历 八月十五、农历 除夕"


@dataclass(frozen=True)
class When:
    """One way of saying which day: a day of every year, one date, or a day of the lunar year."""
    kind: str               # "yearly", "once", "lunar" or "eve"
    month: int = 0
    day: int = 0
    year: int = 0

    def falls_on(self, day):
        if self.kind == "yearly":
            return (day.month, day.day) == (self.month, self.day)
        if self.kind == "once":
            return day == date(self.year, self.month, self.day)
        if self.kind == "eve":
            return lunar.is_eve(day)
        return lunar.lunar(day) == (self.month, self.day, False)       # never in a leap month


def parse_when(value):
    """A When from what the file says, or ValueError. YAML reads an unquoted 2027-02-06 as a date
    and 01-01 as text; both work."""
    text = value.isoformat() if isinstance(value, date) else str(value).strip()
    try:
        if _EVERY_YEAR.fullmatch(text):
            checked = date.fromisoformat(f"2000-{text}")       # 2000 was a leap year, so 02-29 passes
            return When("yearly", checked.month, checked.day)
        if _ONE_DAY.fullmatch(text):
            checked = date.fromisoformat(text)
            return When("once", checked.month, checked.day, checked.year)
        found = _LUNAR.fullmatch(text)
        if found:
            said = found.group(1).strip()
            if said == EVE:
                return When("eve")
            numbers = _LUNAR_NUMBERS.fullmatch(said)
            if numbers:
                month, day = int(numbers.group(1)), int(numbers.group(2))
            else:
                name = next((m for m in lunar.MONTH_NAMES if said.startswith(m)), None)      # no name begins another
                month, day = lunar.MONTH_NAMES.get(name, 0), lunar.DAY_NAMES.get(said[len(name or ""):].strip(), 0)
            if 1 <= month <= 12 and 1 <= day <= 30:
                return When("lunar", month, day)
    except ValueError:
        pass
    raise ValueError(f"「{text}」不是日期，可以写成：{WHEN_HELP}")


@dataclass(frozen=True)
class Occasion:
    name: str
    when: tuple             # of When: the day comes when any of them does
    lasts: int = 1          # days it runs, from each of those
    chat: str = ""          # what AI chat is told about the day, besides its name
    greet: tuple = ()       # lines to pick from for the first members who speak that day
    first: int = FIRST      # how many members in each group get one; 0 = everyone
    die: int = 0            # added to what the team game's die shows, up to its six

    def on(self, day):
        return any(when.falls_on(day - timedelta(days=back)) for when in self.when for back in range(self.lasts))

    def next_from(self, day, within=800):
        """The next date it falls on, from `day` on, or None if there's none in the next two years."""
        return next((day + timedelta(days=ahead) for ahead in range(within) if self.on(day + timedelta(days=ahead))), None)


def _lines(value):
    if value is None:
        return []
    return [str(v).strip() for v in (value if isinstance(value, (list, tuple)) else [value]) if str(v).strip()]


def _number(entry, key, default, least, most, where):
    try:
        number = int(entry.get(key, default))
        if not least <= number <= most:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError(f"{where}：{key} 要写 {least} 到 {most} 的整数") from None
    return number


def _read(entry, where, taken):
    from core.style import ADMIN, placeholders       # the style book reads this module's days
    if not isinstance(entry, dict) or not str(entry.get("name") or "").strip():
        raise ValueError(f"{where}缺少 name")
    name = str(entry["name"]).strip()
    where = f"{where}（{name}）"
    if any(c.isspace() or c in ".@" for c in name) or name == ADMIN:
        raise ValueError(f"{where}：名字里不能有空格、. 和 @，也不能叫 {ADMIN}（风格文件里用「键名@{name}」给这一天另写一版）")
    if name in taken:
        raise ValueError(f"{where}：名字和前面的重复了")
    spans = entry.get("when")       # not "on": YAML reads that word as true
    if spans is None or spans == "" or spans == []:
        raise ValueError(f"{where}：when 要写是哪一天，例如 {WHEN_HELP}")
    try:
        when = tuple(parse_when(item) for item in (spans if isinstance(spans, list) else [spans]))
    except ValueError as e:
        raise ValueError(f"{where}：when 里的{e}") from None
    greet = _lines(entry.get("greet"))
    for line in greet:
        try:
            unknown = placeholders(line) - set(GREET_FIELDS)
        except ValueError as e:
            raise ValueError(f"{where}：greet 的花括号写得不对（{e}）") from None
        if unknown:
            raise ValueError(f"{where}：greet 里的 {'、'.join('{' + u + '}' for u in sorted(unknown))} 程序不会填写，"
                             f"能用的有：{'、'.join('{' + f + '}' for f in GREET_FIELDS)}")
    return Occasion(name=name, when=when, lasts=_number(entry, "lasts", 1, 1, MAX_LASTS, where),
                    chat=" ".join(str(entry.get("chat") or "").split()), greet=tuple(greet),
                    first=_number(entry, "first", FIRST, 0, 100000, where),
                    die=_number(entry, "die", 0, 0, MAX_DIE, where))


def load(path=DAYS_FILE):
    """([Occasion], [problems]) from the file. A day written wrong is left out and named, so one
    mistake costs that day and not the bot's start. No file, no special days."""
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return [], []
    except (OSError, yaml.YAMLError) as e:
        return [], [f"occasions.yaml 读不了：{e}"]
    entries = data.get("days") if isinstance(data, dict) else None
    if entries is None:
        return [], []
    if not isinstance(entries, list):
        return [], ["occasions.yaml 的 days 要写成列表，每个日子以「- name:」开头"]
    found, problems = [], []
    for i, entry in enumerate(entries, 1):
        try:
            found.append(_read(entry, f"occasions.yaml 第 {i} 个日子", {o.name for o in found}))
        except ValueError as e:
            problems.append(str(e))
    return found, problems


class Calendar:
    """Which of the file's days it is today, by the Beijing date."""

    def __init__(self, occasions=(), clock=None):
        self.occasions = tuple(occasions)
        self.tz = pytz.timezone("Asia/Shanghai")
        self.clock = clock or (lambda: datetime.now(self.tz))
        self._day = self._today = None

    def today(self):
        """Today's occasions, in the file's order."""
        day = self.clock().astimezone(self.tz).date()
        if day != self._day:
            self._day, self._today = day, tuple(o for o in self.occasions if o.on(day))
        return self._today

    def names(self):
        return tuple(o.name for o in self.today())

    def install(self, styles):
        """Has a style book speak each day's versions of its lines (key@元旦) on that day."""
        styles.days, styles.day_names = self.names, {o.name for o in self.occasions}

    def running_out(self):
        """What to say in the log once the lunar table's last year has begun and a day needs it."""
        today = self.clock().astimezone(self.tz).date()
        needs = any(when.kind in ("lunar", "eve") for o in self.occasions for when in o.when)
        if needs and today.year >= lunar.LAST.year - 1:
            return (f"农历日期表只到 {lunar.LAST.isoformat()}，之后农历的日子（春节、中秋…）不会再到来："
                    f"请给 core/lunar.py 的 STARTS 补上后面的年份")
        return None


def main(path=DAYS_FILE, out=sys.stdout, today=None):
    found, problems = load(path)
    today = today or datetime.now(pytz.timezone("Asia/Shanghai")).date()
    for occasion in found:
        coming = occasion.next_from(today)
        eggs = [label for label, has in (("问候", occasion.greet), ("聊天", occasion.chat), (f"骰子+{occasion.die}", occasion.die))
                if has]
        when = "两年内没有" if coming is None else "就是今天" if coming == today else coming.isoformat()
        lasts = f"，连续 {occasion.lasts} 天" if occasion.lasts > 1 else ""
        print(f"{occasion.name}  下一次：{when}{lasts}  {' · '.join(eggs)}", file=out)
    for problem in problems:
        print(f"!! {problem}", file=out)
    print(f"{path}：{len(found)} 个日子，{len(problems)} 个问题。", file=out)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
