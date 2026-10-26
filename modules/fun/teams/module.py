"""Team games: captains grab members with /抓, then decide what happens to them.

Who the captains are, and which groups play, is set in config.yaml (teams:).
The game itself -- the statuses a member can have and every action a captain
can take -- lives in prompts/teams.yaml, so new actions and statuses need no
code. Grabbed members can't leave on their own; their captain, or an admin
acting as referee, decides. Teams last one game day.

Every line comes from the style files via t(), except each action's own
announcement, which sits next to the action in teams.yaml.
"""
import asyncio
import random
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
import pytz
from botpy import logging
from core.config import root_path
from core.style import join, render, t
from modules.fun.teams.roster import GroupTeams, Member, Roster, Team
from modules.fun.teams.rules import CAPTAIN, RULES_FILE, Action, load_rules


_log = logging.get_logger()

DATA_FILE = root_path("data", "teams.json")
# The command word: "/抓" out of "/抓 @小明" or "/抓@小明".
COMMAND_RE = re.compile(r"/[^\s@/]+")
MAX_MESSAGE_LENGTH = 900


class Refused(Exception):
    """The action can't be used here; the message says why, in the current style."""


@dataclass
class Turn:
    """One use of an action: who is acting, with what, on which group's teams."""
    actor: str
    actor_name: str
    admin: bool
    action: Action
    teams: GroupTeams
    now: int
    team: Team = None       # grab only: the team people are grabbed into
    rolled: dict = None     # roll only: member openid -> the status it landed on


class TeamsModule:
    def __init__(self, gateway, config=None, is_admin=None, nickname_of=None, reserved=(),
                 rules_path=RULES_FILE, data_path=DATA_FILE, clock=None, rng=None):
        cfg = (config or {}).get("teams") or {}
        self.captains = {str(c) for c in cfg.get("captains") or []}
        self.groups = {str(g) for g in cfg.get("groups") or []}     # empty: every group
        self.gw = gateway
        self.is_admin = is_admin or (lambda group, member, role=None: False)
        # Falls back on the nickname the group manager last saw, for @mentions that arrive without one.
        self.nickname_of = nickname_of or (lambda group, member: None)
        self.tz = pytz.timezone('Asia/Shanghai')
        self.clock = clock or (lambda: datetime.now(self.tz))
        self.task = None
        self.rng = rng or random.Random()
        self.rules = self.roster = None
        if self.enabled:
            self.rules = load_rules(rules_path, reserved)
            self.roster = Roster(data_path, self.rules)
        self.fixed = {}         # "/队伍" -> handler; the words can be renamed in teams.yaml
        if self.enabled:
            handlers = {"teams": self.cmd_teams, "disband": self.cmd_disband, "clear": self.cmd_clear}
            self.fixed = {f"/{word}": handlers[key] for key, word in self.rules.commands.items()}

    @property
    def enabled(self):
        return bool(self.captains)

    # ------------------------------------------------------------------ helpers

    def _day(self):
        """Today's game day. It starts at reset_time, so a game running past midnight stays on one day."""
        reset = self.rules.reset_time
        local = self.clock().astimezone(self.tz)
        return (local - timedelta(hours=reset.hour, minutes=reset.minute)).date()

    def _name(self, group, openid, nickname=None):
        return nickname or self.nickname_of(group, openid) or t("games.teams.unnamed", tag=openid[-4:])

    def _targets(self, message):
        """[(openid, name)] of everyone @-ed in the message, once each, in order."""
        found = {}
        for openid, nickname in message.mentioned_members:
            if openid not in found:
                found[openid] = self._name(message.group_openid, openid, nickname)
        return list(found.items())

    def _has_any(self, member, statuses):
        return any(member.status.get(self.rules.column_of(s)) == s for s in statuses if s != CAPTAIN)

    def _denied(self, action):
        who = list(action.by)
        if action.effect != "grab":         # admins referee every action except grabbing
            who.append(t("games.teams.admins"))
        return t("games.teams.denied", who=join(who), action=action.name)

    def _split(self, text):
        chunks, current = [], ""
        for line in text.split("\n"):
            if current and len(current) + len(line) + 1 > MAX_MESSAGE_LENGTH:
                chunks.append(current)
                current = line
            else:
                current = f"{current}\n{line}" if current else line
        if current:
            chunks.append(current)
        return chunks

    async def _reply(self, message, text, buttons=None):
        try:
            chunks = self._split(text)
            for i, chunk in enumerate(chunks):
                await self.gw.send(message.group_openid, chunk, reply_to=message.id,
                                   buttons=buttons if i == len(chunks) - 1 else None)
        except Exception as e:
            _log.error(f"[Teams] 回复失败: {e}")

    def help_block(self):
        """The 【组队游戏】 part of /帮助, listing the actions in teams.yaml."""
        grab = " ".join(f"/{a.name}" for a in self.rules.actions if a.effect == "grab")
        others = " ".join(f"/{a.name}" for a in self.rules.actions if a.effect != "grab")
        return t("games.teams.help", grab=grab, actions=others, reset=self.rules.reset_time.strftime("%H:%M"),
                 **self._fixed_words())

    def _fixed_words(self):
        """{teams}, {disband}, {clear}: the built-in commands as this group types them, e.g. "/队伍"."""
        return {key: f"/{word}" for key, word in self.rules.commands.items()}

    # ------------------------------------------------------------------ commands

    async def handle_teams(self, message):
        if not self.enabled:
            return False
        match = COMMAND_RE.match((message.content or "").strip())
        if not match:
            return False
        word = match.group(0)
        handler = self.fixed.get(word)
        action = None if handler else self.rules.action(word[1:])
        if handler is None and action is None:
            return False
        if self.groups and message.group_openid not in self.groups:
            await self._reply(message, t("games.teams.not_here"))
            return True
        try:
            teams = self.roster.today(message.group_openid, self._day())
            if handler is not None:
                await handler(message, teams)
            else:
                await self.use(message, teams, action)
        except Exception as e:
            _log.error(f"[Teams] 指令 {word} 出错: {e}")
            await self._reply(message, t("common.error", command=word, error=e))
        return True

    async def use(self, message, teams, action):
        """An action from teams.yaml -- /抓, /踢出, /死亡 … -- on everyone @-ed in the message."""
        group, actor = message.group_openid, message.member_openid
        if actor in teams.teams and message.nickname:
            teams.teams[actor].name = message.nickname
        turn = Turn(actor, self._name(group, actor, message.nickname), self.is_admin(group, actor, message.role),
                    action, teams, int(self.clock().timestamp()))
        try:
            if action.effect == "grab":
                turn.team = self._grabbing_team(turn)
            elif not turn.admin and not self._may_use(turn):
                raise Refused(self._denied(action))
        except Refused as e:
            await self._reply(message, str(e))
            return
        targets = self._targets(message)
        if not targets:
            await self._reply(message, t("games.teams.usage", action=action.name))
            return

        effect = getattr(self, f"effect_{action.effect}")
        turn.rolled = {}
        done, problems = [], []     # done: (name, their captain's name, the status they now have)
        for openid, name in targets:
            try:
                captain = effect(turn, openid, name).name
                done.append((name, captain, turn.rolled.get(openid, action.status)))
            except Refused as e:
                problems.append(str(e))
        lines = []
        if done:
            self.roster.save()
            # A roll lands differently for each member, so each gets a line of their own.
            groups = [[d] for d in done] if action.effect == "roll" else [done]
            for group in groups:
                said = dict(actor=turn.actor_name, members=join([name for name, _, _ in group]), count=len(group),
                            captain=group[0][1], action=action.name, status=group[0][2])
                generic = "games.teams.rolled" if action.effect == "roll" else "games.teams.done"
                lines.append(render(action.say, **said) if action.say else t(generic, **said))
        await self._reply(message, "\n".join(lines + problems))

    def _grabbing_team(self, turn):
        """The team /抓 adds people to: the one the actor leads, or a new one if they're a captain."""
        actor, action, teams = turn.actor, turn.action, turn.teams
        if actor in teams.teams and CAPTAIN in action.by:
            return teams.teams[actor]
        team = teams.team_of(actor)
        if team is not None:
            # Allowed by status, e.g. by: [队长, 小队长]: grabs for the team they're in.
            if self._has_any(team.members[actor], action.by):
                return team
            if actor in self.captains:
                raise Refused(t("games.teams.in_a_team", captain=team.name))
        elif actor in self.captains and CAPTAIN in action.by:
            return Team(actor, turn.actor_name)     # joins the roster with its first member
        raise Refused(self._denied(action))

    def _may_use(self, turn):
        """Whether the actor could use this action on anybody at all."""
        by = turn.action.by
        if CAPTAIN in by and (turn.actor in self.captains or turn.actor in turn.teams.teams):
            return True
        team = turn.teams.team_of(turn.actor)
        return team is not None and self._has_any(team.members[turn.actor], by)

    def _commands(self, turn, team):
        """Whether the actor may use this action on the members of `team`."""
        if CAPTAIN in turn.action.by and team.captain == turn.actor:
            return True
        me = team.members.get(turn.actor)
        return me is not None and self._has_any(me, turn.action.by)

    def _target(self, turn, openid, name):
        """(team, member) for someone the action may touch."""
        if openid in turn.teams.teams:
            raise Refused(t("games.teams.fail.captain", member=name))
        team = turn.teams.team_of(openid)
        if team is None:
            raise Refused(t("games.teams.fail.free" if turn.admin else "games.teams.fail.not_yours", member=name))
        if not turn.admin and not self._commands(turn, team):
            raise Refused(t("games.teams.fail.not_yours", member=name))
        member = team.members[openid]
        for status in turn.action.only:
            if member.status.get(self.rules.column_of(status)) != status:
                raise Refused(t("games.teams.fail.needs", member=name, status=status, action=turn.action.name))
        return team, member

    # Effects: each takes one target and returns the team it happened in, or raises Refused.

    def effect_grab(self, turn, openid, name):
        if openid == turn.actor:
            raise Refused(t("games.teams.fail.self"))
        if openid in turn.teams.teams:
            raise Refused(t("games.teams.fail.captain", member=name))
        current = turn.teams.team_of(openid)
        if current is not None:
            if current.captain == turn.team.captain:
                raise Refused(t("games.teams.fail.mine", member=name))
            raise Refused(t("games.teams.fail.taken", member=name, captain=current.name))
        limit = self.rules.max_members
        if limit and len(turn.team.members) >= limit:
            raise Refused(t("games.teams.fail.full", member=name, max=limit))
        turn.team.members[openid] = Member(openid, name, self.rules.starting(), turn.now)
        turn.teams.teams.setdefault(turn.team.captain, turn.team)
        return turn.team

    def effect_kick(self, turn, openid, name):
        team, _ = self._target(turn, openid, name)
        del team.members[openid]
        if not team.members:
            del turn.teams.teams[team.captain]
        return team

    def effect_roll(self, turn, openid, name):
        team, member = self._target(turn, openid, name)
        status = self.rng.choice(turn.action.pool)
        member.status[self.rules.column_of(status)] = status
        turn.rolled[openid] = status
        return team

    def effect_set(self, turn, openid, name):
        team, member = self._target(turn, openid, name)
        action = turn.action
        if member.status.get(action.column) == action.status:
            raise Refused(t("games.teams.fail.already", member=name, status=action.status))
        member.status[action.column] = action.status
        return team

    # ---- fixed commands ----

    async def cmd_teams(self, message, teams):
        """/队伍: every team, with each member's statuses."""
        if not teams.teams:
            grab = next(f"/{a.name}" for a in self.rules.actions if a.effect == "grab")
            await self._reply(message, t("games.teams.none", grab=grab))
            return
        day = self._day()
        lines, shown = [t("games.teams.title", date=t("common.date", month=day.month, day=day.day))], set()
        for team in teams.teams.values():
            members = join([self._label(m, shown) for m in team.members.values()])
            lines.append(t("games.teams.team", captain=team.name, count=len(team.members), members=members))
        legend = [f"{icon}{status}" for status, icon in self.rules.icons.items() if status in shown]
        if legend:
            lines.append(t("games.teams.legend", legend=" ".join(legend)))
        grab = next(a.name for a in self.rules.actions if a.effect == "grab")
        await self._reply(message, "\n".join(lines), buttons=[[
            ("刷新", f"/{self.rules.commands['teams']}"), (grab, f"/{grab} ")]])

    def today_for_ai(self, group):
        """Today's teams for AI chat, as [(captain, [(member, [statuses])])], leaving out statuses
        listed under hide_from_ai in teams.yaml. Reads only: an earlier day's teams count as none."""
        if not self.enabled:
            return []
        teams = self.roster.groups.get(group)
        if teams is None or teams.day != self._day().isoformat():
            return []
        found = []
        for team in teams.teams.values():
            members = [(m.name, [s for s in self.rules.tidy(m.status).values() if s not in self.rules.hidden])
                       for m in team.members.values()]
            if members:
                found.append((team.name, members))
        return found

    def _label(self, member, shown):
        """A member's name with their statuses: icons in front, statuses without one in brackets after."""
        icons, plain = "", []
        for column, statuses in self.rules.columns.items():
            status = member.status.get(column, statuses[0])
            if status == statuses[0]:
                continue
            if status in self.rules.icons:
                icons += self.rules.icons[status]
                shown.add(status)
            else:
                plain.append(status)
        label = icons + member.name
        return t("games.teams.tagged", member=label, statuses=join(plain)) if plain else label

    async def cmd_disband(self, message, teams):
        """/解散队伍: a captain lets their whole team go; an admin can name captains with @."""
        group, actor = message.group_openid, message.member_openid
        admin = self.is_admin(group, actor, message.role)
        named = [(openid, name) for openid, name in self._targets(message) if openid != actor]
        if named and not admin:
            await self._reply(message, t("games.teams.disband_others", **self._fixed_words()))
            return
        if not named:
            if actor not in teams.teams:
                key = ("disband_usage" if admin else "no_own_team" if actor in self.captains else "disband_denied")
                await self._reply(message, t(f"games.teams.{key}", **self._fixed_words()))
                return
            named = [(actor, teams.teams[actor].name)]
        lines = []
        for openid, name in named:
            team = teams.teams.pop(openid, None)
            if team is None:
                lines.append(t("games.teams.no_team", member=name))
            else:
                lines.append(t("games.teams.disbanded", captain=team.name, count=len(team.members)))
        self.roster.save()
        await self._reply(message, "\n".join(lines))

    async def cmd_clear(self, message, teams):
        """/清空队伍: admins end every team in the group at once."""
        if not self.is_admin(message.group_openid, message.member_openid, message.role):
            await self._reply(message, t("common.not_admin"))
            return
        count = len(teams.teams)
        if not count:
            await self._reply(message, t("games.teams.cleared_none"))
            return
        teams.teams.clear()
        self.roster.save()
        await self._reply(message, t("games.teams.cleared", count=count))

    # ------------------------------------------------------------------ housekeeping

    def forget_group(self, group):
        if self.enabled:
            self.roster.forget_group(group)

    def forget_member(self, group, member):
        if self.enabled:
            self.roster.forget_member(group, member)

    async def run_scheduler(self):
        """Clear yesterday's teams at reset_time, even in groups nobody plays in today."""
        if not self.enabled:
            return
        where = f"{len(self.groups)} 个群" if self.groups else "所有群"
        _log.info(f"[Teams] 组队游戏已开启：{len(self.captains)} 位队长，{where}，"
                  f"{len(self.rules.actions)} 个操作，每天 {self.rules.reset_time.strftime('%H:%M')} 清空队伍")
        while True:
            try:
                cleared = self.roster.reset_stale(self._day())
                if cleared:
                    _log.info(f"[Teams] 新的一天，已清空 {cleared} 个群的队伍")
            except Exception as e:
                _log.error(f"[Teams] 清空队伍出错: {e}")
            await asyncio.sleep(60)
