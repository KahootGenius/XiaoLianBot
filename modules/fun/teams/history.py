"""What has happened to each member in the team game across game days (data/teams.db), for /档案.

Counts only: how often someone was caught and by whom, how often a captain grabbed someone and
whom, how often they hid, were found, stayed hidden until the day was over or met an event, and how
often each status befell them. Nothing says when, and nothing of what anyone said is here.
"""
import os
import sqlite3


SCHEMA = """
CREATE TABLE IF NOT EXISTS tally (
    group_openid TEXT NOT NULL,
    member_openid TEXT NOT NULL,
    what TEXT NOT NULL,              -- caught, grabbed, hid, found, finds, slipped, event; a status's name after "="
    other TEXT NOT NULL DEFAULT '',  -- with whom: the member_openid of who caught them or whom they grabbed;
                                     -- for an event, its name
    name TEXT NOT NULL DEFAULT '',   -- that member's nickname the last time it happened, for showing the record
    n INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (group_openid, member_openid, what, other)
);
"""
STATUS = "="        # in front of a status's name, so one named like a count can't be taken for it


class History:
    def __init__(self, path):
        if path != ":memory:":
            os.makedirs(os.path.dirname(path), exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        if path != ":memory:":
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)

    def add(self, group, member, what, other="", name=""):
        """One more of `what` for a member, with `other` when it matters who it was with, and the
        nickname that other member has now."""
        self.db.execute("INSERT INTO tally (group_openid, member_openid, what, other, name, n) VALUES (?, ?, ?, ?, ?, 1) "
                        "ON CONFLICT (group_openid, member_openid, what, other) DO UPDATE SET n = n + 1, "
                        "name = CASE WHEN excluded.name != '' THEN excluded.name ELSE name END",
                        (group, member, what, other, name or ""))
        self.db.commit()

    def totals(self, group, member):
        """{what: how often} for a member, whoever it was with."""
        rows = self.db.execute("SELECT what, SUM(n) AS n FROM tally WHERE group_openid = ? AND member_openid = ? "
                               "GROUP BY what", (group, member)).fetchall()
        return {row["what"]: row["n"] for row in rows}

    def most(self, group, member, what):
        """(who or what it was most often with, how often, their nickname then) for one kind of
        thing, or None."""
        row = self.db.execute("SELECT other, n, name FROM tally WHERE group_openid = ? AND member_openid = ? AND what = ? "
                              "AND other != '' ORDER BY n DESC, rowid LIMIT 1", (group, member, what)).fetchone()
        return (row["other"], row["n"], row["name"]) if row else None

    def forget_member(self, group, member):
        """Their own record, and their part in everyone else's."""
        self.db.execute("DELETE FROM tally WHERE group_openid = ? AND (member_openid = ? OR "
                        "(other = ? AND what != 'event'))", (group, member, member))
        self.db.commit()

    def forget_group(self, group):
        self.db.execute("DELETE FROM tally WHERE group_openid = ?", (group,))
        self.db.commit()
