"""Rehearse AI chat: play a scripted group chat against the real DeepSeek, with this machine's
config.yaml and style, and print her replies. For tuning a persona with evidence, not guesses.

    python -m modules.ai.rehearse                           # prompts/rehearsal.yaml (local/ first)
    python -m modules.ai.rehearse my_script.yaml
    python -m modules.ai.rehearse --model deepseek-v4-pro   # the reply model; helper calls unchanged
    python -m modules.ai.rehearse --times 3                 # play it three times
    python -m modules.ai.rehearse --memory                  # also take memory notes, then consolidate them

Each line costs two small DeepSeek calls (the pre-reply judgement and the reply).
Nothing is saved: the chat and any memories vanish when it ends.
"""
import argparse
import asyncio
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta
from types import SimpleNamespace
import pytz
import yaml
from core.config import content_path, load_config
from core.style import STYLES
from modules.ai.ai_chat import AIChatModule
from modules.ai.store import ChatStore


TZ = pytz.timezone("Asia/Shanghai")
GROUP = "REHEARSAL"
ACTION = re.compile(r"[（(]([^（）()\n]{1,20})[）)]")


class Gateway:
    def __init__(self):
        self.sent = []

    async def send(self, group, content, reply_to=None, buttons=None):
        self.sent.append(content)

    async def send_image(self, group, path, reply_to=None, url=None):
        self.sent.append(f"[sticker {os.path.basename(path)}]")

    async def send_markdown(self, group, content, reply_to=None):
        content = re.sub(r"!\[[^\]]*\]\(rehearsal://([^)]+)\)", r"[sticker \1]", content)
        self.sent.append(content.replace("\n\n", "\n"))

    async def upload_library_image(self, path):
        return "-", f"rehearsal://{os.path.basename(path)}", 0


def split_line(line):
    """("昵称", "消息") from "昵称：消息"."""
    match = re.match(r"\s*([^：:]+)[：:](.*)", str(line))
    if not match:
        raise ValueError(f"脚本里的这一行要写成「昵称：消息」：{line}")
    return match.group(1).strip(), match.group(2).strip()


def load_script(path):
    with open(path, encoding="utf-8") as f:
        script = yaml.safe_load(f) or {}
    if not isinstance(script, dict) or not script.get("lines"):
        raise ValueError(f"{path} 里要有 lines：一行一句「昵称：消息」")
    return script


def today_from(script):
    """The teams_today / social_today hooks for the script's game, birthdays and poll."""
    teams = [(captain, [(name, list(statuses or [])) for name, statuses in (members or {}).items()])
             for captain, members in (script.get("teams") or {}).items()]
    poll = script.get("poll")
    if poll:
        letters = "ABCDEFGHI"
        poll = (str(poll["question"]),
                [(letters[i], str(option), int(votes)) for i, (option, votes) in enumerate(poll["options"].items())])
    return (lambda group: teams), (lambda group: {"birthdays": list(script.get("birthdays") or []), "poll": poll})


async def play(script, config, run, model=None, remember=False, out=sys.stdout):
    chat = dict(config.get("ai_chat") or {}, memory=remember)
    if "special_people" in script:
        chat["special_people"] = script["special_people"]
    deepseek = dict(config.get("deepseek") or {})
    if model:
        deepseek["model"] = model
    config = dict(config, ai_chat=chat, deepseek=deepseek)
    STYLES.configure(config)
    start = TZ.localize(datetime.strptime(script["time"], "%Y-%m-%d %H:%M")) if script.get("time") \
        else datetime.now(TZ)
    clock = SimpleNamespace(now=start)
    ids = {str(name): str(openid) for name, openid in (script.get("ids") or {}).items()}
    names = {openid: name for name, openid in ids.items()}
    gateway, store = Gateway(), ChatStore(":memory:")
    ai = AIChatModule(gateway, config, store=store, clock=lambda: clock.now,
                      nickname_of=lambda group, member: names.get(member), library_path=None)
    ai.teams_today, ai.social_today = today_from(script)
    judged = []
    perceive = ai.perceive

    async def recording(name, said, window):
        found = await perceive(name, said, window)
        judged.append(found)
        return found

    ai.perceive = recording
    print(f"\n== run {run} · {ai.model} · 北京时间 {start.strftime('%m-%d %H:%M')}", file=out)
    for i, line in enumerate(script["lines"]):
        nickname, text = split_line(line)
        clock.now = start + timedelta(minutes=i)
        message = SimpleNamespace(group_openid=GROUP, member_openid=ids.get(nickname, f"R-{nickname}"),
                                  nickname=nickname, content=text, id=f"r{run}-{i}", role=None,
                                  mentions_bot=True, is_bot=False)
        before = len(gateway.sent)
        with STYLES.scope(GROUP):
            await ai.handle_ai_chat(message)
            await ai.settle()
        reply = " + ".join(gateway.sent[before:]) or "（没有回复）"
        register, length = judged[-1] if len(judged) > i else (None, None)
        temperature = ai.register_temperatures.get(register, ai.temperature)
        tag = f"{register or '默认'}·{length or '-'} {temperature}" if ai.dynamic else f"{temperature}"
        print(f"{nickname}：{text}\n  → [{tag}] {reply}", file=out)
    replies = gateway.sent
    actions = [a for reply in replies for a in ACTION.findall(reply)]
    repeated = [a for a, n in Counter(actions).items() if n > 1]
    average = sum(len(r) for r in replies) // max(1, len(replies))
    print(f"-- {len(replies)} replies, about {average} characters each, {len(actions)} bracketed actions, "
          f"repeated: {'、'.join(repeated) or 'none'}", file=out)
    if remember:
        show_memories(store, names, "noted during the chat", out)
        for group, member in store.members_with("episode"):
            await ai.consolidate(group, member)
        show_memories(store, names, "after a night's consolidation", out)


def show_memories(store, names, when, out):
    print(f"-- memories {when}:", file=out)
    for row in store.all_memories():
        who = names.get(row["member_openid"], row["member_openid"].removeprefix("R-"))
        print(f"   {who} · {row['kind']} · importance {row['importance']}: {row['text']}", file=out)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Rehearse AI chat against the real DeepSeek.")
    parser.add_argument("script", nargs="?", default=content_path("prompts", "rehearsal.yaml"))
    parser.add_argument("--model", help="the reply model, e.g. deepseek-v4-pro")
    parser.add_argument("--times", type=int, default=1)
    parser.add_argument("--memory", action="store_true", help="take memory notes too (more calls)")
    args = parser.parse_args(argv)
    config = load_config()
    if not (config.get("deepseek") or {}).get("api_key"):
        print("config.yaml has no deepseek.api_key; the rehearsal needs the real DeepSeek.")
        return 1
    script = load_script(args.script)
    for run in range(1, args.times + 1):
        asyncio.run(play(script, config, run, args.model, args.memory))
    return 0


if __name__ == "__main__":
    sys.exit(main())
