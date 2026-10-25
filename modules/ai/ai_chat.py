"""AI chat with DeepSeek, with a memory of the conversation and of each member.

Adapted from seekchat (Desktop/ai). In each group, everyone who @s the bot
shares one conversation:
- context window: the newest turns that fit ai_chat.context_tokens, each
  member's line labelled with their nickname;
- rolling summary: once ai_chat.summarize_after turns have scrolled out of
  the window, a cheap call folds them into the group's summary;
- memory: after every reply an observer call notes what the speaker said
  about themselves (modules/ai/memory.py).
Summary and memories travel in a separate "evidence" system message that is
labelled as data, not instructions (modules/ai/context.py).

The persona is the style's system_prompt; the rules around it are in
prompts/chat.yaml. Raw turns are deleted after ai_chat.keep_days.
"""
import asyncio
import functools
import os
import re
from datetime import datetime
import pytz
import yaml
from botpy import logging
from openai import OpenAI
from core.config import content_path, load_config, root_path
from core.style import current, join, placeholders, render, t
from modules.ai import context, memory
from modules.ai.store import ChatStore


_log = logging.get_logger()

# DeepSeek discontinued "deepseek-chat" on 2026-07-24; V4.1 Flash is "deepseek-flash".
DEFAULT_MODEL = "deepseek-flash"
CHAT_PROMPTS_FILE = content_path("prompts", "chat.yaml")
# What each line of prompts/chat.yaml may fill in, besides {name} (the style's name).
PROMPT_FIELDS = {
    "group_rules": {"member"},
    "memory_rules": set(),
    "evidence_policy": set(),
    "evidence_title": set(),
    "summary_prefix": set(),
    "memory_title": set(),
    "empty_mention": {"member"},
    "summary": set(),
    "observer": {"member", "known", "said", "reply", "max_text", "max_adds"},
}
# Commands this module answers before chatting. Listed so team-game actions can't take them.
COMMANDS = ("/我的记忆", "/忘记", "/重置对话", "/服务器QA")
FORGET_ALL_WORDS = ("全部", "所有", "all")
MEMORY_SPEAKERS = 5         # members whose memories ride along: the speaker plus recent ones
DAY = 86400


def load_chat_prompts(path=CHAT_PROMPTS_FILE):
    """Read and check prompts/chat.yaml. A mistake stops the bot at startup, naming the line."""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError("chat.yaml 的最外层应该是「键: 值」的形式")
    prompts = {}
    for key, fields in PROMPT_FIELDS.items():
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"chat.yaml 缺少 {key}")
        try:
            unknown = placeholders(data[key]) - fields - {"name"}
        except ValueError as e:
            raise ValueError(f"chat.yaml 的 {key}：花括号写得不对（{e}）。想写真正的花括号要写两个：{{{{ 和 }}}}")
        if unknown:
            allowed = "、".join("{" + f + "}" for f in sorted(fields | {"name"}))
            raise ValueError(f"chat.yaml 的 {key}：程序不会填写 {'、'.join('{' + u + '}' for u in sorted(unknown))}，"
                             f"这一项能用的有：{allowed}")
        prompts[key] = data[key]
    return prompts


class AIChatModule:
    def __init__(self, gateway, config=None, store=None, client=None, is_admin=None, clock=None,
                 prompts_path=CHAT_PROMPTS_FILE):
        self.gw = gateway
        self.config = load_config() if config is None else config
        deepseek = self.config.get("deepseek") or {}
        chat = self.config.get("ai_chat") or {}
        api_key = deepseek.get("api_key")
        # AI chat is optional: without a DeepSeek key the rest of the bot
        # (Epic, Steam, Minecraft, blackjack, sleep, ...) still runs normally.
        self.enabled = bool(api_key) or client is not None
        self.model = str(deepseek.get("model") or DEFAULT_MODEL)
        self.helper_model = str(deepseek.get("helper_model") or self.model)
        self.thinking = bool(deepseek.get("thinking", False))
        self.context_tokens = int(chat.get("context_tokens", 3000))
        self.summarize_after = int(chat.get("summarize_after", 20))
        self.keep_days = int(chat.get("keep_days", 7))
        self.memory_on = bool(chat.get("memory", True))
        self.memories_per_member = int(chat.get("memories_per_member", 12))
        self.is_admin = is_admin or (lambda group, member, role=None: False)
        self.tz = pytz.timezone('Asia/Shanghai')
        self.clock = clock or (lambda: datetime.now(self.tz))
        self.summarizing = set()    # groups with a summary call in flight
        # Bumped whenever a group's chat or memories are deleted, so a summary or
        # memory note computed from the deleted text is thrown away when it lands.
        self.generation = {}
        self.observing = {}         # (group, member) -> lock, so one member's notes are taken in order
        self.background = set()     # running observer/summary tasks, kept so they finish
        self.task = None
        if not self.enabled:
            self.client = self.qa_client = self.store = self.prompts = self.qa_system_prompt = None
            print("[AIChat] 未配置 deepseek.api_key，AI 聊天与 /服务器QA 功能已禁用。"
                  " (No deepseek.api_key configured; AI chat and /服务器QA are disabled.)")
            return
        self.client = client or OpenAI(api_key=api_key, base_url="https://api.deepseek.com", timeout=60)
        self.qa_client = client or OpenAI(api_key=deepseek.get("qa_api_key", api_key),
                                          base_url="https://api.deepseek.com", timeout=60)
        self.qa_system_prompt = self._load_qa_prompt()
        self.prompts = load_chat_prompts(prompts_path)
        self.store = store or ChatStore(root_path("data", "chat.db"))
        self.commands = {"/我的记忆": self.cmd_my_memory, "/忘记": self.cmd_forget, "/重置对话": self.cmd_reset}

    def _load_qa_prompt(self):
        """Load the server-QA system prompt from its dedicated prompt file."""
        default_prompt = "你是一个专业的Minecraft服务器管理助手，擅长回答有关服务器配置、管理和故障排除的问题。请提供准确、专业的回答。"
        prompt_path = content_path("prompts", "server_qa_prompt.yaml")
        try:
            if os.path.exists(prompt_path):
                with open(prompt_path, 'r', encoding='utf-8') as f:
                    prompt_data = yaml.safe_load(f)
                    return prompt_data.get('system_prompt', default_prompt)
            else:
                return default_prompt
        except Exception as e:
            print(f"加载提示词文件出错: {str(e)}")
            return default_prompt

    # ------------------------------------------------------------------ helpers

    async def _complete(self, client, model, messages, thinking=False):
        """Run a blocking chat completion in a worker thread and return its text.

        The OpenAI client is synchronous; calling it directly from a coroutine
        would stall the bot's event loop for the whole duration of the request.
        DeepSeek thinks before answering unless told not to, which is slower
        and dearer than a group chat needs.
        """
        call = functools.partial(
            client.chat.completions.create,
            model=model,
            messages=messages,
            stream=False,
            extra_body={"thinking": {"type": "enabled" if thinking else "disabled"}},
        )
        response = await asyncio.get_running_loop().run_in_executor(None, call)
        return response.choices[0].message.content or ""

    def _now(self):
        return int(self.clock().timestamp())

    async def _reply(self, message, text):
        try:
            await self.gw.send(message.group_openid, text, reply_to=message.id)
        except Exception as e:
            _log.error(f"[AIChat] 回复失败: {e}")

    def _spawn(self, coroutine):
        """Run follow-up work (memory notes, summaries) without holding up the reply."""
        task = asyncio.create_task(coroutine)
        self.background.add(task)
        task.add_done_callback(self.background.discard)

    async def settle(self):
        """Wait for follow-up work to finish (tests, shutdown)."""
        while self.background:
            await asyncio.gather(*list(self.background), return_exceptions=True)

    # ------------------------------------------------------------------ chat

    async def handle_ai_chat(self, message):
        if not self.enabled:
            # Returning False lets main.py fall through to its default log
            # instead of silently swallowing every unmatched message.
            return False
        text = (message.content or "").strip()
        if "/服务器QA" in text:
            return await self.handle_server_qa(message)
        word = next((w for w in self.commands if text.startswith(w)), None)
        if word is not None:
            await self.commands[word](message, text[len(word):].strip())
            return True
        await self.chat(message, text)
        return True

    def _prompt(self, group, member_name, window):
        """(instructions, evidence) for one reply."""
        instructions = [current().system_prompt, render(self.prompts["group_rules"], member=member_name)]
        evidence = []
        summary = self.store.summary(group)
        if summary:
            evidence.append(f"{render(self.prompts['summary_prefix'])}\n{summary['summary']}")
        if self.memory_on:
            # The members speaking in the window, the current speaker first.
            speakers = {}
            for turn in reversed(window):
                if turn["role"] == "user" and turn["member_openid"] not in speakers:
                    speakers[turn["member_openid"]] = turn["name"]
            known = [(name, [m["text"] for m in self.store.memories(group, openid)])
                     for openid, name in list(speakers.items())[:MEMORY_SPEAKERS]]
            block = memory.evidence(render(self.prompts["memory_title"]), known)
            if block:
                instructions.append(render(self.prompts["memory_rules"]))
                evidence.append(block)
        return context.compose(instructions, evidence, render(self.prompts["evidence_policy"]),
                               render(self.prompts["evidence_title"]))

    async def chat(self, message, text):
        group, member = message.group_openid, message.member_openid
        name = message.nickname or t("common.someone")
        said = text or render(self.prompts["empty_mention"], member=name)
        self.store.add_turn(group, member, name, "user", said, self._now())
        window = context.select_window(self.store.recent_turns(group), self.context_tokens)
        instructions, evidence = self._prompt(group, name, window)
        try:
            reply = (await self._complete(self.client, self.model,
                                          context.build_messages(instructions, evidence, window),
                                          thinking=self.thinking)).strip()
        except Exception as e:
            await self._reply(message, t("ai.error", error=e))
            return
        if not reply:
            return
        await self._reply(message, reply)
        self.store.add_turn(group, None, current().name, "assistant", reply, self._now())
        if self.memory_on and member:
            self._spawn(self.observe(group, member, name, said, reply))
        self._spawn(self.maybe_summarize(group))

    async def observe(self, group, member, name, said, reply):
        """Note what `member` just said about themselves. Failures only cost a missed note."""
        lock = self.observing.setdefault((group, member), asyncio.Lock())
        async with lock:
            try:
                generation = self.generation.get(group, 0)
                known = self.store.memories(group, member)
                texts = [m["text"] for m in known]
                prompt = render(self.prompts["observer"], member=name, said=memory.excerpt(said),
                                reply=memory.excerpt(reply), known=memory.numbered(texts, "（还没有）"),
                                max_text=memory.MAX_MEMORY_TEXT, max_adds=memory.MAX_ADDS_PER_TURN)
                raw = await self._complete(self.client, self.helper_model, [{"role": "user", "content": prompt}])
                if self.generation.get(group, 0) != generation:
                    return
                facts, forget = memory.parse_observation(raw, texts)
                for n in forget:
                    self.store.delete_memory(known[n - 1]["id"])
                if not facts:
                    return
                # A full notebook drops its oldest entries to make room.
                kept = [m for i, m in enumerate(known, 1) if i not in forget]
                for old in kept[:max(0, len(kept) + len(facts) - self.memories_per_member)]:
                    self.store.delete_memory(old["id"])
                for fact in facts:
                    self.store.add_memory(group, member, fact, self._now())
            except Exception as e:
                _log.warning(f"[AIChat] 整理记忆失败: {e}")

    async def maybe_summarize(self, group, up_to_id=None):
        """Fold turns that left the window into the group's summary once there are enough of them.

        With `up_to_id`, fold everything up to that turn now, window or not:
        housekeeping does this before deleting old turns, so they aren't lost.
        """
        if group in self.summarizing:
            return
        generation = self.generation.get(group, 0)
        before = self.store.summary(group)
        done = before["up_to_id"] if before else 0
        turns = self.store.recent_turns(group)
        if up_to_id is None:
            pending = context.unsummarized(turns, context.select_window(turns, self.context_tokens), done)
            if len(pending) <= self.summarize_after:
                return
        else:
            pending = self.store.turns_between(group, done, up_to_id)
            if not pending:
                return
        self.summarizing.add(group)
        try:
            lines = "\n".join(f"{turn['name']}：{turn['content']}" for turn in pending)
            text = await self._complete(self.client, self.helper_model, [
                {"role": "system", "content": render(self.prompts["summary"])},
                {"role": "user", "content": f"此前总结：\n{before['summary'] if before else '（无）'}\n\n新增对话节选：\n{lines}"},
            ])
            # /重置对话 or a deletion may have run while the call was out: don't bring the text back.
            if self.generation.get(group, 0) != generation:
                return
            if text.strip():
                self.store.set_summary(group, text.strip(), pending[-1]["id"], self._now())
        except Exception as e:
            _log.warning(f"[AIChat] 生成总结失败: {e}")
        finally:
            self.summarizing.discard(group)

    # ------------------------------------------------------------------ commands

    async def cmd_my_memory(self, message, args):
        """/我的记忆: what the bot has noted about me in this group."""
        if not self.memory_on:
            await self._reply(message, t("ai.memory.disabled"))
            return
        rows = self.store.memories(message.group_openid, message.member_openid)
        if not rows:
            await self._reply(message, t("ai.memory.empty"))
            return
        lines = [t("ai.memory.title", member=message.nickname or t("common.someone"), count=len(rows))]
        lines += [t("ai.memory.row", n=i, text=row["text"]) for i, row in enumerate(rows, 1)]
        lines.append(t("ai.memory.footer"))
        await self._reply(message, "\n".join(lines))

    async def cmd_forget(self, message, args):
        """/忘记 序号… or /忘记 全部: delete my own memories."""
        group, member = message.group_openid, message.member_openid
        if args in FORGET_ALL_WORDS:
            count = self.store.clear_memories(group, member)
            await self._reply(message, t("ai.memory.forgot_all", count=count))
            return
        numbers = [int(n) for n in re.findall(r"\d+", args)]
        if not numbers:
            await self._reply(message, t("ai.memory.usage"))
            return
        rows = self.store.memories(group, member)
        missing = [n for n in numbers if not 1 <= n <= len(rows)]
        if missing:
            await self._reply(message, t("ai.memory.no_such", n=join([str(n) for n in missing])))
            return
        for n in sorted(set(numbers)):
            self.store.delete_memory(rows[n - 1]["id"])
        await self._reply(message, t("ai.memory.forgot", n=join([str(n) for n in sorted(set(numbers))])))

    async def cmd_reset(self, message, args):
        """/重置对话 (admins): start the group's conversation over. Members' memories stay."""
        if not self.is_admin(message.group_openid, message.member_openid, message.role):
            await self._reply(message, t("common.not_admin"))
            return
        self.store.reset_chat(message.group_openid)
        self._deleted(message.group_openid)
        await self._reply(message, t("ai.reset"))

    async def handle_server_qa(self, message):
        """Handle server-QA mode, which uses its own dedicated system prompt."""
        try:
            question = message.content.replace("/服务器QA", "").strip()
            if not question:
                await self._reply(message, t("ai.qa_usage"))
                return True
            reply_content = await self._complete(self.qa_client, self.model, [
                {"role": "system", "content": self.qa_system_prompt},
                {"role": "user", "content": question},
            ], thinking=self.thinking)
            await self._reply(message, reply_content)
            return True
        except Exception as e:
            await self._reply(message, t("ai.qa_error", error=e))
            return True

    # ------------------------------------------------------------------ housekeeping

    def _deleted(self, group):
        self.generation[group] = self.generation.get(group, 0) + 1

    def forget_member(self, group, member):
        if self.enabled:
            self.store.forget_member(group, member)
            self._deleted(group)

    def forget_group(self, group):
        if self.enabled:
            self.store.forget_group(group)
            self._deleted(group)

    async def housekeeping(self):
        """Keep the retention promise: turns older than keep_days are deleted, after
        anything not yet in the summary has been folded in."""
        cutoff = self._now() - self.keep_days * DAY
        for group in self.store.groups_with_turns_before(cutoff):
            last_old = self.store.last_turn_before(group, cutoff)
            if last_old is not None:
                await self.maybe_summarize(group, up_to_id=last_old)
        self.store.prune(cutoff)

    async def run_scheduler(self):
        if not self.enabled:
            return
        _log.info(f"[AIChat] AI 聊天：模型 {self.model}，上下文约 {self.context_tokens} token，"
                  f"长期记忆{'开启' if self.memory_on else '关闭'}，聊天记录保留 {self.keep_days} 天")
        while True:
            try:
                await self.housekeeping()
            except Exception as e:
                _log.error(f"[AIChat] 清理聊天记录出错: {e}")
            await asyncio.sleep(3600)
