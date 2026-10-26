"""Look at, rewrite and delete what AI chat remembers about members, from the command line.

Members see and delete their own memories with /我的记忆 and /忘记; this is for
the operator, on the machine running the bot. On the server, deploy/memories.sh
runs it as the bot's user:

    python -m modules.ai.memory_admin                   # every memory
    python -m modules.ai.memory_admin 爸爸              # memories or nicknames containing 爸爸
    python -m modules.ai.memory_admin delete 12 15      # delete memories 12 and 15
    python -m modules.ai.memory_admin edit 12 新的内容   # rewrite memory 12

The bot reads memories afresh for every reply, so changes apply at once.
"""
import os
import sqlite3
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
import pytz
from core.config import root_path
from modules.ai.memory import MAX_MEMORY_TEXT
from modules.ai.store import ChatStore


TZ = pytz.timezone("Asia/Shanghai")
USAGE = """Usage:
  memory_admin [WORD]              list every memory, or those whose text or member contains WORD
  memory_admin delete NUMBER...    delete memories by their number
  memory_admin edit NUMBER TEXT    rewrite a memory"""


def _width(text):
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text, width):
    return text + " " * max(0, width - _width(text))


def nicknames(store, manager_path):
    """{(group, member): nickname}: the member records' current name, else the last name seen in AI chat."""
    names = {}
    for row in store.db.execute("SELECT group_openid, member_openid, name FROM turns WHERE role = 'user' ORDER BY id"):
        names[(row[0], row[1])] = row[2]
    if os.path.exists(manager_path):
        db = sqlite3.connect(Path(manager_path).resolve().as_uri() + "?mode=ro", uri=True)
        try:
            for group, member, nickname in db.execute("SELECT group_openid, member_openid, nickname FROM members"):
                if nickname:
                    names[(group, member)] = nickname
        except sqlite3.Error:
            pass        # an older or empty manager.db: AI chat's names will do
        finally:
            db.close()
    return names


def show(store, names, word, out):
    rows = store.all_memories()
    who = {r["id"]: names.get((r["group_openid"], r["member_openid"])) or r["member_openid"][:8] + "…" for r in rows}
    if word:
        rows = [r for r in rows if word in r["text"] or word in who[r["id"]]]
    if not rows:
        print("No memories" + (f" containing {word}." if word else " yet."), file=out)
        return
    width = max(_width(who[r["id"]]) for r in rows)
    print(f"{'#':>5}  group     {_pad('member', width)}  saved  memory", file=out)
    for r in rows:
        saved = datetime.fromtimestamp(r["created_at"], TZ).strftime("%m-%d")
        print(f"{r['id']:>5}  {r['group_openid'][:8]}  {_pad(who[r['id']], width)}  {saved}  {r['text']}", file=out)
    print(f"{len(rows)} memories. Delete: delete NUMBER...   Rewrite: edit NUMBER NEW TEXT", file=out)


def delete(store, numbers, out):
    for n in numbers:
        row = store.memory(int(n))
        if row is None:
            print(f"No memory {n}.", file=out)
            continue
        store.delete_memory(row["id"])
        print(f"Deleted {n}: {row['text']}", file=out)
    return 0


def edit(store, number, text, out):
    if len(text) > MAX_MEMORY_TEXT:
        print(f"Keep it to {MAX_MEMORY_TEXT} characters; this is {len(text)}.", file=out)
        return 2
    row = store.memory(int(number))
    if row is None:
        print(f"No memory {number}.", file=out)
        return 1
    store.set_memory_text(row["id"], text)
    print(f"Memory {number}: {row['text']} -> {text}", file=out)
    return 0


def main(argv, chat_path=None, manager_path=None, out=sys.stdout):
    chat_path = chat_path or root_path("data", "chat.db")
    manager_path = manager_path or root_path("data", "manager.db")
    if argv[:1] in (["-h"], ["--help"], ["help"]):
        print(USAGE, file=out)
        return 0
    if not os.path.exists(chat_path):
        print(f"No AI chat database at {chat_path} yet.", file=out)
        return 1
    store = ChatStore(chat_path)
    try:
        if argv[:1] == ["delete"]:
            if len(argv) < 2 or not all(n.isdigit() for n in argv[1:]):
                print(USAGE, file=out)
                return 2
            return delete(store, argv[1:], out)
        if argv[:1] == ["edit"]:
            text = " ".join(argv[2:]).strip()
            if len(argv) < 3 or not argv[1].isdigit() or not text:
                print(USAGE, file=out)
                return 2
            return edit(store, argv[1], text, out)
        show(store, nicknames(store, manager_path), " ".join(argv).strip(), out)
        return 0
    finally:
        store.db.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
