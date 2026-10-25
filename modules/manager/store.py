"""SQLite persistence for the group manager.

Stores activity *metadata* only: who spoke when, whether it counted, and a
short snippet of each member's latest valid message (for "why was I
reminded?" questions). Full message text is never written to disk.
"""
import json
import os
import sqlite3


SCHEMA = """
CREATE TABLE IF NOT EXISTS groups (
    group_openid TEXT PRIMARY KEY,
    -- First every-message event. NULL until the owner lets the bot receive
    -- all messages: before that, everyone would merely *look* silent.
    tracking_start INTEGER,
    full_seen_at INTEGER,            -- latest GROUP_MESSAGE_CREATE
    at_seen_at INTEGER,              -- latest GROUP_AT_MESSAGE_CREATE
    mode TEXT,                       -- overrides config when an admin switches it
    member_list_api INTEGER,         -- capability probe results: 1 / 0 / NULL (unknown)
    remove_api INTEGER,
    probed_at INTEGER
);
CREATE TABLE IF NOT EXISTS members (
    group_openid TEXT NOT NULL,
    member_openid TEXT NOT NULL,
    nickname TEXT,
    role TEXT,
    is_bot INTEGER NOT NULL DEFAULT 0,
    first_seen INTEGER NOT NULL,
    joined_at INTEGER,
    rejoined_at INTEGER,
    last_message_at INTEGER,
    last_valid_at INTEGER,
    last_valid_snippet TEXT,
    reminded_at INTEGER,
    exempt INTEGER NOT NULL DEFAULT 0,
    exempt_reason TEXT,
    status TEXT NOT NULL DEFAULT 'active',   -- active | removed | left
    removed_at INTEGER,
    streak INTEGER NOT NULL DEFAULT 0,
    best_streak INTEGER NOT NULL DEFAULT 0,
    streak_day TEXT,
    PRIMARY KEY (group_openid, member_openid)
);
CREATE TABLE IF NOT EXISTS counters (
    group_openid TEXT NOT NULL,
    member_openid TEXT NOT NULL,
    key TEXT NOT NULL,
    value INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (group_openid, member_openid, key)
);
CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    group_openid TEXT NOT NULL,
    member_openid TEXT NOT NULL,
    ts INTEGER NOT NULL,
    day TEXT NOT NULL,
    hour INTEGER NOT NULL,
    valid INTEGER NOT NULL,
    kind TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_group_ts ON messages (group_openid, ts);
CREATE TABLE IF NOT EXISTS leaves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_openid TEXT NOT NULL,
    member_openid TEXT NOT NULL,
    start_at INTEGER NOT NULL,
    end_at INTEGER NOT NULL,
    reason TEXT,
    granted_by TEXT,
    ended_at INTEGER                 -- set when the leave ends early
);
CREATE TABLE IF NOT EXISTS achievements (
    group_openid TEXT NOT NULL,
    member_openid TEXT NOT NULL,
    code TEXT NOT NULL,
    earned_at INTEGER NOT NULL,
    PRIMARY KEY (group_openid, member_openid, code)
);
CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_openid TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    status TEXT NOT NULL,            -- pending | manual | executed | cancelled | expired | done
    members TEXT NOT NULL,           -- JSON list of member_openids
    decided_by TEXT,
    decided_at INTEGER
);
CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    group_openid TEXT NOT NULL,
    member_openid TEXT,
    action TEXT NOT NULL,
    detail TEXT,
    actor TEXT
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


class Store:
    def __init__(self, path):
        if path != ":memory:":
            os.makedirs(os.path.dirname(path), exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        if path != ":memory:":
            self.db.execute("PRAGMA journal_mode=WAL")
            # Standard pairing with WAL: crash-safe, without an fsync per write.
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

    # ---- groups ----

    def ensure_group(self, group):
        self._write("INSERT OR IGNORE INTO groups (group_openid) VALUES (?)", group)
        return self.group(group)

    def saw_event(self, group, event_type, ts):
        """Note which kind of message event arrived, to tell whether every-message access works."""
        if event_type == "GROUP_MESSAGE_CREATE":
            self._write("UPDATE groups SET tracking_start = COALESCE(tracking_start, ?), full_seen_at = ? "
                        "WHERE group_openid = ?", ts, ts, group)
        elif event_type == "GROUP_AT_MESSAGE_CREATE":
            self._write("UPDATE groups SET at_seen_at = ? WHERE group_openid = ?", ts, group)

    def group(self, group):
        return self._one("SELECT * FROM groups WHERE group_openid = ?", group)

    def set_group_mode(self, group, mode):
        self._write("UPDATE groups SET mode = ? WHERE group_openid = ?", mode, group)

    def set_capabilities(self, group, member_list, remove, now_ts):
        self._write(
            "UPDATE groups SET member_list_api = ?, remove_api = ?, probed_at = ? WHERE group_openid = ?",
            member_list, remove, now_ts, group)

    # ---- messages ----

    def has_message(self, message_id):
        return self._one("SELECT 1 FROM messages WHERE message_id = ?", message_id) is not None

    def record_message(self, message_id, group, member, ts, day, hour, valid, kind):
        self._write(
            "INSERT OR IGNORE INTO messages (message_id, group_openid, member_openid, ts, day, hour, valid, kind) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            message_id, group, member, ts, day, hour, int(valid), kind)

    def prune(self, messages_before, actions_before, departed_before):
        """Apply the retention periods promised in docs/PRIVACY_POLICY.md."""
        self._write("DELETE FROM messages WHERE ts < ?", messages_before)
        self._write("DELETE FROM actions WHERE ts < ?", actions_before)
        gone = self._all("SELECT group_openid, member_openid FROM members "
                         "WHERE status != 'active' AND removed_at < ?", departed_before)
        for row in gone:
            self.forget_member(row["group_openid"], row["member_openid"])

    def forget_member(self, group, member):
        for table in ("members", "counters", "achievements", "leaves", "messages", "actions"):
            self._write(f"DELETE FROM {table} WHERE group_openid = ? AND member_openid = ?", group, member)

    def forget_group(self, group):
        for table in ("groups", "members", "counters", "achievements", "leaves", "messages", "actions", "batches"):
            self._write(f"DELETE FROM {table} WHERE group_openid = ?", group)
        self._write("DELETE FROM kv WHERE key LIKE ?", f"%:{group}")

    # ---- members ----

    def member(self, group, member):
        return self._one("SELECT * FROM members WHERE group_openid = ? AND member_openid = ?", group, member)

    def members(self, group, status="active"):
        return self._all("SELECT * FROM members WHERE group_openid = ? AND status = ?", group, status)

    def find_members(self, group, member_openids):
        marks = ",".join("?" * len(member_openids))
        rows = self._all(
            f"SELECT * FROM members WHERE group_openid = ? AND member_openid IN ({marks})", group, *member_openids)
        return {r["member_openid"]: r for r in rows}

    def ensure_member(self, group, member, nickname, ts):
        """Create a row for someone who hasn't spoken yet (e.g. an admin grants them leave)."""
        self._write(
            "INSERT OR IGNORE INTO members (group_openid, member_openid, nickname, first_seen) VALUES (?, ?, ?, ?)",
            group, member, nickname, ts)
        if nickname:
            self._write("UPDATE members SET nickname = ? WHERE group_openid = ? AND member_openid = ?",
                        nickname, group, member)

    def touch_member(self, group, member, nickname, role, is_bot, ts):
        """Record that a member was seen; creates the row on first sight."""
        self._write(
            "INSERT OR IGNORE INTO members (group_openid, member_openid, nickname, role, is_bot, first_seen) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            group, member, nickname, role, int(bool(is_bot)), ts)
        self._write(
            "UPDATE members SET nickname = COALESCE(?, nickname), role = COALESCE(?, role), "
            "is_bot = MAX(is_bot, ?), last_message_at = ? WHERE group_openid = ? AND member_openid = ?",
            nickname, role, int(bool(is_bot)), ts, group, member)

    def sync_member(self, group, member, nickname, role, is_bot, joined_at, ts):
        """Upsert a member from the member-list API (they may never have spoken)."""
        self._write(
            "INSERT OR IGNORE INTO members (group_openid, member_openid, nickname, role, is_bot, first_seen, joined_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            group, member, nickname, role, int(bool(is_bot)), ts, joined_at)
        self._write(
            "UPDATE members SET nickname = COALESCE(?, nickname), role = COALESCE(?, role), is_bot = ?, "
            "joined_at = COALESCE(?, joined_at) WHERE group_openid = ? AND member_openid = ?",
            nickname, role, int(bool(is_bot)), joined_at, group, member)

    def mark_valid(self, group, member, ts, snippet, streak, best_streak, streak_day):
        self._write(
            "UPDATE members SET last_valid_at = ?, last_valid_snippet = ?, streak = ?, best_streak = ?, "
            "streak_day = ? WHERE group_openid = ? AND member_openid = ?",
            ts, snippet, streak, best_streak, streak_day, group, member)

    def set_reminded(self, group, members, ts):
        for member in members:
            self._write("UPDATE members SET reminded_at = ? WHERE group_openid = ? AND member_openid = ?",
                        ts, group, member)

    def clear_reminded(self, group, member):
        self._write("UPDATE members SET reminded_at = NULL WHERE group_openid = ? AND member_openid = ?",
                    group, member)

    def set_exempt(self, group, member, exempt, reason=None):
        self._write("UPDATE members SET exempt = ?, exempt_reason = ? WHERE group_openid = ? AND member_openid = ?",
                    int(exempt), reason if exempt else None, group, member)

    def exempt_members(self, group):
        return self._all("SELECT * FROM members WHERE group_openid = ? AND exempt = 1 AND status = 'active'", group)

    def set_status(self, group, members, status, ts):
        for member in members:
            self._write(
                "UPDATE members SET status = ?, removed_at = ?, reminded_at = NULL "
                "WHERE group_openid = ? AND member_openid = ?",
                status, ts, group, member)

    def mark_rejoined(self, group, member, ts, joined_at=None):
        self._write(
            "UPDATE members SET status = 'active', rejoined_at = ?, joined_at = COALESCE(?, joined_at), "
            "reminded_at = NULL, removed_at = NULL WHERE group_openid = ? AND member_openid = ?",
            ts, joined_at, group, member)

    # ---- counters ----

    def bump(self, group, member, increments):
        for key, amount in increments.items():
            if not amount:
                continue
            self._write(
                "INSERT INTO counters (group_openid, member_openid, key, value) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (group_openid, member_openid, key) DO UPDATE SET value = value + excluded.value",
                group, member, key, amount)

    def counters(self, group, member):
        rows = self._all("SELECT key, value FROM counters WHERE group_openid = ? AND member_openid = ?", group, member)
        return {r["key"]: r["value"] for r in rows}

    # ---- leaves ----

    def add_leave(self, group, member, start_at, end_at, reason, granted_by):
        # A new leave replaces any current one.
        self.end_leave(group, member, start_at)
        cur = self._write(
            "INSERT INTO leaves (group_openid, member_openid, start_at, end_at, reason, granted_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            group, member, start_at, end_at, reason, granted_by)
        return cur.lastrowid

    def current_leave(self, group, member, now_ts):
        return self._one(
            "SELECT * FROM leaves WHERE group_openid = ? AND member_openid = ? AND start_at <= ? AND end_at > ? "
            "AND ended_at IS NULL ORDER BY id DESC LIMIT 1",
            group, member, now_ts, now_ts)

    def end_leave(self, group, member, now_ts):
        leave = self.current_leave(group, member, now_ts)
        if leave is None:
            return None
        self._write("UPDATE leaves SET ended_at = ? WHERE id = ?", now_ts, leave["id"])
        return leave

    def leave(self, leave_id):
        return self._one("SELECT * FROM leaves WHERE id = ?", leave_id)

    def end_leave_by_id(self, leave_id, now_ts):
        self._write("UPDATE leaves SET ended_at = ? WHERE id = ? AND ended_at IS NULL", now_ts, leave_id)

    def latest_leave(self, group, member):
        return self._one(
            "SELECT * FROM leaves WHERE group_openid = ? AND member_openid = ? ORDER BY id DESC LIMIT 1",
            group, member)

    def latest_leaves(self, group):
        rows = self._all(
            "SELECT * FROM leaves WHERE id IN (SELECT MAX(id) FROM leaves WHERE group_openid = ? GROUP BY member_openid)",
            group)
        return {r["member_openid"]: r for r in rows}

    def active_leaves(self, group, now_ts):
        return self._all(
            "SELECT * FROM leaves WHERE group_openid = ? AND start_at <= ? AND end_at > ? AND ended_at IS NULL "
            "ORDER BY end_at",
            group, now_ts, now_ts)

    # ---- achievements ----

    def award(self, group, member, code, ts):
        cur = self._write(
            "INSERT OR IGNORE INTO achievements (group_openid, member_openid, code, earned_at) VALUES (?, ?, ?, ?)",
            group, member, code, ts)
        return cur.rowcount == 1

    def achievements(self, group, member):
        return self._all(
            "SELECT * FROM achievements WHERE group_openid = ? AND member_openid = ? ORDER BY earned_at",
            group, member)

    def achievements_since(self, group, since_ts):
        return self._all(
            "SELECT a.*, m.nickname FROM achievements a LEFT JOIN members m "
            "ON m.group_openid = a.group_openid AND m.member_openid = a.member_openid "
            "WHERE a.group_openid = ? AND a.earned_at >= ? ORDER BY a.earned_at",
            group, since_ts)

    # ---- removal batches ----

    def create_batch(self, group, members, status, ts):
        cur = self._write(
            "INSERT INTO batches (group_openid, created_at, status, members) VALUES (?, ?, ?, ?)",
            group, ts, status, json.dumps(members))
        return cur.lastrowid

    def batch(self, batch_id):
        return self._one("SELECT * FROM batches WHERE id = ?", batch_id)

    def open_batches(self, group):
        return self._all(
            "SELECT * FROM batches WHERE group_openid = ? AND status IN ('pending', 'manual') ORDER BY id", group)

    def update_batch(self, batch_id, status=None, members=None, decided_by=None, ts=None):
        if members is not None:
            self._write("UPDATE batches SET members = ? WHERE id = ?", json.dumps(members), batch_id)
        if status is not None:
            self._write("UPDATE batches SET status = ?, decided_by = ?, decided_at = ? WHERE id = ?",
                        status, decided_by, ts, batch_id)

    # ---- statistics ----

    def valid_counts(self, group, since_ts, until_ts=None):
        """[(member_openid, nickname, valid_count)] sorted by count, for leaderboards."""
        until_ts = until_ts if until_ts is not None else 2 ** 62
        return self._all(
            "SELECT msg.member_openid, m.nickname, COUNT(*) AS n FROM messages msg "
            "LEFT JOIN members m ON m.group_openid = msg.group_openid AND m.member_openid = msg.member_openid "
            "WHERE msg.group_openid = ? AND msg.valid = 1 AND msg.ts >= ? AND msg.ts < ? "
            "AND COALESCE(m.is_bot, 0) = 0 "
            "GROUP BY msg.member_openid ORDER BY n DESC, MIN(msg.ts)",
            group, since_ts, until_ts)

    def member_activity(self, group):
        """Every recorded member with their latest message's verdict and their all-time valid count.

        latest_* columns are NULL when that message has aged out of the 35-day
        message log; members.last_message_at still has its time.
        """
        return self._all(
            "WITH latest AS ("
            "  SELECT member_openid, ts, valid, kind, "
            "         ROW_NUMBER() OVER (PARTITION BY member_openid ORDER BY ts DESC, rowid DESC) AS rn "
            "  FROM messages WHERE group_openid = ?) "
            "SELECT m.*, l.ts AS latest_ts, l.valid AS latest_valid, l.kind AS latest_kind, "
            "       COALESCE(c.value, 0) AS valid_total "
            "FROM members m "
            "LEFT JOIN latest l ON l.member_openid = m.member_openid AND l.rn = 1 "
            "LEFT JOIN counters c ON c.group_openid = m.group_openid AND c.member_openid = m.member_openid "
            "  AND c.key = 'valid' "
            "WHERE m.group_openid = ?",
            group, group)

    def totals(self, group, since_ts, until_ts):
        return self._one(
            "SELECT COUNT(*) AS total, COALESCE(SUM(valid), 0) AS valid, "
            "COUNT(DISTINCT CASE WHEN valid = 1 THEN member_openid END) AS speakers "
            "FROM messages WHERE group_openid = ? AND ts >= ? AND ts < ?",
            group, since_ts, until_ts)

    def busiest_hour(self, group, since_ts, until_ts):
        row = self._one(
            "SELECT hour, COUNT(*) AS n FROM messages WHERE group_openid = ? AND ts >= ? AND ts < ? "
            "GROUP BY hour ORDER BY n DESC LIMIT 1",
            group, since_ts, until_ts)
        return row["hour"] if row else None

    def newcomers(self, group, since_ts, until_ts, tracking_start):
        # Everyone is "first seen" right after tracking starts; only count
        # people first seen a day or more later as genuinely new.
        return self._all(
            "SELECT * FROM members WHERE group_openid = ? AND is_bot = 0 AND status = 'active' "
            "AND COALESCE(joined_at, first_seen) >= ? AND COALESCE(joined_at, first_seen) < ? "
            "AND COALESCE(joined_at, first_seen) >= ? ORDER BY first_seen",
            group, since_ts, until_ts, tracking_start + 86400)

    # ---- audit log & key-value ----

    def log(self, ts, group, member, action, detail=None, actor=None):
        self._write("INSERT INTO actions (ts, group_openid, member_openid, action, detail, actor) "
                    "VALUES (?, ?, ?, ?, ?, ?)", ts, group, member, action, detail, actor)

    def get_kv(self, key):
        row = self._one("SELECT value FROM kv WHERE key = ?", key)
        return row["value"] if row else None

    def set_kv(self, key, value):
        self._write("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                    key, value)
