"""SQLite persistence for AI chat (data/chat.db).

Only conversations with the bot are stored: messages that @ it and its own
replies, never other group chat. Raw turns are kept for ai_chat.keep_days
(7 by default). The rolling summary and members' memories stay until they are
reset or deleted. See docs/PRIVACY_POLICY.md.
"""
import os
import sqlite3


SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_openid TEXT NOT NULL,
    member_openid TEXT,              -- NULL for the bot's own replies
    name TEXT NOT NULL,              -- the speaker's nickname at the time
    role TEXT NOT NULL,              -- user | assistant
    content TEXT NOT NULL,
    ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_group ON turns (group_openid, id);
CREATE TABLE IF NOT EXISTS summaries (
    group_openid TEXT PRIMARY KEY,
    summary TEXT NOT NULL,
    up_to_id INTEGER NOT NULL,       -- the last turn folded into it
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_openid TEXT NOT NULL,
    member_openid TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memories_member ON memories (group_openid, member_openid, id);
"""


class ChatStore:
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

    # ---- turns

    def add_turn(self, group, member, name, role, content, ts):
        return self._write("INSERT INTO turns (group_openid, member_openid, name, role, content, ts) "
                           "VALUES (?, ?, ?, ?, ?, ?)", group, member, name, role, content, ts).lastrowid

    def recent_turns(self, group, limit=300):
        """The group's newest turns, oldest first."""
        rows = self._all("SELECT * FROM turns WHERE group_openid = ? ORDER BY id DESC LIMIT ?", group, limit)
        return rows[::-1]

    def turns_between(self, group, after_id, up_to_id):
        return self._all("SELECT * FROM turns WHERE group_openid = ? AND id > ? AND id <= ? ORDER BY id",
                         group, after_id, up_to_id)

    def last_turn_before(self, group, ts):
        row = self._one("SELECT MAX(id) AS id FROM turns WHERE group_openid = ? AND ts < ?", group, ts)
        return row["id"] if row and row["id"] is not None else None

    def groups_with_turns_before(self, ts):
        return [r["group_openid"] for r in self._all("SELECT DISTINCT group_openid FROM turns WHERE ts < ?", ts)]

    def prune(self, before_ts):
        """Delete raw turns older than the retention period."""
        self._write("DELETE FROM turns WHERE ts < ?", before_ts)

    # ---- summary

    def summary(self, group):
        return self._one("SELECT * FROM summaries WHERE group_openid = ?", group)

    def set_summary(self, group, summary, up_to_id, ts):
        self._write("INSERT INTO summaries (group_openid, summary, up_to_id, updated_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(group_openid) DO UPDATE SET summary = excluded.summary, "
                    "up_to_id = excluded.up_to_id, updated_at = excluded.updated_at",
                    group, summary, up_to_id, ts)

    def reset_chat(self, group):
        """Forget the group's conversation (turns and summary). Members' memories stay."""
        self._write("DELETE FROM turns WHERE group_openid = ?", group)
        self._write("DELETE FROM summaries WHERE group_openid = ?", group)

    # ---- memories

    def memories(self, group, member):
        return self._all("SELECT * FROM memories WHERE group_openid = ? AND member_openid = ? ORDER BY id",
                         group, member)

    def add_memory(self, group, member, text, ts):
        self._write("INSERT INTO memories (group_openid, member_openid, text, created_at) VALUES (?, ?, ?, ?)",
                    group, member, text, ts)

    def delete_memory(self, memory_id):
        self._write("DELETE FROM memories WHERE id = ?", memory_id)

    def memory(self, memory_id):
        return self._one("SELECT * FROM memories WHERE id = ?", memory_id)

    def all_memories(self):
        """Every member's memories in every group, a member's together (modules/ai/memory_admin.py)."""
        return self._all("SELECT * FROM memories ORDER BY group_openid, member_openid, id")

    def set_memory_text(self, memory_id, text):
        self._write("UPDATE memories SET text = ? WHERE id = ?", text, memory_id)

    def clear_memories(self, group, member):
        return self._write("DELETE FROM memories WHERE group_openid = ? AND member_openid = ?",
                           group, member).rowcount

    # ---- deletion on request

    def forget_member(self, group, member):
        """Everything about one member: their memories, their messages, and the group's summary,
        which may retell what they said."""
        self.clear_memories(group, member)
        self._write("DELETE FROM turns WHERE group_openid = ? AND member_openid = ?", group, member)
        self._write("DELETE FROM summaries WHERE group_openid = ?", group)

    def forget_group(self, group):
        for table in ("turns", "summaries", "memories"):
            self._write(f"DELETE FROM {table} WHERE group_openid = ?", group)
