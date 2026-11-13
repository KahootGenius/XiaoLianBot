"""Today's teams in every group, and who is hiding, kept in data/teams.json so a restart
doesn't end a game.

A group's teams belong to one game day and are cleared when the next one
starts (prompts/teams.yaml: reset_time). So is everything about hide-and-seek:
who is hiding and where, how often each has hidden, how often each captain has looked.
And so are the throws of the die each captain has used to take other captains' members.
"""
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from botpy import logging


_log = logging.get_logger()


@dataclass
class Member:
    openid: str
    name: str
    status: dict            # column -> status, see prompts/teams.yaml
    since: int = 0          # when they were grabbed, epoch seconds


@dataclass
class Team:
    captain: str            # the captain's member_openid
    name: str               # the captain's nickname
    members: dict = field(default_factory=dict)     # openid -> Member, in the order grabbed


@dataclass
class Hider:
    name: str
    spot: str               # where the bot hid them: nobody is told, not even them
    since: int = 0


@dataclass
class GroupTeams:
    day: str                                        # the game day, e.g. "2026-10-25"
    teams: dict = field(default_factory=dict)       # captain openid -> Team, in the order formed
    hiding: dict = field(default_factory=dict)      # openid -> Hider, in the order they hid
    hides: dict = field(default_factory=dict)       # openid -> how often they've hidden today
    searches: dict = field(default_factory=dict)    # captain openid -> how often they've looked today
    throws: dict = field(default_factory=dict)      # a steal's name -> {openid: how often they've thrown its die today}

    def team_of(self, member):
        """The team `member` was grabbed into, or None. Captains lead a team rather than sit in one."""
        return next((team for team in self.teams.values() if member in team.members), None)

    @property
    def played(self):
        """Whether anything has happened in this game day that's worth keeping."""
        return bool(self.teams or self.hiding or self.hides or self.searches or self.throws)


class Roster:
    def __init__(self, path, rules):
        self.path = path        # None keeps everything in memory
        self.rules = rules
        self.groups = {}        # group_openid -> GroupTeams
        self._load()

    def today(self, group, day):
        """The group's teams for game day `day` (a date); an earlier day's teams are cleared first."""
        day = day.isoformat()
        teams = self.groups.get(group)
        if teams is None or teams.day != day:
            stale = teams is not None and teams.played
            teams = self.groups[group] = GroupTeams(day)
            if stale:
                self.save()
        return teams

    def stale(self, day):
        """{group: its GroupTeams} for every group whose game is from before game day `day`."""
        day = day.isoformat()
        return {group: teams for group, teams in self.groups.items() if teams.day != day}

    def reset_stale(self, day):
        """Clear every group whose game is from before game day `day`. Returns how many groups had one."""
        stale = self.stale(day)
        cleared = sum(1 for teams in stale.values() if teams.played)
        for group in stale:
            del self.groups[group]
        if cleared:
            self.save()
        return cleared

    def forget_group(self, group):
        if self.groups.pop(group, None) is not None:
            self.save()

    def forget_member(self, group, member):
        """Take someone out of every team in the group, and dissolve the team they lead."""
        teams = self.groups.get(group)
        if teams is None:
            return
        teams.teams.pop(member, None)
        for team in list(teams.teams.values()):
            team.members.pop(member, None)
            if not team.members:
                del teams.teams[team.captain]
        for kept in (teams.hiding, teams.hides, teams.searches, *teams.throws.values()):
            kept.pop(member, None)
        teams.throws = {action: thrown for action, thrown in teams.throws.items() if thrown}
        self.save()

    def save(self):
        if self.path is None:
            return
        data = {"groups": {group: {"day": teams.day, "teams": [asdict(team) for team in teams.teams.values()],
                                   "hiding": {openid: asdict(hider) for openid, hider in teams.hiding.items()},
                                   "hides": teams.hides, "searches": teams.searches, "throws": teams.throws}
                           for group, teams in self.groups.items() if teams.played}}
        folder = os.path.dirname(self.path)
        os.makedirs(folder, exist_ok=True)
        # Write a temporary file and swap it in, so a crash mid-write can't leave half a file.
        fd, temp = tempfile.mkstemp(dir=folder, prefix=".teams-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(temp, self.path)
        except BaseException:
            os.unlink(temp)
            raise

    def _load(self):
        if self.path is None or not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            for group, saved in (data.get("groups") or {}).items():
                teams = GroupTeams(str(saved.get("day") or ""))
                for raw in saved.get("teams") or []:
                    team = Team(str(raw["captain"]), str(raw.get("name") or ""))
                    for openid, m in (raw.get("members") or {}).items():
                        team.members[openid] = Member(openid, str(m.get("name") or ""),
                                                      self.rules.tidy(m.get("status") or {}),
                                                      int(m.get("since") or 0))
                    if team.members:
                        teams.teams[team.captain] = team
                # Someone hidden somewhere the rules no longer have could never be found: they're out.
                for openid, h in (saved.get("hiding") or {}).items():
                    if h.get("spot") in self.rules.spots and teams.team_of(openid) is None:
                        teams.hiding[openid] = Hider(str(h.get("name") or ""), str(h["spot"]), int(h.get("since") or 0))
                teams.hides = {str(k): int(v) for k, v in (saved.get("hides") or {}).items()}
                teams.searches = {str(k): int(v) for k, v in (saved.get("searches") or {}).items()}
                teams.throws = {str(action): {str(k): int(v) for k, v in thrown.items()}
                                for action, thrown in (saved.get("throws") or {}).items()}
                self.groups[group] = teams
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            _log.warning(f"[Teams] {self.path} 读不了（{e}），队伍从空的开始")
            self.groups = {}
