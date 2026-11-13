"""Team games: captains grab members with /抓, then decide what happens to them.

Who the captains are, and which groups play, is set in config.yaml (teams:).
The game itself -- the statuses a member can have and every action a captain
can take -- lives in prompts/teams.yaml, so new actions and statuses need no
code. Grabbed members can't leave on their own; their captain, or an admin
acting as referee, decides. Teams last one game day.

Three things from the same file give the caught something to do, and the game a memory:
- hide-and-seek: someone who isn't on a team hides (/躲). The bot picks the place and tells nobody,
  as a command sent in a group can't be secret. A captain can't grab them, only look in one place
  (/找 地点), a few times a day; whoever hides there joins their team.
- events: while the group chats, something from the file's list may befall a team member who speaks,
  or, when it's their captain who speaks, one of that captain's members.
- records: counts of what has happened to each member across game days (/档案), kept by history.py.

Every line comes from the style files via t(), except each action's and each event's own
announcement, which sits next to it in teams.yaml.
"""
import asyncio
import random
import re
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta
import pytz
from botpy import logging
from core.config import root_path
from core.style import join, render, t
from modules.fun.teams.history import STATUS, History
from modules.fun.teams.roster import GroupTeams, Hider, Member, Roster, Team
from modules.fun.teams.rules import CAPTAIN, RULES_FILE, Action, load_rules


_log = logging.get_logger()

DATA_FILE = root_path("data", "teams.json")
HISTORY_FILE = root_path("data", "teams.db")
COME_OUT_WORDS = ("出来", "不躲了")
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
                 rules_path=RULES_FILE, data_path=DATA_FILE, clock=None, rng=None, count=None, history=None):
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
        # (group, member_openid, nickname, counter) -> congratulations for any badge that earned, from
        # the group manager's achievements: being caught, grabbing, and hiding a whole day each count.
        self.count = count or (lambda group, member, nickname, key: [])
        self.rules = self.roster = self.history = None
        if self.enabled:
            self.rules = load_rules(rules_path, reserved)
            self.roster = Roster(data_path, self.rules)
            self.history = history or History(HISTORY_FILE)
        self.fixed = {}         # "/队伍" -> handler; the words can be renamed in teams.yaml
        if self.enabled:
            handlers = {"teams": self.cmd_teams, "disband": self.cmd_disband, "clear": self.cmd_clear,
                        "hide": self.cmd_hide, "seek": self.cmd_seek, "record": self.cmd_record}
            self.fixed = {f"/{word}": handlers[key] for key, word in self.rules.commands.items()}
        self.event_at = {}      # group -> when its last event happened
        self.event_count = {}   # (group, member, game day) -> events they've met that day
        self.observed = OrderedDict()   # ids of the latest messages observe() has seen: an @ arrives twice

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
        """The 【组队游戏】 part of /帮助, listing the actions in teams.yaml; then a line each for
        hide-and-seek and events, when the file has them, and one for the records."""
        grab = " ".join(f"/{a.name}" for a in self.rules.actions if a.effect == "grab")
        others = " ".join(f"/{a.name}" for a in self.rules.actions if a.effect != "grab")
        words = self._fixed_words()
        lines = [t("games.teams.help", grab=grab, actions=others, reset=self.rules.reset_time.strftime("%H:%M"),
                   **words)]
        if self.rules.spots:
            lines.append(t("games.teams.help_hide", **words))
        if self.rules.events:
            lines.append(t("games.teams.help_events", **words))
        lines.append(t("games.teams.help_record", **words))
        return "\n".join(lines)

    def _fixed_words(self):
        """{teams}, {disband}, {clear}, {hide}, {seek}, {record}: the built-in commands as this group
        types them, e.g. "/队伍"."""
        return {key: f"/{word}" for key, word in self.rules.commands.items()}

    def _note(self, group, member, name, what, other="", other_name="", counter=None):
        """One more of something in a member's record, with whom when that matters. With `counter` it
        also counts toward a badge: returns the congratulations to add to the reply if that earned one."""
        self.history.add(group, member, what, other, other_name)
        return self.count(group, member, name, counter) if counter else []

    def _joined(self, group, member, name, actor, actor_name):
        """Someone was caught, by a grab or by being found: both records, and the badges it may earn."""
        return (self._note(group, member, name, "caught", actor, actor_name, counter="caught")
                + self._note(group, actor, actor_name, "grabbed", member, name, counter="grabbed"))

    def _close_days(self):
        """A game day that has ended is put away: whoever was still hiding has slipped through it,
        which goes in their record, and its teams are cleared. Returns how many groups had a game."""
        today = self._day()
        for group, old in self.roster.stale(today).items():
            for openid, hider in old.hiding.items():
                self._note(group, openid, hider.name, "slipped", counter="slipped")
        return self.roster.reset_stale(today)

    def _today(self, group):
        """The group's game today, yesterday's put away first."""
        self._close_days()
        return self.roster.today(group, self._day())

    # ------------------------------------------------------------------ commands

    async def handle_teams(self, message):
        if not self.enabled:
            return False
        match = COMMAND_RE.match((message.content or "").strip())
        if not match:
            return False
        word, rest = match.group(0), (message.content or "").strip()[match.end():].strip()
        handler = self.fixed.get(word)
        seek = f"/{self.rules.commands['seek']}"
        if handler is None and word.startswith(seek) and word[len(seek):] in self.rules.spots:
            handler, rest = self.fixed[seek], word[len(seek):]      # "/找柜子里" without a space
        action = None if handler else self.rules.action(word[1:])
        if handler is None and action is None:
            return False
        if self.groups and message.group_openid not in self.groups:
            await self._reply(message, t("games.teams.not_here"))
            return True
        try:
            teams = self._today(message.group_openid)
            if handler is not None:
                await handler(message, teams, rest)
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
        done, problems, earned = [], [], []     # done: (name, their captain's name, the status they now have)
        for openid, name in targets:
            try:
                captain = effect(turn, openid, name).name
                status = turn.rolled.get(openid, action.status)
                done.append((name, captain, status))
                if action.effect == "grab":
                    earned += self._joined(group, openid, name, actor, turn.actor_name)
                elif status:
                    self._note(group, openid, name, STATUS + status)
            except Refused as e:
                problems.append(str(e))
        lines = []
        if done:
            if action.effect == "grab":
                teams.hiding.pop(actor, None)       # a captain who was hiding has shown themself
            self.roster.save()
            # A roll lands differently for each member, so each gets a line of their own.
            groups = [[d] for d in done] if action.effect == "roll" else [done]
            for group in groups:
                said = dict(actor=turn.actor_name, members=join([name for name, _, _ in group]), count=len(group),
                            captain=group[0][1], action=action.name, status=group[0][2])
                generic = "games.teams.rolled" if action.effect == "roll" else "games.teams.done"
                lines.append(render(action.say, **said) if action.say else t(generic, **said))
        await self._reply(message, "\n".join(lines + problems + earned))

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
        if openid in turn.teams.hiding:         # hidden: a captain has to look for them
            raise Refused(t("games.teams.fail.hidden", member=name, **self._fixed_words()))
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

    async def cmd_teams(self, message, teams, args=""):
        """/队伍: every team, with each member's statuses, and who is hiding (not where)."""
        hiding = [t("games.teams.hiding", count=len(teams.hiding),
                    members=join([hider.name for hider in teams.hiding.values()]))] if teams.hiding else []
        if not teams.teams:
            grab = next(f"/{a.name}" for a in self.rules.actions if a.effect == "grab")
            await self._reply(message, "\n".join([t("games.teams.none", grab=grab)] + hiding))
            return
        day = self._day()
        lines, shown = [t("games.teams.title", date=t("common.date", month=day.month, day=day.day))], set()
        for team in teams.teams.values():
            members = join([self._label(m, shown) for m in team.members.values()])
            lines.append(t("games.teams.team", captain=team.name, count=len(team.members), members=members))
        lines += hiding
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

    async def cmd_disband(self, message, teams, args=""):
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

    async def cmd_clear(self, message, teams, args=""):
        """/清空队伍: admins end every team in the group at once, and bring out whoever is hiding."""
        if not self.is_admin(message.group_openid, message.member_openid, message.role):
            await self._reply(message, t("common.not_admin"))
            return
        count = len(teams.teams)
        if not count and not teams.hiding:
            await self._reply(message, t("games.teams.cleared_none"))
            return
        teams.teams.clear()
        teams.hiding.clear()
        self.roster.save()
        await self._reply(message, t("games.teams.cleared", count=count))

    # ---- hide-and-seek ----

    async def cmd_hide(self, message, teams, args=""):
        """/躲: someone who isn't on a team hides. The bot picks where and tells nobody, them
        included: a command sent in the group can't be a secret. /躲 出来 comes back out."""
        group, actor = message.group_openid, message.member_openid
        name, rules, words = self._name(group, actor, message.nickname), self.rules, self._fixed_words()
        team = teams.team_of(actor)
        if not rules.spots:
            await self._reply(message, t("games.teams.hide.disabled"))
        elif args in COME_OUT_WORDS:
            hider = teams.hiding.pop(actor, None)
            self.roster.save()
            await self._reply(message, t("games.teams.hide.out", member=name) if hider else t("games.teams.hide.not_hiding"))
        elif actor in teams.teams:
            await self._reply(message, t("games.teams.hide.leading"))
        elif team is not None:
            await self._reply(message, t("games.teams.hide.caught", captain=team.name))
        elif actor in teams.hiding:
            await self._reply(message, t("games.teams.hide.already", **words))
        elif teams.hides.get(actor, 0) >= rules.hides:
            await self._reply(message, t("games.teams.hide.times", max=rules.hides))
        else:
            teams.hiding[actor] = Hider(name, self.rng.choice(rules.spots), int(self.clock().timestamp()))
            teams.hides[actor] = teams.hides.get(actor, 0) + 1
            self.roster.save()
            self._note(group, actor, name, "hid")
            await self._reply(message, t("games.teams.hide.done", member=name, count=len(rules.spots), **words))

    def _seek_buttons(self):
        """A button for every hiding place, five to a row; tapping one looks there."""
        seek, spots = self.rules.commands["seek"], self.rules.spots
        return [[(spot, f"/{seek} {spot}") for spot in spots[i:i + 5]] for i in range(0, len(spots), 5)]

    async def cmd_seek(self, message, teams, args=""):
        """/找 地点: a captain looks in one hiding place, a few times a day. Everyone hiding there
        joins their team, as if grabbed; a full team leaves the rest where they are, unseen."""
        group, actor = message.group_openid, message.member_openid
        name, rules, words = self._name(group, actor, message.nickname), self.rules, self._fixed_words()
        used, sitting = teams.searches.get(actor, 0), teams.team_of(actor)
        team = teams.teams.get(actor) or Team(actor, name)
        limit = rules.max_members
        spots = join(list(rules.spots))
        if not rules.spots:
            await self._reply(message, t("games.teams.hide.disabled"))
        elif actor not in self.captains and actor not in teams.teams:
            await self._reply(message, t("games.teams.seek.denied", **words))
        elif sitting is not None:
            await self._reply(message, t("games.teams.in_a_team", captain=sitting.name))
        elif not args:
            await self._reply(message, t("games.teams.seek.usage", hiding=len(teams.hiding), left=max(0, rules.searches - used),
                                         spots=spots, **words), buttons=self._seek_buttons())
        elif args not in rules.spots:
            await self._reply(message, t("games.teams.seek.no_such", spot=args, spots=spots), buttons=self._seek_buttons())
        elif not teams.hiding:
            await self._reply(message, t("games.teams.seek.nobody"))
        elif used >= rules.searches:
            await self._reply(message, t("games.teams.seek.used_up", max=rules.searches))
        elif limit and len(team.members) >= limit:
            await self._reply(message, t("games.teams.seek.full", max=limit))
        else:
            teams.searches[actor] = used + 1
            teams.hiding.pop(actor, None)           # a captain who was hiding has shown themself
            now, left, found, earned = int(self.clock().timestamp()), rules.searches - used - 1, [], []
            for openid, hider in list(teams.hiding.items()):
                if hider.spot == args and not (limit and len(team.members) >= limit):
                    del teams.hiding[openid]
                    team.members[openid] = Member(openid, hider.name, rules.starting(), now)
                    found.append(hider.name)
                    self._note(group, openid, hider.name, "found", actor, name)
                    self._note(group, actor, name, "finds", openid, hider.name)
                    earned += self._joined(group, openid, hider.name, actor, name)
            if found:
                teams.teams.setdefault(actor, team)
            self.roster.save()
            said = dict(actor=name, spot=args, left=left)
            line = t("games.teams.seek.found", members=join(found), count=len(found), **said) if found \
                else t("games.teams.seek.nothing", **said)
            await self._reply(message, "\n".join([line] + earned),
                              buttons=self._seek_buttons() if left and teams.hiding else None)

    # ---- records ----

    async def cmd_record(self, message, teams, args=""):
        """/档案, or /档案 @某人: what has happened to someone in the game across game days."""
        group = message.group_openid
        targets = self._targets(message)
        openid, name = targets[0] if targets else (message.member_openid,
                                                   self._name(group, message.member_openid, message.nickname))
        totals = self.history.totals(group, openid)
        if not totals:
            await self._reply(message, t("games.teams.record.empty", member=name))
            return

        def line(what, most):
            """A count with whoever it was most often with after it, when the record says."""
            text = t(f"games.teams.record.{what}", count=totals[what])
            top = self.history.most(group, openid, what)
            if not top:
                return text
            # The name the group manager has for them now, else the one they had when it last happened.
            return text + t(f"games.teams.record.{most}", count=top[1],
                            member=self._name(group, top[0], self.nickname_of(group, top[0]) or top[2]))

        lines = [t("games.teams.record.title", member=name)]
        if totals.get("caught"):
            lines.append(line("caught", "caught_by"))
        if totals.get("grabbed"):
            lines.append(line("grabbed", "grabbed_most"))
        # Every status but each column's first, which is only where everyone starts, in the file's order.
        been = [t("games.teams.record.status_item", status=status, count=totals[STATUS + status])
                for statuses in self.rules.columns.values() for status in statuses[1:] if totals.get(STATUS + status)]
        if been:
            lines.append(t("games.teams.record.statuses", list=join(been)))
        if totals.get("hid"):
            lines.append(t("games.teams.record.hid", hid=totals["hid"], found=totals.get("found", 0),
                           slipped=totals.get("slipped", 0)))
        if totals.get("finds"):
            lines.append(t("games.teams.record.finds", count=totals["finds"]))
        if totals.get("event"):
            lines.append(t("games.teams.record.events", count=totals["event"]))
        await self._reply(message, "\n".join(lines))

    # ---- events ----

    def _can_meet(self, event, member, group, day):
        """Whether an event can befall this team member now: they have the statuses it asks for, it
        would change something, and they haven't met their day's worth."""
        daily = self.rules.event_daily
        if daily and self.event_count.get((group, member.openid, day), 0) >= daily:
            return False
        if any(member.status.get(self.rules.column_of(status)) != status for status in event.only):
            return False
        return event.effect != "set" or member.status.get(event.column) != event.status

    async def observe(self, message):
        """Any message in a group that plays, commands aside. When whoever spoke is on a team, one of
        the file's events may befall them; when they lead one, one of the captain's events may befall
        one of their members. Each event has its own chance, a group gets one every event_gap minutes
        at most, and a member event_daily a day. The reply is the event's own line."""
        if not self.enabled or not self.rules.events or getattr(message, "is_bot", False):
            return
        group, speaker = message.group_openid, message.member_openid
        if not speaker or (self.groups and group not in self.groups):
            return
        if (message.content or "").strip().startswith(("/", "／")) or message.id in self.observed:
            return
        self.observed[message.id] = True
        while len(self.observed) > 500:
            self.observed.popitem(last=False)
        now, day = int(self.clock().timestamp()), self._day().isoformat()
        teams = self.roster.groups.get(group)
        if teams is None or teams.day != day or now - self.event_at.get(group, 0) < self.rules.event_gap * 60:
            return
        leading, team = speaker in teams.teams, teams.teams.get(speaker) or teams.team_of(speaker)
        if team is None:
            return
        # Each event that could happen now, with whom it could happen to.
        possible = []
        for event in self.rules.events:
            if event.captain == leading:
                who = [m for m in (team.members.values() if leading else [team.members[speaker]])
                       if self._can_meet(event, m, group, day)]
                if who:
                    possible.append((event, who))
        roll, reached = self.rng.random() * 100, 0.0
        for event, who in possible:
            reached += event.chance
            if roll < reached:
                await self._befall(message, teams, team, event, self.rng.choice(who), now, day)
                return

    async def _befall(self, message, teams, team, event, member, now, day):
        """An event happens to a team member: what it does, their record, and its line."""
        group, status = message.group_openid, event.status
        if event.effect == "kick":
            del team.members[member.openid]
            if not team.members:
                del teams.teams[team.captain]
        elif event.effect == "roll":
            status = self.rng.choice(event.pool)
            member.status[self.rules.column_of(status)] = status
        elif event.effect == "set":
            member.status[event.column] = status
        self.roster.save()
        self.event_at[group] = now
        self.event_count[(group, member.openid, day)] = self.event_count.get((group, member.openid, day), 0) + 1
        self.event_count = {key: n for key, n in self.event_count.items() if key[2] == day}
        self._note(group, member.openid, member.name, "event", event.name)
        if status:
            self._note(group, member.openid, member.name, STATUS + status)
        _log.info(f"[Teams] 突发事件「{event.name}」")       # not whom: the log outlives the game day
        await self._reply(message, render(event.say, members=member.name, captain=team.name, status=status))

    # ------------------------------------------------------------------ housekeeping

    def forget_group(self, group):
        if self.enabled:
            self.roster.forget_group(group)
            self.history.forget_group(group)
            self.event_at.pop(group, None)
            self.event_count = {key: n for key, n in self.event_count.items() if key[0] != group}

    def forget_member(self, group, member):
        """Out of today's game: their team place, the team they lead, their hiding place. Their record
        stays, for when someone has only left the group and may come back."""
        if self.enabled:
            self.roster.forget_member(group, member)

    def forget_record(self, group, member):
        """Their record across game days, and their part in other members': on request, or once
        they've been gone long enough."""
        if self.enabled:
            self.history.forget_member(group, member)

    async def run_scheduler(self):
        """Clear yesterday's teams at reset_time, even in groups nobody plays in today."""
        if not self.enabled:
            return
        names = self.rules.names        # the game's own words for itself, from teams.yaml
        where = f"{len(self.groups)} 个群" if self.groups else "所有群"
        _log.info(f"[Teams] {names['game']}已开启：{len(self.captains)} 位{names['captain']}，{where}，"
                  f"{len(self.rules.actions)} 个操作，每天 {self.rules.reset_time.strftime('%H:%M')} 清空{names['team']}")
        while True:
            try:
                cleared = self._close_days()
                if cleared:
                    _log.info(f"[Teams] 新的一天，已清空 {cleared} 个群的{names['team']}")
            except Exception as e:
                _log.error(f"[Teams] 清空{names['team']}出错: {e}")
            await asyncio.sleep(60)
