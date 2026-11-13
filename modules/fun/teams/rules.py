"""The team game's rules, read from prompts/teams.yaml: what statuses a team
member can have, every action captains can use on their members or to take
someone else's, the events that can befall them, and where people can hide.

A new status, action, event or hiding place is a few lines of YAML. Only a new
kind of effect needs code: add its name to EFFECTS below and an effect_<name>
method to TeamsModule.
"""
from dataclasses import dataclass
from datetime import time as dtime
import yaml
from core.config import content_path
from core.style import placeholders


RULES_FILE = content_path("prompts", "teams.yaml")
# In `by`, the captain of the target's team.
CAPTAIN = "队长"
# Effects an action can name with `effect:`. Actions with `set:` have the effect "set".
EFFECTS = ("grab", "kick", "roll", "steal")
EFFECT_HELP = ("grab（把人抓进队）、kick（移出队伍）、roll（从 from 里随机掷一个状态）、"
               "steal（掷骰子，把别的队长的队员抢进自己的队伍）")
# The effects that bring someone into the actor's own team. Every other one is used on the
# members of a team the actor commands, and admins may referee those.
TAKING = ("grab", "steal")
# What an event can do to whoever meets it; one with `set:` has the effect "set", and one with
# neither only says something.
EVENT_EFFECTS = ("kick", "roll")
EVENT_EFFECT_HELP = "kick（掉出队伍）、roll（从 from 里随机掷一个状态）"
# The game's built-in commands and their default words. teams.yaml may rename
# them under `commands:`; actions can't take these words.
FIXED_COMMANDS = {"teams": "队伍", "disband": "解散队伍", "clear": "清空队伍", "hide": "躲", "seek": "找",
                  "record": "档案"}
FIXED_HELP = "teams（查看队伍）、disband（解散队伍）、clear（清空队伍）、hide（躲起来）、seek（队长找人）、record（游戏档案）"
# What the game calls itself, its captains and its teams, in the server log and in
# load_rules' messages. teams.yaml may change them under `names:`.
NAMES = {"game": "组队游戏", "captain": "队长", "team": "队伍"}
NAMES_HELP = "game（游戏名）、captain（队长的叫法）、team（队伍的叫法）"
# What an action's `say` line may use; {name} is the style's name.
SAY_FIELDS = ("actor", "members", "count", "captain", "action", "status", "name")
# ...a steal's `say` and `fail`: {captain} is who they are taken from, {roll} what the die showed,
# {need} what it had to show, {left} the throws the actor still has today. One line for each
# member, who ends up with no new status, so no {count} and no {status}.
STEAL_FIELDS = ("actor", "members", "captain", "action", "name", "roll", "need", "left")
# ...and an event's: nobody acts in one.
EVENT_FIELDS = ("members", "captain", "status", "name")
DIE = 6                 # the faces of the die a steal throws
NEED = 4                # what it must show at least, unless the action says otherwise: half the throws
THROWS = 3              # throws of it each captain gets in a game day
EVENT_GAP = 10          # minutes between two events in a group, unless teams.yaml says otherwise
EVENT_DAILY = 2         # events one member can meet in a game day
MIN_SPOTS = 3           # hiding places: with fewer, a search is hardly a guess
MAX_SPOTS = 25          # they go on the buttons under /找, five rows of five
MAX_SPOT = 8            # characters in one, so it fits on a button
SEARCHES = 3            # places each captain can look in a game day
HIDES = 3               # times one member can hide in a game day


@dataclass(frozen=True)
class Action:
    name: str
    words: tuple            # what people type after "/": the name, then any aliases
    effect: str             # "grab", "kick", "roll", "steal" or "set"
    status: str = ""        # set: the status it gives
    column: str = ""        # set: the column that status belongs to
    only: tuple = ()        # the target must have every one of these statuses
    by: tuple = (CAPTAIN,)  # who may use it besides admins: CAPTAIN and/or statuses
    say: tuple = ()         # lines to pick from afterwards; empty = the style's generic line
    pool: tuple = ()        # roll: the statuses a roll can land on
    need: int = 0           # steal: the die must show at least this
    times: int = 0          # steal: throws of the die each captain gets in a game day
    fail: tuple = ()        # steal: lines to pick from when the die falls short; empty = the style's generic line


@dataclass(frozen=True)
class Event:
    """Something that can befall a team member while the group chats."""
    name: str
    chance: float           # percent, each time someone it can happen through speaks
    captain: bool = False   # True: it comes when a captain speaks, to one of their members; else to whoever spoke
    effect: str = ""        # "kick", "roll" or "set"; "": it only says something
    status: str = ""        # set: the status it gives
    column: str = ""        # set: the column that status belongs to
    only: tuple = ()        # whoever meets it must have every one of these statuses
    say: tuple = ()         # lines to pick from when it happens
    pool: tuple = ()        # roll: the statuses it can land on


@dataclass(frozen=True)
class Rules:
    columns: dict           # column -> its statuses; the first is everyone's status when grabbed
    icons: dict             # status -> icon shown in front of names in /队伍
    actions: tuple
    max_members: int = 0    # per team, not counting the captain; 0 = no limit
    reset_time: dtime = dtime(5, 0)
    commands: dict = None   # built-in command -> its word, see FIXED_COMMANDS
    hidden: tuple = ()      # statuses AI chat is never told about (hide_from_ai)
    names: dict = None      # what the game calls itself, its captains and teams, see NAMES
    events: tuple = ()      # what can befall members while the group chats
    event_gap: int = EVENT_GAP          # minutes between two events in a group
    event_daily: int = EVENT_DAILY      # events one member can meet in a game day; 0 = no limit
    spots: tuple = ()       # where people can hide; none = no hide-and-seek
    searches: int = SEARCHES            # places each captain can look in a game day
    hides: int = HIDES                  # times one member can hide in a game day

    def column_of(self, status):
        return next(column for column, statuses in self.columns.items() if status in statuses)

    def starting(self):
        """A newly grabbed member's statuses."""
        return {column: statuses[0] for column, statuses in self.columns.items()}

    def action(self, word):
        return next((a for a in self.actions if word in a.words), None)

    def tidy(self, statuses):
        """Statuses saved under older rules, as these rules see them: unknown ones go back to the start."""
        return {column: statuses.get(column) if statuses.get(column) in options else options[0]
                for column, options in self.columns.items()}


def _names(value):
    """A string or a list of strings, as a list."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _word(value, where):
    word = str(value).strip().lstrip("/")
    if not word or any(c.isspace() or c in "@/" for c in word):
        raise ValueError(f"{where}：指令名「{value}」不能是空的，也不能带空格、@ 或 /")
    return word


def _whole(data, key, default, where, least=0):
    """A whole number of at least `least` from the file, or `default` when it isn't written."""
    try:
        number = int(data.get(key, default))
        if number < least:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError(f"{where} 要写不小于 {least} 的整数")
    return number


def _pool(entry, where, owner, effect):
    """The statuses a roll can land on (`from`), which only a roll may have."""
    pool = _names(entry.get("from"))
    if effect == "roll" and not pool:
        raise ValueError(f"{where}：roll 要用 from 列出可能掷出的状态，例如 from: [存活, 死亡]")
    if pool and effect != "roll":
        raise ValueError(f"{where}：只有 effect: roll 才能写 from")
    for rolled in pool:
        if rolled not in owner:
            raise ValueError(f"{where}：from 里的「{rolled}」不是 statuses 里的状态")
    return pool


def _only(entry, where, owner):
    """The statuses someone must have (`only`): each a real one, no two from the same column."""
    only, seen = _names(entry.get("only")), {}
    for wanted in only:
        if wanted not in owner:
            raise ValueError(f"{where}：only 里的「{wanted}」不是 statuses 里的状态")
        if owner[wanted] in seen:
            raise ValueError(f"{where}：only 里的「{seen[owner[wanted]]}」和「{wanted}」都在「{owner[wanted]}」"
                             f"这一栏，不可能同时满足")
        seen[owner[wanted]] = wanted
    return only


def _say(entry, where, fields, key="say"):
    """The lines to say (`say`, or a steal's `fail`), using only the {names} the program fills in."""
    say = _names(entry.get(key))
    for line in say:
        try:
            unknown = placeholders(line) - set(fields)
        except ValueError as e:
            raise ValueError(f"{where}：{key} 的花括号写得不对（{e}）")
        if unknown:
            raise ValueError(f"{where}：{key} 里的 {'、'.join('{' + u + '}' for u in sorted(unknown))} 程序不会填写，"
                             f"能用的有：{'、'.join('{' + f + '}' for f in fields)}")
    return say


def _steal(entry, where, effect):
    """(need, times, fail) of an action that throws the die to take someone else's member; any
    other action may write none of the three."""
    if effect != "steal":
        for key in ("need", "times", "fail"):
            if key in entry:
                raise ValueError(f"{where}：只有 effect: steal 才能写 {key}")
        return 0, 0, []
    need = _whole(entry, "need", NEED, f"{where}的 need", least=1)
    if need > DIE:
        raise ValueError(f"{where}的 need 最多写 {DIE}：骰子只有 {DIE} 点，写 {NEED} 就是一半的机会")
    return need, _whole(entry, "times", THROWS, f"{where}的 times", least=1), _say(entry, where, STEAL_FIELDS, "fail")


def _events(data, owner):
    """The events in teams.yaml, checked like the actions."""
    entries = data.get("events") or []
    if not isinstance(entries, list):
        raise ValueError("teams.yaml 的 events 要写成列表，每件事以「- name:」开头")
    events, taken = [], set()
    for i, entry in enumerate(entries, 1):
        where = f"teams.yaml 第 {i} 件突发事件"
        if not isinstance(entry, dict) or not str(entry.get("name") or "").strip():
            raise ValueError(f"{where}缺少 name")
        name = str(entry["name"]).strip()
        where = f"{where}（{name}）"
        if name in taken:
            raise ValueError(f"{where}：名字和前面的重复了")
        taken.add(name)
        try:
            chance = float(str(entry.get("chance")).strip().rstrip("%"))
        except ValueError:
            chance = 0.0
        if not 0 < chance <= 100:
            raise ValueError(f"{where}：chance 要写每次说话遇上它的几率，大于 0、不超过 100 的数，例如 chance: 2")
        if "effect" in entry and "set" in entry:
            raise ValueError(f"{where}：effect 和 set 只能写其中一种（都不写，这件事就只是说一句话）")
        effect = status = column = ""
        if "effect" in entry:
            effect = str(entry["effect"])
            if effect not in EVENT_EFFECTS:
                raise ValueError(f"{where}：突发事件没有叫「{effect}」的效果，可用的有：{EVENT_EFFECT_HELP}")
        elif "set" in entry:
            effect, status = "set", str(entry["set"])
            if status not in owner:
                raise ValueError(f"{where}：set 的「{status}」不是 statuses 里的状态，可用的有：{'、'.join(owner)}")
            column = owner[status]
        pool = _pool(entry, where, owner, effect)
        only = _only(entry, where, owner)
        speaker = str(entry.get("speaker") or "").strip()
        if speaker and speaker != CAPTAIN:
            raise ValueError(f"{where}：speaker 只能写「{CAPTAIN}」（{CAPTAIN}说话时，落在 TA 的一名队员身上），"
                             f"不写就是队员自己说话时遇上")
        say = _say(entry, where, EVENT_FIELDS)
        if not say:
            raise ValueError(f"{where}：say 要写，这是这件事发生时我说的话")
        events.append(Event(name=name, chance=chance, captain=speaker == CAPTAIN, effect=effect, status=status,
                            column=column, only=tuple(only), say=tuple(say), pool=tuple(pool)))
    return tuple(events)


def _hiding(data):
    """(the hiding places, searches a captain gets a day, times a member can hide a day) from `hide`."""
    raw = data.get("hide") or {}
    if not isinstance(raw, dict):
        raise ValueError("teams.yaml 的 hide 要写成几项设置：spots（躲的地方）、searches、times")
    for key in raw:
        if key not in ("spots", "searches", "times"):
            raise ValueError(f"teams.yaml 的 hide 里没有「{key}」这一项，可用的有：spots（躲的地方）、"
                             f"searches（每位队长每天能找几次）、times（每人每天能躲几次）")
    spots = _names(raw.get("spots"))
    if spots and not MIN_SPOTS <= len(spots) <= MAX_SPOTS:
        raise ValueError(f"teams.yaml 的 hide.spots 要写 {MIN_SPOTS} 到 {MAX_SPOTS} 个地方：太少一找就找到，"
                         f"太多按钮放不下")
    for spot in spots:
        if spots.count(spot) > 1:
            raise ValueError(f"teams.yaml 的 hide.spots 里「{spot}」重复了")
        if len(spot) > MAX_SPOT or any(c.isspace() or c in "@/" for c in spot):
            raise ValueError(f"teams.yaml 的 hide.spots：「{spot}」最多 {MAX_SPOT} 个字，不能带空格、@ 或 /")
    return (tuple(spots), _whole(raw, "searches", SEARCHES, "teams.yaml 的 hide.searches", least=1),
            _whole(raw, "times", HIDES, "teams.yaml 的 hide.times", least=1))


def _clock(value):
    # YAML reads an unquoted 05:00 as the base-60 number 300.
    if isinstance(value, int):
        return dtime(value // 60, value % 60)
    hour, minute = str(value).split(":")
    return dtime(int(hour), int(minute))


def load_rules(path=RULES_FILE, reserved=()):
    """Read and check teams.yaml. A mistake stops the bot at startup, naming the bad entry.

    `reserved` are the bot's other command words, like "/成员"; an action named so
    that one of them would answer it first is refused. Error messages are in
    Chinese: the file's editors read them, not developers.
    """
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError("teams.yaml 的最外层应该是「键: 值」的形式")

    raw = data.get("statuses")
    if not isinstance(raw, dict) or not raw:
        raise ValueError("teams.yaml 的 statuses 要写成「栏名: [状态1, 状态2, …]」，至少一栏")
    columns, owner = {}, {}
    for column, statuses in raw.items():
        column, statuses = str(column), _names(statuses)
        if len(statuses) < 2:
            raise ValueError(f"teams.yaml 的「{column}」这一栏至少要有两个状态：第一个是刚被抓进队时的状态，"
                             f"后面是可以改成的状态")
        for status in statuses:
            if status == CAPTAIN:
                raise ValueError(f"teams.yaml：「{CAPTAIN}」是保留字（by 里用它指本队队长），不能当状态名")
            if status in owner:
                raise ValueError(f"teams.yaml：状态「{status}」在「{owner[status]}」和「{column}」两栏里重复了，"
                                 f"状态名不能重复")
            owner[status] = column
        columns[column] = tuple(statuses)

    hidden = _names(data.get("hide_from_ai"))
    for status in hidden:
        if status not in owner:
            raise ValueError(f"teams.yaml 的 hide_from_ai 里的「{status}」不是 statuses 里的状态")

    raw = data.get("icons") or {}
    if not isinstance(raw, dict):
        raise ValueError("teams.yaml 的 icons 要写成「状态: 图标」")
    icons = {}
    for status, icon in raw.items():
        if str(status) not in owner:
            raise ValueError(f"teams.yaml 的 icons 里的「{status}」不是 statuses 里的状态")
        icons[str(status)] = str(icon)

    entries = data.get("actions") or []
    if not isinstance(entries, list):
        raise ValueError("teams.yaml 的 actions 要写成列表，每个操作以「- name:」开头")
    raw = data.get("names") or {}
    if not isinstance(raw, dict):
        raise ValueError(f"teams.yaml 的 names 要写成「叫法: 名字」，可用的有：{NAMES_HELP}")
    names = dict(NAMES)
    for key, name in raw.items():
        if key not in NAMES:
            raise ValueError(f"teams.yaml 的 names 里没有「{key}」，可用的有：{NAMES_HELP}")
        names[key] = str(name if name is not None else "").strip()
        if not names[key]:
            raise ValueError(f"teams.yaml 的 names.{key} 不能是空的")
    raw = data.get("commands") or {}
    if not isinstance(raw, dict):
        raise ValueError(f"teams.yaml 的 commands 要写成「内置指令: 名字」，可用的有：{FIXED_HELP}")
    commands = dict(FIXED_COMMANDS)
    for key, word in raw.items():
        if key not in FIXED_COMMANDS:
            raise ValueError(f"teams.yaml 的 commands 里没有「{key}」这个内置指令，可用的有：{FIXED_HELP}")
        commands[key] = _word(word, f"teams.yaml 的 commands.{key}")
    fixed = {word: key for key, word in commands.items()}
    if len(fixed) < len(commands):
        raise ValueError("teams.yaml 的 commands 里有两个内置指令用了同一个名字")
    for word in fixed:
        clash = next((r for r in reserved if f"/{word}".startswith(r)), None)
        if clash:
            raise ValueError(f"teams.yaml 的 commands：/{word} 会被机器人已有的指令 {clash} 抢先接住，换个名字吧")

    actions, taken = [], {}
    for i, entry in enumerate(entries, 1):
        where = f"teams.yaml 第 {i} 个操作"
        if not isinstance(entry, dict) or not entry.get("name"):
            raise ValueError(f"{where}缺少 name")
        name = _word(entry["name"], where)
        where = f"{where}（{name}）"
        words = [name] + [_word(alias, where) for alias in _names(entry.get("aliases"))]
        for word in words:
            if word in fixed:
                raise ValueError(f"{where}：/{word} 是{names['game']}的内置指令，换个名字吧")
            if word in taken:
                raise ValueError(f"{where}：/{word} 和「{taken[word]}」重复了")
            clash = next((r for r in reserved if f"/{word}".startswith(r)), None)
            if clash:
                raise ValueError(f"{where}：/{word} 会被机器人已有的指令 {clash} 抢先接住，换个名字吧")
            taken[word] = name

        if ("effect" in entry) == ("set" in entry):
            raise ValueError(f"{where}：effect 和 set 必须写，而且只能写其中一种")
        status = column = ""
        if "effect" in entry:
            effect = str(entry["effect"])
            if effect not in EFFECTS:
                raise ValueError(f"{where}：没有叫「{effect}」的效果，可用的有：{EFFECT_HELP}")
        else:
            effect, status = "set", str(entry["set"])
            if status not in owner:
                raise ValueError(f"{where}：set 的「{status}」不是 statuses 里的状态，可用的有：{'、'.join(owner)}")
            column = owner[status]

        pool = _pool(entry, where, owner, effect)
        if entry.get("only") and effect == "grab":
            raise ValueError(f"{where}：被抓的人还没进队，没有状态，grab 不能写 only")
        only = _only(entry, where, owner)

        by = _names(entry["by"]) if "by" in entry else [CAPTAIN]
        for who in by:
            if who != CAPTAIN and who not in owner:
                raise ValueError(f"{where}：by 里的「{who}」既不是「{CAPTAIN}」，也不是 statuses 里的状态")

        need, times, fail = _steal(entry, where, effect)
        say = _say(entry, where, STEAL_FIELDS if effect == "steal" else SAY_FIELDS)
        actions.append(Action(name=name, words=tuple(words), effect=effect, status=status, column=column,
                              only=tuple(only), by=tuple(by), say=tuple(say), pool=tuple(pool),
                              need=need, times=times, fail=tuple(fail)))

    if not any(a.effect == "grab" for a in actions):
        raise ValueError("teams.yaml 至少要有一个 effect: grab 的操作，不然队长没法抓人")

    try:
        max_members = int(data.get("max_members") or 0)
        if max_members < 0:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("teams.yaml 的 max_members 要写 0 或正整数（0 = 不限人数）")
    try:
        reset_time = _clock(data.get("reset_time", "05:00"))
    except (TypeError, ValueError):
        raise ValueError("teams.yaml 的 reset_time 要写成 \"05:00\" 这样的时间")
    spots, searches, hides = _hiding(data)
    return Rules(columns=columns, icons=icons, actions=tuple(actions), max_members=max_members,
                 reset_time=reset_time, commands=commands, hidden=tuple(hidden), names=names,
                 events=_events(data, owner),
                 event_gap=_whole(data, "event_gap", EVENT_GAP, "teams.yaml 的 event_gap"),
                 event_daily=_whole(data, "event_daily", EVENT_DAILY, "teams.yaml 的 event_daily"),
                 spots=spots, searches=searches, hides=hides)
