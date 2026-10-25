"""Today's teams in every group, kept in data/teams.json so a restart doesn't end a game.

A group's teams belong to one game day and are cleared when the next one
starts (prompts/teams.yaml: reset_time).
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
class GroupTeams:
    day: str                                        # the game day, e.g. "2026-10-25"
    teams: dict = field(default_factory=dict)       # captain openid -> Team, in the order formed

    def team_of(self, member):
        """The team `member` was grabbed into, or None. Captains lead a team rather than sit in one."""
        return next((team for team in self.teams.values() if member in team.members), None)


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
            stale = teams is not None and teams.teams
            teams = self.groups[group] = GroupTeams(day)
            if stale:
                self.save()
        return teams

    def reset_stale(self, day):
        """Clear every group whose teams are from before game day `day`. Returns how many groups had teams."""
        day = day.isoformat()
        stale = [group for group, teams in self.groups.items() if teams.day != day]
        cleared = sum(1 for group in stale if self.groups[group].teams)
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
        self.save()

    def save(self):
        if self.path is None:
            return
        data = {"groups": {group: {"day": teams.day, "teams": [asdict(team) for team in teams.teams.values()]}
                           for group, teams in self.groups.items() if teams.teams}}
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
                self.groups[group] = teams
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            _log.warning(f"[Teams] {self.path} 读不了（{e}），队伍从空的开始")
            self.groups = {}
