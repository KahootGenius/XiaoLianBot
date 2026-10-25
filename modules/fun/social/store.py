"""SQLite persistence for the social features (data/fun.db): birthdays, polls, and
once-a-day marks (birthday wishes, 翻牌子). See docs/PRIVACY_POLICY.md for how
long each is kept.
"""
import json
import os
import sqlite3


SCHEMA = """
CREATE TABLE IF NOT EXISTS birthdays (
    group_openid TEXT NOT NULL,
    member_openid TEXT NOT NULL,
    nickname TEXT,
    month INTEGER NOT NULL,
    day INTEGER NOT NULL,
    PRIMARY KEY (group_openid, member_openid)
);
CREATE TABLE IF NOT EXISTS polls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_openid TEXT NOT NULL,
    question TEXT NOT NULL,
    options TEXT NOT NULL,           -- JSON list of option texts
    creator TEXT NOT NULL,           -- member_openid
    creator_name TEXT,
    created_at INTEGER NOT NULL,
    closed_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_polls_group ON polls (group_openid, closed_at);
CREATE TABLE IF NOT EXISTS votes (
    poll_id INTEGER NOT NULL,
    member_openid TEXT NOT NULL,
    nickname TEXT,
    choice INTEGER NOT NULL,         -- index into the poll's options
    voted_at INTEGER NOT NULL,
    PRIMARY KEY (poll_id, member_openid)
);
CREATE TABLE IF NOT EXISTS daily (
    group_openid TEXT NOT NULL,
    member_openid TEXT NOT NULL,
    kind TEXT NOT NULL,              -- birthday | flip
    day TEXT NOT NULL,               -- the Beijing date it happened, e.g. 2026-10-25
    value TEXT,                      -- flip: who was drawn
    PRIMARY KEY (group_openid, member_openid, kind)
);
"""


class FunStore:
    def __init__(self, path):
        if path != ":memory:":
            os.makedirs(os.path.dirname(path), exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        if path != ":memory:":
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)

    def _one(self, sql, *args):
        return self.db.execute(sql, args).fetchone()

    def _all(self, sql, *args):
        return self.db.execute(sql, args).fetchall()

    def _write(self, sql, *args):
        cur = self.db.execute(sql, args)
        self.db.commit()
        return cur

    # ---- birthdays

    def birthday(self, group, member):
        return self._one("SELECT * FROM birthdays WHERE group_openid = ? AND member_openid = ?", group, member)

    def set_birthday(self, group, member, nickname, month, day):
        self._write("INSERT INTO birthdays (group_openid, member_openid, nickname, month, day) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT (group_openid, member_openid) DO UPDATE SET nickname = excluded.nickname, "
                    "month = excluded.month, day = excluded.day", group, member, nickname, month, day)

    def delete_birthday(self, group, member):
        return self._write("DELETE FROM birthdays WHERE group_openid = ? AND member_openid = ?",
                           group, member).rowcount

    def birthdays_in(self, group, month):
        return self._all("SELECT * FROM birthdays WHERE group_openid = ? AND month = ? ORDER BY day",
                         group, month)

    # ---- polls

    def open_poll(self, group):
        row = self._one("SELECT * FROM polls WHERE group_openid = ? AND closed_at IS NULL ORDER BY id DESC LIMIT 1",
                        group)
        return self._poll(row)

    def poll(self, poll_id):
        return self._poll(self._one("SELECT * FROM polls WHERE id = ?", poll_id))

    @staticmethod
    def _poll(row):
        if row is None:
            return None
        poll = dict(row)
        poll["options"] = json.loads(poll["options"])
        return poll

    def create_poll(self, group, question, options, creator, creator_name, ts):
        return self._write("INSERT INTO polls (group_openid, question, options, creator, creator_name, created_at) "
                           "VALUES (?, ?, ?, ?, ?, ?)", group, question, json.dumps(options, ensure_ascii=False),
                           creator, creator_name, ts).lastrowid

    def close_poll(self, poll_id, ts):
        self._write("UPDATE polls SET closed_at = ? WHERE id = ? AND closed_at IS NULL", ts, poll_id)

    def vote(self, poll_id, member, nickname, choice, ts):
        self._write("INSERT INTO votes (poll_id, member_openid, nickname, choice, voted_at) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT (poll_id, member_openid) DO UPDATE SET nickname = excluded.nickname, "
                    "choice = excluded.choice, voted_at = excluded.voted_at", poll_id, member, nickname, choice, ts)

    def votes(self, poll_id):
        return self._all("SELECT * FROM votes WHERE poll_id = ? ORDER BY voted_at", poll_id)

    # ---- once a day

    def daily(self, group, member, kind):
        """(day, value) of the last time this happened, or (None, None)."""
        row = self._one("SELECT day, value FROM daily WHERE group_openid = ? AND member_openid = ? AND kind = ?",
                        group, member, kind)
        return (row["day"], row["value"]) if row else (None, None)

    def mark_daily(self, group, member, kind, day, value=None):
        self._write("INSERT INTO daily (group_openid, member_openid, kind, day, value) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT (group_openid, member_openid, kind) DO UPDATE SET day = excluded.day, "
                    "value = excluded.value", group, member, kind, day, value)

    # ---- retention and deletion

    def prune(self, before_day, closed_before_ts):
        """Once-a-day marks from earlier days, and polls closed long ago with their votes."""
        self._write("DELETE FROM daily WHERE day < ?", before_day)
        old = [r["id"] for r in self._all("SELECT id FROM polls WHERE closed_at < ?", closed_before_ts)]
        for poll_id in old:
            self._write("DELETE FROM votes WHERE poll_id = ?", poll_id)
            self._write("DELETE FROM polls WHERE id = ?", poll_id)

    def forget_member(self, group, member):
        """Their birthday, their votes and marks. Polls they started stay, without their name."""
        self.delete_birthday(group, member)
        self._write("DELETE FROM daily WHERE group_openid = ? AND member_openid = ?", group, member)
        self._write("DELETE FROM votes WHERE member_openid = ? AND poll_id IN "
                    "(SELECT id FROM polls WHERE group_openid = ?)", member, group)
        self._write("UPDATE polls SET creator = '', creator_name = NULL WHERE group_openid = ? AND creator = ?",
                    group, member)

    def forget_group(self, group):
        self._write("DELETE FROM votes WHERE poll_id IN (SELECT id FROM polls WHERE group_openid = ?)", group)
        for table in ("polls", "birthdays", "daily"):
            self._write(f"DELETE FROM {table} WHERE group_openid = ?", group)
