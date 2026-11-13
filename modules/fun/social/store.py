"""SQLite persistence for the social features (data/fun.db): birthdays, polls,
征集意见 with their opinions and who joined them, once-a-day marks (birthday
wishes, 翻牌子), and 海龟汤 with its questions and guesses. See
docs/PRIVACY_POLICY.md for how long each is kept.
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
CREATE TABLE IF NOT EXISTS opinion_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_openid TEXT NOT NULL,
    topic TEXT NOT NULL,
    creator TEXT NOT NULL,           -- member_openid
    creator_name TEXT,
    created_at INTEGER NOT NULL,
    closed_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_opinion_calls_group ON opinion_calls (group_openid, closed_at);
CREATE TABLE IF NOT EXISTS opinions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER NOT NULL,
    member_openid TEXT NOT NULL,
    nickname TEXT,
    text TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_opinions_call ON opinions (call_id);
-- Who gave an opinion in each 征集, kept after a 撤回 so it counts toward a badge only once.
CREATE TABLE IF NOT EXISTS joined (
    call_id INTEGER NOT NULL,
    member_openid TEXT NOT NULL,
    PRIMARY KEY (call_id, member_openid)
);
CREATE TABLE IF NOT EXISTS daily (
    group_openid TEXT NOT NULL,
    member_openid TEXT NOT NULL,
    kind TEXT NOT NULL,              -- birthday | flip | "day:" and a special day's name, once greeted on it
    day TEXT NOT NULL,               -- the Beijing date it happened, e.g. 2026-10-25
    value TEXT,                      -- flip: who was drawn
    PRIMARY KEY (group_openid, member_openid, kind)
);
CREATE TABLE IF NOT EXISTS soups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_openid TEXT NOT NULL,
    puzzle TEXT NOT NULL,            -- its code in prompts/soups.yaml
    starter TEXT NOT NULL,           -- member_openid
    starter_name TEXT,
    created_at INTEGER NOT NULL,
    closed_at INTEGER,
    solver TEXT,                     -- member_openid of whoever worked it out; NULL: nobody did
    solver_name TEXT,
    hints INTEGER NOT NULL DEFAULT 0     -- how many of its hints have been given
);
CREATE INDEX IF NOT EXISTS idx_soups_group ON soups (group_openid, closed_at);
CREATE TABLE IF NOT EXISTS soup_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    soup_id INTEGER NOT NULL,
    member_openid TEXT NOT NULL,
    nickname TEXT,
    kind TEXT NOT NULL,              -- ask | guess
    text TEXT NOT NULL,
    answer TEXT NOT NULL,            -- ask: positive, negative, unrelated, partly; guess: right, close, wrong
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_soup_turns_soup ON soup_turns (soup_id);
-- The puzzles each group has had, so its next one is new to it. Nothing about who played.
CREATE TABLE IF NOT EXISTS soup_played (
    group_openid TEXT NOT NULL,
    puzzle TEXT NOT NULL,
    played_at INTEGER NOT NULL,
    PRIMARY KEY (group_openid, puzzle)
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
        """Records or changes their vote; True when it's their first in this poll."""
        first = self._one("SELECT 1 FROM votes WHERE poll_id = ? AND member_openid = ?", poll_id, member) is None
        self._write("INSERT INTO votes (poll_id, member_openid, nickname, choice, voted_at) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT (poll_id, member_openid) DO UPDATE SET nickname = excluded.nickname, "
                    "choice = excluded.choice, voted_at = excluded.voted_at", poll_id, member, nickname, choice, ts)
        return first

    def votes(self, poll_id):
        return self._all("SELECT * FROM votes WHERE poll_id = ? ORDER BY voted_at", poll_id)

    # ---- 征集意见

    def open_call(self, group):
        return self._one("SELECT * FROM opinion_calls WHERE group_openid = ? AND closed_at IS NULL "
                         "ORDER BY id DESC LIMIT 1", group)

    def create_call(self, group, topic, creator, creator_name, ts):
        return self._write("INSERT INTO opinion_calls (group_openid, topic, creator, creator_name, created_at) "
                           "VALUES (?, ?, ?, ?, ?)", group, topic, creator, creator_name, ts).lastrowid

    def close_call(self, call_id, ts):
        self._write("UPDATE opinion_calls SET closed_at = ? WHERE id = ? AND closed_at IS NULL", ts, call_id)

    def add_opinion(self, call_id, member, nickname, text, ts):
        self._write("INSERT INTO opinions (call_id, member_openid, nickname, text, created_at) VALUES (?, ?, ?, ?, ?)",
                    call_id, member, nickname, text, ts)

    def opinions(self, call_id):
        return self._all("SELECT * FROM opinions WHERE call_id = ? ORDER BY id", call_id)

    def take_back(self, call_id, member):
        """Deletes the member's latest opinion in this call; returns its text, or None."""
        row = self._one("SELECT id, text FROM opinions WHERE call_id = ? AND member_openid = ? ORDER BY id DESC LIMIT 1",
                        call_id, member)
        if row is None:
            return None
        self._write("DELETE FROM opinions WHERE id = ?", row["id"])
        return row["text"]

    def join_call(self, call_id, member):
        """Notes that they gave an opinion in this 征集; True the first time."""
        return self._write("INSERT OR IGNORE INTO joined (call_id, member_openid) VALUES (?, ?)",
                           call_id, member).rowcount == 1

    # ---- 海龟汤

    def open_soup(self, group):
        return self._one("SELECT * FROM soups WHERE group_openid = ? AND closed_at IS NULL ORDER BY id DESC LIMIT 1",
                         group)

    def start_soup(self, group, puzzle, starter, starter_name, ts):
        """Opens a soup and notes that the group has now had this puzzle."""
        self._write("INSERT OR REPLACE INTO soup_played (group_openid, puzzle, played_at) VALUES (?, ?, ?)",
                    group, puzzle, ts)
        return self._write("INSERT INTO soups (group_openid, puzzle, starter, starter_name, created_at) "
                           "VALUES (?, ?, ?, ?, ?)", group, puzzle, starter, starter_name, ts).lastrowid

    def close_soup(self, soup_id, ts, solver=None, solver_name=None):
        """Closes it, with whoever worked it out. False if it had already closed: two right guesses
        at once, and only the first counts."""
        return self._write("UPDATE soups SET closed_at = ?, solver = ?, solver_name = ? WHERE id = ? "
                           "AND closed_at IS NULL", ts, solver, solver_name, soup_id).rowcount == 1

    def soup_played(self, group):
        """The codes of the puzzles this group has had, oldest first."""
        return [r["puzzle"] for r in self._all("SELECT puzzle FROM soup_played WHERE group_openid = ? "
                                               "ORDER BY played_at, rowid", group)]

    def reset_soup_played(self, group, codes):
        """The group may have these puzzles again: it has been through their whole set."""
        for code in codes:
            self._write("DELETE FROM soup_played WHERE group_openid = ? AND puzzle = ?", group, code)

    def add_soup_turn(self, soup_id, member, nickname, kind, text, answer, ts):
        self._write("INSERT INTO soup_turns (soup_id, member_openid, nickname, kind, text, answer, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)", soup_id, member, nickname, kind, text, answer, ts)

    def soup_turns(self, soup_id, kind="ask"):
        return self._all("SELECT * FROM soup_turns WHERE soup_id = ? AND kind = ? ORDER BY id", soup_id, kind)

    def add_soup_hint(self, soup_id):
        self._write("UPDATE soups SET hints = hints + 1 WHERE id = ?", soup_id)

    # ---- once a day

    def daily(self, group, member, kind):
        """(day, value) of the last time this happened, or (None, None)."""
        row = self._one("SELECT day, value FROM daily WHERE group_openid = ? AND member_openid = ? AND kind = ?",
                        group, member, kind)
        return (row["day"], row["value"]) if row else (None, None)

    def count_daily(self, group, kind, day):
        """How many members in the group it has happened to on that day."""
        return self._one("SELECT COUNT(*) AS n FROM daily WHERE group_openid = ? AND kind = ? AND day = ?",
                         group, kind, day)["n"]

    def mark_daily(self, group, member, kind, day, value=None):
        self._write("INSERT INTO daily (group_openid, member_openid, kind, day, value) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT (group_openid, member_openid, kind) DO UPDATE SET day = excluded.day, "
                    "value = excluded.value", group, member, kind, day, value)

    # ---- retention and deletion

    def close_stale(self, now_ts, poll_seconds, call_seconds, soup_seconds, soup_grace=0):
        """Whatever ran out of time while nobody was looking closes as of when its time was up, so it
        is deleted on schedule. Until someone uses its command again it would otherwise stay open,
        and so stay for good. A 海龟汤 is left `soup_grace` longer first."""
        for table, seconds, grace in (("polls", poll_seconds, 0), ("opinion_calls", call_seconds, 0),
                                      ("soups", soup_seconds, soup_grace)):
            self._write(f"UPDATE {table} SET closed_at = created_at + ? WHERE closed_at IS NULL AND created_at <= ?",
                        seconds, now_ts - seconds - grace)

    def prune(self, before_day, closed_before_ts, calls_closed_before_ts, soups_closed_before_ts=0):
        """Once-a-day marks from earlier days, polls closed long ago with their votes, 征集意见
        closed long ago with their opinions and who joined them, and 海龟汤 that ended a while
        ago with their questions and guesses."""
        self._write("DELETE FROM daily WHERE day < ?", before_day)
        self._write("DELETE FROM soup_turns WHERE soup_id IN (SELECT id FROM soups WHERE closed_at < ?)",
                    soups_closed_before_ts)
        self._write("DELETE FROM soups WHERE closed_at < ?", soups_closed_before_ts)
        old = [r["id"] for r in self._all("SELECT id FROM polls WHERE closed_at < ?", closed_before_ts)]
        for poll_id in old:
            self._write("DELETE FROM votes WHERE poll_id = ?", poll_id)
            self._write("DELETE FROM polls WHERE id = ?", poll_id)
        old = [r["id"] for r in self._all("SELECT id FROM opinion_calls WHERE closed_at < ?", calls_closed_before_ts)]
        for call_id in old:
            self._write("DELETE FROM opinions WHERE call_id = ?", call_id)
            self._write("DELETE FROM joined WHERE call_id = ?", call_id)
            self._write("DELETE FROM opinion_calls WHERE id = ?", call_id)

    def forget_member(self, group, member):
        """Their birthday, votes, opinions, marks, and what they asked and guessed in 海龟汤. Polls,
        征集意见 and 海龟汤 they started or solved stay, without their name."""
        self.delete_birthday(group, member)
        self._write("DELETE FROM daily WHERE group_openid = ? AND member_openid = ?", group, member)
        self._write("DELETE FROM votes WHERE member_openid = ? AND poll_id IN "
                    "(SELECT id FROM polls WHERE group_openid = ?)", member, group)
        self._write("UPDATE polls SET creator = '', creator_name = NULL WHERE group_openid = ? AND creator = ?",
                    group, member)
        self._write("DELETE FROM opinions WHERE member_openid = ? AND call_id IN "
                    "(SELECT id FROM opinion_calls WHERE group_openid = ?)", member, group)
        self._write("DELETE FROM joined WHERE member_openid = ? AND call_id IN "
                    "(SELECT id FROM opinion_calls WHERE group_openid = ?)", member, group)
        self._write("UPDATE opinion_calls SET creator = '', creator_name = NULL WHERE group_openid = ? AND creator = ?",
                    group, member)
        self._write("DELETE FROM soup_turns WHERE member_openid = ? AND soup_id IN "
                    "(SELECT id FROM soups WHERE group_openid = ?)", member, group)
        self._write("UPDATE soups SET starter = '', starter_name = NULL WHERE group_openid = ? AND starter = ?",
                    group, member)
        self._write("UPDATE soups SET solver = '', solver_name = NULL WHERE group_openid = ? AND solver = ?",
                    group, member)

    def forget_group(self, group):
        self._write("DELETE FROM soup_turns WHERE soup_id IN (SELECT id FROM soups WHERE group_openid = ?)", group)
        self._write("DELETE FROM votes WHERE poll_id IN (SELECT id FROM polls WHERE group_openid = ?)", group)
        self._write("DELETE FROM opinions WHERE call_id IN (SELECT id FROM opinion_calls WHERE group_openid = ?)", group)
        self._write("DELETE FROM joined WHERE call_id IN (SELECT id FROM opinion_calls WHERE group_openid = ?)", group)
        for table in ("polls", "opinion_calls", "birthdays", "daily", "soups", "soup_played"):
            self._write(f"DELETE FROM {table} WHERE group_openid = ?", group)
