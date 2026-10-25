"""The team game's rules, read from prompts/teams.yaml: what statuses a team
member can have, and every action captains can use on their members.

A new status or a new action is a few lines of YAML. Only a new kind of
effect needs code: add its name to EFFECTS below and an effect_<name> method
to TeamsModule.
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
EFFECTS = ("grab", "kick", "roll")
EFFECT_HELP = "grab（把人抓进队）、kick（移出队伍）、roll（从 from 里随机掷一个状态）"
# The game's built-in commands and their default words. teams.yaml may rename
# them under `commands:`; actions can't take these words.
FIXED_COMMANDS = {"teams": "队伍", "disband": "解散队伍", "clear": "清空队伍"}
FIXED_HELP = "teams（查看队伍）、disband（解散队伍）、clear（清空队伍）"
# What an action's `say` line may use; {name} is the style's name.
SAY_FIELDS = ("actor", "members", "count", "captain", "action", "status", "name")


@dataclass(frozen=True)
class Action:
    name: str
    words: tuple            # what people type after "/": the name, then any aliases
    effect: str             # "grab", "kick" or "set"
    status: str = ""        # set: the status it gives
    column: str = ""        # set: the column that status belongs to
    only: tuple = ()        # the target must have every one of these statuses
    by: tuple = (CAPTAIN,)  # who may use it besides admins: CAPTAIN and/or statuses
    say: tuple = ()         # lines to pick from afterwards; empty = the style's generic line
    pool: tuple = ()        # roll: the statuses a roll can land on


@dataclass(frozen=True)
class Rules:
    columns: dict           # column -> its statuses; the first is everyone's status when grabbed
    icons: dict             # status -> icon shown in front of names in /队伍
    actions: tuple
    max_members: int = 0    # per team, not counting the captain; 0 = no limit
    reset_time: dtime = dtime(5, 0)
    commands: dict = None   # built-in command -> its word, see FIXED_COMMANDS

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
                raise ValueError(f"{where}：/{word} 是组队游戏的内置指令，换个名字吧")
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

        pool = _names(entry.get("from"))
        if effect == "roll" and not pool:
            raise ValueError(f"{where}：roll 要用 from 列出可能掷出的状态，例如 from: [存活, 死亡]")
        if pool and effect != "roll":
            raise ValueError(f"{where}：只有 effect: roll 才能写 from")
        for rolled in pool:
            if rolled not in owner:
                raise ValueError(f"{where}：from 里的「{rolled}」不是 statuses 里的状态")

        only = _names(entry.get("only"))
        if only and effect == "grab":
            raise ValueError(f"{where}：被抓的人还没进队，没有状态，grab 不能写 only")
        seen = {}
        for wanted in only:
            if wanted not in owner:
                raise ValueError(f"{where}：only 里的「{wanted}」不是 statuses 里的状态")
            if owner[wanted] in seen:
                raise ValueError(f"{where}：only 里的「{seen[owner[wanted]]}」和「{wanted}」都在「{owner[wanted]}」"
                                 f"这一栏，不可能同时满足")
            seen[owner[wanted]] = wanted

        by = _names(entry["by"]) if "by" in entry else [CAPTAIN]
        for who in by:
            if who != CAPTAIN and who not in owner:
                raise ValueError(f"{where}：by 里的「{who}」既不是「{CAPTAIN}」，也不是 statuses 里的状态")

        say = _names(entry.get("say"))
        for line in say:
            try:
                unknown = placeholders(line) - set(SAY_FIELDS)
            except ValueError as e:
                raise ValueError(f"{where}：say 的花括号写得不对（{e}）")
            if unknown:
                raise ValueError(f"{where}：say 里的 {'、'.join('{' + u + '}' for u in sorted(unknown))} 程序不会填写，"
                                 f"能用的有：{'、'.join('{' + f + '}' for f in SAY_FIELDS)}")

        actions.append(Action(name=name, words=tuple(words), effect=effect, status=status, column=column,
                              only=tuple(only), by=tuple(by), say=tuple(say), pool=tuple(pool)))

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
    return Rules(columns=columns, icons=icons, actions=tuple(actions), max_members=max_members,
                 reset_time=reset_time, commands=commands)
