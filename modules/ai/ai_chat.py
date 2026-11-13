"""AI chat with DeepSeek, with a memory of the conversation and of each member.

Adapted from seekchat (Desktop/ai). In each group, everyone who @s the bot
shares one conversation; with ai_chat.hear_all the rest of the group's chat
joins it too, marked as not addressed to the bot:
- context window: the newest turns that fit ai_chat.context_tokens, each
  member's line labelled with their nickname;
- rolling summary: once ai_chat.summarize_after turns have scrolled out of
  the window, a cheap call folds them into the group's summary;
- memory: after every reply an observer call notes what the speaker said
  about themselves, and a check call drops notes that were only jokes
  (modules/ai/memory.py).
Summary and memories travel in a separate "evidence" system message that is
labelled as data, not instructions (modules/ai/context.py). So do the Beijing
time, today's team game, birthdays and open poll, and the actions the bot's
last replies used, which it's told to vary.

The persona is the style's system_prompt; the rules around it are in
prompts/chat.yaml. Raw turns are deleted after ai_chat.keep_days.

ai_chat.special_people in config.yaml says who particular members are (the
bot's author, say). They're recognised by member_openid, never by nickname,
so someone who copies a nickname isn't taken for them.

Like seekchat, she has a life of her own: each day one cheap call plans a few
small things in her day (ai_chat.her_day), which ride along as evidence. In
the groups listed under ai_chat.topics she speaks up when nobody has for a
while, and with a MiniMax key (config.yaml voice) she can answer with a short
voice message (modules/ai/voice.py).
"""
import asyncio
import functools
import hashlib
import os
import re
import time
from typing import NamedTuple
from urllib.parse import quote
from datetime import datetime
import pytz
import yaml
from botpy import logging
from openai import OpenAI
from core.config import content_path, load_config, root_path
from core.style import STYLES, current, join, placeholders, render, t
from modules.ai import context, gallery, memory, search, stickers, vision, voice
from modules.ai.store import ChatStore


_log = logging.get_logger()

# DeepSeek discontinued "deepseek-chat" on 2026-07-24; V4.1 Flash is "deepseek-flash".
DEFAULT_MODEL = "deepseek-flash"
CHAT_PROMPTS_FILE = content_path("prompts", "chat.yaml")
# After QQ refuses a sticker inside a reply, stickers go out as their own messages for this long.
INLINE_RETRY_SECONDS = 3600
# After a voice message fails, her [语音:…] lines go out as text for this long.
VOICE_RETRY_SECONDS = 3600
# 她带话题, unless ai_chat.topics says otherwise: speak up after this many hours of silence, only
# between these hours (Beijing time), at most this many times a day.
TOPIC_QUIET_HOURS = 3
TOPIC_HOURS = (10, 22)
TOPICS_PER_DAY = 2
DAY_START_HOUR = 5          # her day is planned ahead for the topic groups from this hour on
DAY_RETRY_SECONDS = 1800    # after a failed plan, wait this long before asking again
TICK_SECONDS = 300          # how often the scheduler looks at quiet groups
HEARD_SAVE_SECONDS = 60     # a group's last message time is written down at most this often
# What each line of prompts/chat.yaml may fill in, besides {name} (the style's name).
PROMPT_FIELDS = {
    "group_rules": {"member"},
    "honesty": set(),
    "examples": {"examples"},
    "bad_examples": {"examples"},
    "reply_shape": set(),
    "declining": set(),
    "rewrite": {"draft", "phrase", "phrases"},
    "length_short": set(),
    "length_medium": set(),
    "length_long": set(),
    "lore": {"lore"},
    "taught_rules": set(),
    "taught_title": set(),
    "teach_check": {"keys", "text"},
    "sticker_draft": set(),
    "pet_name": {"member", "known", "impression", "habits", "before", "who"},
    "pet_name_who": {"who"},
    "pet_name_habits": {"valid", "night", "morning", "best", "badges"},
    "pet_name_before": {"before"},
    "pet_name_rules": set(),
    "pet_name_title": {"member", "nickname"},
    "stickers": {"stickers", "max"},
    "voice": {"max_chars"},
    "special_people": {"people"},
    "special_line": {"member", "note"},
    "special_speaker": {"member", "note"},
    "special_namesake": {"member"},
    "memory_rules": set(),
    "day_rules": set(),
    "search_rules": set(),
    "vision_rules": set(),
    "vision_strangers": set(),
    "vision_characters": set(),
    "vision_missing": set(),
    "evidence_policy": set(),
    "evidence_title": set(),
    "now": {"date", "weekday", "time"},
    "today_teams": {"teams"},
    "team_line": {"captain", "members"},
    "today_birthdays": {"members"},
    "today_poll": {"question", "options"},
    "today_opinions": {"topic", "count"},
    "today_soup": {"surface"},
    "soup_question": {"surface", "truth", "points", "said"},
    "soup_guess": {"surface", "truth", "points", "said"},
    "recent_actions": {"actions"},
    "her_day": {"events"},
    "summary_prefix": set(),
    "memory_title": set(),
    "impression_title": set(),
    "web_results": {"query", "results"},
    "picture_characters": {"names"},
    "empty_mention": {"member"},
    "picture_mark": set(),
    "someone": set(),
    "person_check": set(),
    "character_reference": {"character"},
    "character_reference_note": {"character", "note"},
    "character_check": set(),
    "topic": {"hours"},
    "topic_cue": {"hours"},
    "summary": set(),
    "digest_comment": {"digest"},
    "opinion_summary": {"topic", "opinions"},
    "fortune_comment": {"member", "level", "good", "bad", "said"},
    "day": {"date", "weekday"},
    "observer": {"member", "known", "impression", "said", "reply", "max_text", "max_adds", "max_impression"},
    "register": {"member", "said", "last", "search"},
    "register_search": set(),
    "memory_check": {"member", "said", "reply", "notes"},
    "consolidation": {"member", "facts", "notes", "max_text", "max_facts"},
}
# Commands this module answers before chatting. Listed so team-game actions can't take them.
COMMANDS = ("/我的记忆", "/忘记", "/重置对话", "/看图测试", "/服务器QA", "/教她", "/她学会的", "/记下", "/不记",
            "/外号", "/投稿表情", "/表情投稿", "/她的表情", "/收下", "/不收")
FORGET_ALL_WORDS = ("全部", "所有", "all")
FORGET_IMPRESSION_WORDS = ("印象",)
DELETE_WORDS = ("删除", "取消", "清除", "忘掉")
TEACH_PER_DAY = 3           # things one member can teach her a day (/教她); admins as many as they like
TAUGHT_MAX = 100            # what one group can have taught her in all...
TAUGHT_WAITING = 20         # ...and how many more can be waiting for an admin
SUBMITS_PER_DAY = 3         # stickers one member can send in a day (/投稿表情); admins as many as they like
GROUP_SUBMITS_PER_DAY = 20  # and a whole group, admins included: each one costs two looks at a picture
MEMBER_STICKERS_MAX = 20    # the stickers one group's members can have given her in all: QQ's image
MEMBER_STICKERS_WAITING = 10    # library holds 100 a bot. And how many more can be waiting for an admin
OFFER_WAIT_DAYS = 14        # what has waited for an admin this long is dropped
LIST_PAGE = 15              # entries on a page of /她学会的 or /她的表情
MAX_MESSAGE_LENGTH = 900    # a longer list goes out as several messages
PET_NAMES_PER_DAY = 3       # nicknames she'll think up for one member a day (/外号)
PET_NAME_AGAIN_WORDS = ("换", "换一个", "换个", "再来", "再来一个")
PET_NAME_DROP_WORDS = ("不要", "不要了") + DELETE_WORDS
CONSOLIDATE_HOUR = 4        # Beijing time: when the night's consolidation runs, like sleep
NOTES_LEFT_DAYS = 2         # recent notes this old are consolidated whenever housekeeping runs
HEARD_TEXT = 300            # characters kept of each message that didn't @ the bot (hear_all)
MEMORY_SPEAKERS = 5         # members whose memories ride along: the speaker plus recent ones
DIGEST_COMMENT_LIMIT = 120  # characters; the prompt asks for 60
OPINION_SUMMARY_MIN = 3     # a 征集意见 with fewer opinions than this ends without her summary
OPINION_SUMMARY_LIMIT = 300  # characters; the prompt asks for 150
FORTUNE_COMMENT_LIMIT = 120  # characters; the prompt asks for 60
DEFAULT_TEMPERATURE = 1.0   # chat replies when dynamic_temperature is off or its judgement fails
WEEKDAYS = "一二三四五六日"
DAY = 86400


class Offered(NamedTuple):
    """Something members offer her that an admin accepts first: what they teach her (/教她), the
    stickers they send in (/投稿表情). Both are listed, kept, dropped and taken back the same way."""
    table: str      # where ChatStore keeps them
    lines: str      # where the style has their lines
    offer: str      # the command members offer one with; "<offer> 删除 编号" takes one's own back
    listing: str    # the command that lists them
    keep: str       # the admins' two commands, each with a number
    drop: str


TAUGHT = Offered("taught", "ai.teach", "/教她", "/她学会的", "/记下", "/不记")
STICKERS = Offered("member_stickers", "ai.submit", "/投稿表情", "/她的表情", "/收下", "/不收")


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


def read_special_people(value):
    """ai_chat.special_people from config.yaml as {member_openid: note}. A broken entry is
    skipped with a warning rather than stopping the bot."""
    if not value:
        return {}
    if not isinstance(value, dict):
        _log.warning("[AIChat] config.yaml 的 ai_chat.special_people 要写成「ID: 说明」的形式，已忽略")
        return {}
    people = {}
    for openid, note in value.items():
        if not isinstance(note, str) or not note.strip():
            _log.warning(f"[AIChat] ai_chat.special_people 里的 {str(openid)[:8]}… 没写说明，已跳过")
            continue
        people[str(openid).strip()] = note.strip()
    return people


class AIChatModule:
    def __init__(self, gateway, config=None, store=None, client=None, is_admin=None, clock=None,
                 prompts_path=CHAT_PROMPTS_FILE, nickname_of=None, stickers_dir=None,
                 library_path=stickers.LIBRARY_FILE, speaker=None, searcher=None, fetch_picture=None,
                 gallery_dir=None, member_stickers_dir=None):
        self.gw = gateway
        self.config = load_config() if config is None else config
        deepseek = self.config.get("deepseek") or {}
        chat = self.config.get("ai_chat") or {}
        api_key = deepseek.get("api_key")
        # AI chat is optional: without a DeepSeek key the rest of the bot
        # (Epic, Steam, Minecraft, blackjack, sleep, ...) still runs normally.
        self.enabled = bool(api_key) or client is not None
        self.model = str(deepseek.get("model") or DEFAULT_MODEL)
        # Summaries, memory notes and checks, and the pre-reply judgement stay on the flash tier even
        # when replies use a bigger model.
        self.helper_model = str(deepseek.get("helper_model") or DEFAULT_MODEL)
        self.thinking = bool(deepseek.get("thinking", False))
        temperature = deepseek.get("temperature", DEFAULT_TEMPERATURE)
        self.temperature = None if temperature is None else float(temperature)     # None: DeepSeek's default
        self.context_tokens = int(chat.get("context_tokens", 3000))
        self.summarize_after = int(chat.get("summarize_after", 20))
        self.keep_days = int(chat.get("keep_days", 7))
        self.memory_on = bool(chat.get("memory", True))
        self.memories_per_member = int(chat.get("memories_per_member", 8))     # long-term facts each
        self.consolidated_on = None     # the day the nightly consolidation last ran
        self.memory_check = bool(chat.get("memory_check", True))
        self.digest_comment = bool(chat.get("digest_comment", True))
        self.opinion_summary = bool(chat.get("opinion_summary", True))
        # 今日运势 reading: off unless asked for, as it sends members' own messages that didn't @ her.
        self.fortune = bool(chat.get("fortune", False))
        # /教她: members teach her what words mean in their group; an admin accepts each one.
        self.teach = bool(chat.get("teach", True))
        # /外号: she gives a member a nickname from what she remembers of them and how they take part.
        self.pet_names = bool(chat.get("pet_names", True))
        # Set by main.py: (group, member) -> what the group manager's record says of how they take
        # part, as ManagerModule.habits returns it, or None.
        self.habits_of = None
        self.tries = {}             # (what, group, member) -> (day, how often they did it that day): daily limits
        self.game_state = bool(chat.get("game_state", True))
        # seekchat's 动态想象力: one cheap call before each reply picks its temperature and length.
        self.dynamic = bool(chat.get("dynamic_temperature", True))
        picked = chat.get("temperatures") if isinstance(chat.get("temperatures"), dict) else {}
        self.register_temperatures = {register: float(picked.get(register, value))
                                      for register, value in context.REGISTERS.items()}
        # Every group message joins her context, not just the ones that @ her (see the privacy policy).
        self.hear_all = bool(chat.get("hear_all", False))
        self.heard_since = {}       # group -> messages heard since the last summary check
        # Stickers she can send under a reply (local/stickers/); a URL to fetch them from, if QQ
        # won't take a direct upload.
        self.stickers, problems = stickers.load(stickers_dir) if chat.get("stickers", True) else ({}, [])
        for problem in problems:
            _log.warning(f"[AIChat] 表情包：{problem}")
        self.stickers_url = str(chat.get("stickers_url") or "")
        # Inside the reply, small, as markdown showing the image from QQ's image library; switched
        # off, or for a while after QQ refused that, as a message of its own under the text.
        self.stickers_inline = bool(chat.get("stickers_inline", True))
        self.sticker_size = int(chat.get("sticker_size", stickers.SIZE))
        self.inline_off_until = 0.0
        # /投稿表情: members send her stickers for their own group. Off unless asked for, as their
        # pictures go to DeepSeek to be looked at and are kept on this machine.
        self.member_stickers = bool(chat.get("member_stickers", False))
        self.member_stickers_dir = member_stickers_dir or stickers.MEMBER_DIR
        self.library = stickers.Library(gateway, {os.path.basename(s.path) for s in self.stickers.values()},
                                        library_path) if self.stickers or self.member_stickers else None
        # 她的一天: each day one cheap call plans a few small things in her day.
        self.her_day = bool(chat.get("her_day", True))
        self.planning = set()       # styles with a planning call out
        self.plan_failed = {}       # style name -> when its plan last failed
        # 她带话题: in the groups listed, she speaks up when nobody has for a while.
        topics = chat.get("topics") if isinstance(chat.get("topics"), dict) else {}
        self.topic_groups = [str(group) for group in topics.get("groups") or []]
        self.quiet_hours = float(topics.get("quiet_hours", TOPIC_QUIET_HOURS))
        hours = topics.get("hours")
        if not (isinstance(hours, (list, tuple)) and len(hours) == 2):
            if hours is not None:
                _log.warning(f"[AIChat] ai_chat.topics.hours 要写成 [开始, 结束]，例如 [10, 22]；"
                             f"先用 {TOPIC_HOURS[0]} 点到 {TOPIC_HOURS[1]} 点")
            hours = TOPIC_HOURS
        self.topic_hours = (int(hours[0]), int(hours[1]))
        self.topics_per_day = int(topics.get("per_day", TOPICS_PER_DAY))
        self.heard = {}             # group -> when a member last said anything there
        self.heard_saved = {}       # group -> the time last written to chat.db
        self.topic_wait = {}        # group -> no topic before this, after one failed
        # 语音: MiniMax says her [语音:…] lines (config.yaml voice). Tests and rehearsals pass a speaker.
        spoken = self.config.get("voice") or {}
        if speaker is None and spoken.get("api_key") and spoken.get("voice_id"):
            try:
                speaker = voice.MiniMax(str(spoken["api_key"]), str(spoken["voice_id"]),
                                        region=str(spoken.get("region") or "global"),
                                        model=str(spoken.get("model") or voice.MODEL),
                                        speed=float(spoken.get("speed", 1.0)),
                                        host=str(spoken["host"]) if spoken.get("host") else None)
            except ValueError as e:
                _log.warning(f"[Voice] {e}。语音没有打开")
        self.speaker = speaker
        self.voice_per_day = int(spoken.get("per_day", voice.PER_DAY))
        self.voice_off_until = 0
        # 联网搜索: she looks up what she can't know. Tests and rehearsals may pass their own searcher.
        if searcher is None and api_key:
            searcher = search.DeepSeekSearch(str(api_key))
        self.searcher = searcher if chat.get("web_search", True) else None
        self.search_per_day = int(chat.get("web_search_per_day", search.PER_DAY))
        self.search_off_until = 0
        # 看图: in the groups listed, she looks at the pictures members @ her with.
        looking = chat.get("vision") if isinstance(chat.get("vision"), dict) else {}
        self.vision_groups = [str(group) for group in looking.get("groups") or []]
        self.vision_per_day = int(looking.get("per_day", vision.PER_DAY))
        self.vision_model = str(looking.get("model") or DEFAULT_MODEL)
        self.vision_off_until = 0
        # Reference pictures of characters (local/characters/), the only source of their names.
        self.references, problems = gallery.load(gallery_dir) if self.vision_groups else ([], [])
        for problem in problems:
            _log.warning(f"[AIChat] 角色参考图：{problem}")
        self.careful_until = {}     # group -> her replies stay careful until this, after real people in a picture
        # Today's team game and birthdays/poll, set by main.py: group -> what teams.today_for_ai and
        # social.today_for_ai return. Without them the bot only knows the time.
        self.teams_today = self.social_today = None
        self.special = read_special_people(chat.get("special_people"))
        self.is_admin = is_admin or (lambda group, member, role=None: False)
        # A member's current nickname in a managed group, for special people who aren't in the chat.
        self.nickname_of = nickname_of or (lambda group, member: None)
        self.tz = pytz.timezone('Asia/Shanghai')
        self.clock = clock or (lambda: datetime.now(self.tz))
        self.started = self._now()  # a group with no message since counts as quiet from here
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
        if self.library is not None and self.member_stickers:     # QQ's library keeps their images too
            self.library.files |= {row["file"] for row in self.store.all_member_stickers()}
        self.fetch_picture = fetch_picture or vision.fetch
        self.commands = {"/我的记忆": self.cmd_my_memory, "/忘记": self.cmd_forget, "/重置对话": self.cmd_reset,
                         "/看图测试": self.cmd_picture_test, "/外号": self.cmd_pet_name,
                         "/教她": self.cmd_teach, "/投稿表情": self.cmd_submit, "/表情投稿": self.cmd_submit}
        for offered in (TAUGHT, STICKERS):
            self.commands.update({offered.listing: functools.partial(self.cmd_offered, offered),
                                  offered.keep: functools.partial(self.cmd_keep, offered),
                                  offered.drop: functools.partial(self.cmd_drop, offered)})

    @property
    def features(self):
        """The things members can do with her that can be switched off, and are on: /帮助 lists them."""
        return [name for name in ("pet_names", "teach", "member_stickers") if self.enabled and getattr(self, name)]

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

    async def _complete(self, client, model, messages, thinking=False, temperature=None):
        """Run a blocking chat completion in a worker thread and return its text.

        The OpenAI client is synchronous; calling it directly from a coroutine
        would stall the bot's event loop for the whole duration of the request.
        DeepSeek thinks before answering unless told not to, which is slower
        and dearer than a group chat needs. Only chat replies pass a temperature;
        the helper calls keep DeepSeek's default, so their JSON stays steady.
        """
        options = {} if temperature is None else {"temperature": temperature}
        call = functools.partial(
            client.chat.completions.create,
            model=model,
            messages=messages,
            stream=False,
            extra_body={"thinking": {"type": "enabled" if thinking else "disabled"}},
            **options,
        )
        response = await asyncio.get_running_loop().run_in_executor(None, call)
        return response.choices[0].message.content or ""

    def _now(self):
        return int(self.clock().timestamp())

    def _midnight(self):
        """The start of today, Beijing time, as a timestamp: daily limits count from here."""
        return int(self.clock().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())

    async def _reply(self, message, text, buttons=None):
        try:
            await self.gw.send(message.group_openid, text, reply_to=message.id, buttons=buttons)
        except Exception as e:
            _log.error(f"[AIChat] 回复失败: {e}")

    async def _reply_list(self, message, lines, buttons=None):
        """A list that may be long: as many messages as it takes, the buttons under the last."""
        chunks = [""]
        for line in lines:
            if chunks[-1] and len(chunks[-1]) + len(line) + 1 > MAX_MESSAGE_LENGTH:
                chunks.append(line)
            else:
                chunks[-1] = f"{chunks[-1]}\n{line}" if chunks[-1] else line
        for i, chunk in enumerate(chunks):
            await self._reply(message, chunk, buttons=buttons if i == len(chunks) - 1 else None)

    def _tried(self, what, group, member):
        """How often today a member has done something they can only do so often a day. Kept in
        memory: a restart gives everyone a fresh day, which costs little."""
        day, count = self.tries.get((what, group, member), (None, 0))
        return count if day == self.clock().date().isoformat() else 0

    def _try(self, what, group, member):
        self.tries[(what, group, member)] = (self.clock().date().isoformat(), self._tried(what, group, member) + 1)

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
            try:
                await self.commands[word](message, text[len(word):].strip())
            except Exception as e:
                _log.error(f"[AIChat] 指令 {word} 出错: {e}")
                await self._reply(message, t("common.error", command=word, error=e))
            return True
        await self.chat(message, text)
        return True

    def _special_people(self, group, member, member_name, window):
        """Who the special people known in this group are, under the nicknames they use now,
        and whether this turn's speaker is one of them. None if none of them is known here."""
        if not self.special:
            return None
        nicknames = {turn["member_openid"]: turn["name"] for turn in window     # the newest name wins
                     if turn["role"] == "user" and turn["member_openid"] in self.special}
        lines, known = [], set()
        for openid, note in self.special.items():
            nickname = member_name if openid == member else nicknames.get(openid) or self.nickname_of(group, openid)
            if nickname:
                lines.append(render(self.prompts["special_line"], member=nickname, note=note))
                known.add(nickname)
        if not lines:
            return None
        block = [render(self.prompts["special_people"], people="\n".join(lines))]
        if member in self.special:
            block.append(render(self.prompts["special_speaker"], member=member_name, note=self.special[member]))
        elif member_name in known:
            block.append(render(self.prompts["special_namesake"], member=member_name))
        return "\n".join(block)

    def _today(self, group, window, alone=False):
        """The first part of the evidence: the time, her day, today's game, birthdays and poll, and
        the actions the bot's last replies used. Nicknames come from members, so this is data.
        `alone` leaves out what's going on in the group, which is full of nicknames."""
        now = self.clock()
        lines = [render(self.prompts["now"], date=t("common.date", month=now.month, day=now.day),
                        weekday=WEEKDAYS[now.weekday()], time=now.strftime("%H:%M"))]
        day = self._her_day()
        if day:
            lines.append(render(self.prompts["her_day"], events="\n".join(f"- {event}" for event in day)))
        if self.game_state and self.teams_today and not alone:
            teams = [render(self.prompts["team_line"], captain=captain,
                            members=join([f"{name}（{join(statuses)}）" if statuses else name
                                          for name, statuses in members]))
                     for captain, members in self.teams_today(group)]
            if teams:
                lines.append(render(self.prompts["today_teams"], teams="\n".join(teams)))
        if self.game_state and self.social_today and not alone:
            today = self.social_today(group)
            if today["birthdays"]:
                lines.append(render(self.prompts["today_birthdays"], members=join(today["birthdays"])))
            if today["poll"]:
                question, options = today["poll"]
                lines.append(render(self.prompts["today_poll"], question=question,
                                    options=join([f"{label}. {option}：{votes}" for label, option, votes in options])))
            if today.get("opinions"):
                topic, count = today["opinions"]
                lines.append(render(self.prompts["today_opinions"], topic=topic, count=count))
            if today.get("soup"):
                lines.append(render(self.prompts["today_soup"], surface=today["soup"]))
        actions = context.recent_actions(window)
        if actions:
            lines.append(render(self.prompts["recent_actions"], actions="".join(f"「{a}」" for a in actions)))
        return "\n".join(lines)

    def hear(self, message):
        """A group message that doesn't @ the bot. With ai_chat.hear_all it joins the group's
        conversation, marked as not addressed to her, so her context has the whole chat."""
        if not (self.enabled and self.hear_all) or message.is_bot or not message.member_openid:
            return
        text = re.sub(r"<[^>]{0,200}>", "", message.content or "").strip()   # no mention or face tags
        if not text or text.startswith("/"):
            return
        group = message.group_openid
        self.store.add_turn(group, message.member_openid, message.nickname or t("common.someone"), "user",
                            text[:HEARD_TEXT], self._now(), addressed=False)
        # A busy group shouldn't wait for the next @ before its old chat goes into the summary.
        self.heard_since[group] = self.heard_since.get(group, 0) + 1
        if self.heard_since[group] >= self.summarize_after:
            self.heard_since[group] = 0
            self._spawn(self.maybe_summarize(group))

    def noticed(self, message):
        """Any member's message in a group where she may start topics: it isn't quiet there. Only
        the time is kept, and written to chat.db at most once a minute, so a restart remembers it."""
        group = message.group_openid
        if not self.enabled or getattr(message, "is_bot", False) or group not in self.topic_groups:
            return
        ts = self.heard[group] = self._now()
        if ts - self.heard_saved.get(group, 0) >= HEARD_SAVE_SECONDS:
            self.heard_saved[group] = ts
            self.store.set_heard(group, ts)

    def _last_heard(self, group):
        """When a member last spoke in the group; since the bot started, if it doesn't know."""
        return self.heard.get(group) or self.store.heard_at(group) or self.started

    def _her_day(self):
        """Her day today in the current style, as planned (lines like "上午：…"), or None."""
        return self.store.day_plan(current().name, self.clock().date().isoformat()) if self.her_day else None

    def _can_speak(self, group):
        """Whether her next [语音:…] can be a voice message: a voice is set up, it hasn't failed in
        the last hour, and this group's voice.per_day isn't used up today."""
        return (self.speaker is not None and self._now() >= self.voice_off_until
                and self.store.posts_since(group, "voice", self._midnight()) < self.voice_per_day)

    def _samples(self, style):
        """The style's sample exchanges and the voice to avoid, as instruction sections."""
        examples = str(style.lookup("examples") or "").strip()
        bad = str(style.lookup("bad_examples") or "").strip()
        return [examples and render(self.prompts["examples"], examples=examples),
                bad and render(self.prompts["bad_examples"], examples=bad)]

    @staticmethod
    def _never_say(style):
        """The phrases a style never says (never_say): its own list, and the default's behind it."""
        phrases = []
        while style is not None:
            own = style.own("never_say")
            phrases += [str(phrase) for phrase in own] if isinstance(own, list) else []
            style = style.base
        return list(dict.fromkeys(phrases))

    def _impression(self, group, member):
        row = self.store.impression(group, member)
        return row["text"] if row else None

    def _prompt(self, group, member, member_name, window, length=None, found=None, sight=vision.NOTHING, redo=None):
        """(instructions, evidence) for one reply. `length` is the pre-reply call's 短/中/长, if it gave
        one, `found` what a web search for this message turned up, as look_up returns it, and `sight`
        what she has of its pictures. When real people are in them she knows only the sender: no rule
        stops her pinning them on someone she knows, but she can't name someone she isn't told about.
        `redo` is (her first try, the phrase in it she never says), when she's asked to say it again:
        the whole list goes along, or she swaps one of its phrases for another."""
        style = current()
        alone = sight.strangers
        hers = self._stickers_in(group)
        lore = None if alone else context.lore_for(style.lookup("lore"), window)
        # What members taught her is theirs, not the style's: it rides with the evidence, as data.
        taught = context.taught_for(self.store.taught(group), window) if self.teach and not alone else None
        # The parts that never change for a style come first, so DeepSeek's prefix cache can reuse them.
        instructions = [style.system_prompt, *self._samples(style),
                        render(self.prompts["group_rules"], member=member_name),
                        render(self.prompts["honesty"]),
                        render(self.prompts["reply_shape"]),
                        render(self.prompts["declining"]),
                        length and render(self.prompts[context.LENGTHS[length]]),
                        hers and render(self.prompts["stickers"], stickers=stickers.listing(hers),
                                        max=stickers.MAX_PER_REPLY),
                        self._can_speak(group) and render(self.prompts["voice"], max_chars=voice.MAX_CHARS),
                        not alone and self._special_people(group, member, member_name, window),
                        t("talking_to"),
                        lore and render(self.prompts["lore"], lore="\n".join(f"- {text}" for text in lore)),
                        taught and render(self.prompts["taught_rules"]),
                        self._her_day() and render(self.prompts["day_rules"]),
                        found and render(self.prompts["search_rules"]),
                        sight.pictures and render(self.prompts["vision_rules"]),
                        alone and render(self.prompts["vision_strangers"]),
                        sight.characters and render(self.prompts["vision_characters"]),
                        sight.missed and render(self.prompts["vision_missing"]),
                        redo and render(self.prompts["rewrite"], draft=redo[0], phrase=redo[1],
                                        phrases=join(f"「{phrase}」" for phrase in self._never_say(style)))]
        evidence = [self._today(group, window, alone)]
        if taught:
            evidence.append("\n".join([render(self.prompts["taught_title"]), *(f"- {line}" for line in taught)]))
        summary = None if alone else self.store.summary(group)
        if summary:
            evidence.append(f"{render(self.prompts['summary_prefix'])}\n{summary['summary']}")
        if self.memory_on:
            # The members speaking in the window, the current speaker first.
            speakers = {}
            for turn in reversed(window):
                if turn["role"] == "user" and turn["member_openid"] not in speakers:
                    speakers[turn["member_openid"]] = turn["name"]
            chosen = list(speakers.items())[:MEMORY_SPEAKERS]
            facts = memory.evidence(render(self.prompts["memory_title"]),
                                    [(name, [m["text"] for m in self._notes(group, openid)])
                                     for openid, name in chosen])
            views = memory.impressions(render(self.prompts["impression_title"]),
                                       [(name, self._impression(group, openid)) for openid, name in chosen])
            if facts or views:
                instructions.append(render(self.prompts["memory_rules"]))
                evidence += [facts, views]
        called = self.pet_name_for(group, member)
        if called:
            instructions.append(render(self.prompts["pet_name_rules"]))
            evidence.append(render(self.prompts["pet_name_title"], member=member_name, nickname=called))
        if found:
            evidence.append(render(self.prompts["web_results"], query=found[0], results=found[1]))
        return context.compose(instructions, evidence, render(self.prompts["evidence_policy"]),
                               render(self.prompts["evidence_title"]))

    async def perceive(self, name, said, window):
        """seekchat's 动态想象力: one cheap call before the reply judges the register and length this
        message needs, and whether it needs a web search. (register, length, (words, days) or None),
        or all None if it fails and the manual temperature rides."""
        last = next((turn["content"] for turn in reversed(window) if turn["role"] == "assistant"), "")
        prompt = render(self.prompts["register"], member=name, said=memory.excerpt(said, 300),
                        last=memory.excerpt(last, 300) if last else "（还没有）",
                        search=f"\n{render(self.prompts['register_search'])}" if self.searcher else "")
        try:
            raw = await self._complete(self.client, self.helper_model, [{"role": "user", "content": prompt}],
                                       temperature=0)
        except Exception as e:
            _log.warning(f"[AIChat] 回复前的判断失败，这次用 deepseek.temperature: {e}")
            return None, None, None
        return (*context.parse_register(raw), context.parse_search(raw) if self.searcher else None)

    async def look_up(self, group, words, days):
        """(words, the pages found) for a web search this message needs. None when the group has used
        today's searches, one failed a moment ago or nothing useful came back: she then doesn't know."""
        now = self._now()
        if (now < self.search_off_until
                or self.store.posts_since(group, "search", self._midnight()) >= self.search_per_day):
            return None
        try:
            results = await self.searcher.search(search.query(words, days, self.clock().date()))
        except Exception as e:
            self.search_off_until = now + search.RETRY_SECONDS
            _log.warning(f"[Search] 没搜成，{search.RETRY_SECONDS // 60} 分钟内先不搜: {e}")
            return None
        self.store.add_post(group, "search", now)
        pages = search.lines(results)
        _log.info(f"[Search] 搜了一次，用上 {len(pages)} 个网页")      # not the words: they aren't kept
        return (words, "\n".join(pages)) if pages else None

    def _may_look(self, group):
        """Whether she can look at a picture now: this group is listed, it hasn't used today's
        pictures, and looking didn't fail a moment ago."""
        return (group in self.vision_groups and self._now() >= self.vision_off_until
                and self.store.posts_since(group, "picture", self._midnight()) < self.vision_per_day)

    async def look(self, group, images):
        """What she can have of a message's pictures, as a Sight. They're held in memory for this
        reply only. If she can't get any she's told so, so that she doesn't play along."""
        if not self._may_look(group):
            return vision.Sight(missed=True)
        pictures = []
        for attachment, _ in images[:vision.MAX_PICTURES]:
            try:
                pictures.append(vision.data_url(*await self.fetch_picture(attachment.get("url"))))
            except vision.PictureError as e:
                _log.warning(f"[Vision] 有张图没拿到: {e.reason} {e.detail}")
        if not pictures:
            return vision.Sight(missed=True)
        for _ in pictures:
            self.store.add_post(group, "picture", self._now())
        strangers = not await self.no_people_in(pictures)
        characters = [] if strangers else await self.characters_in(pictures)
        _log.info(f"[Vision] 看了 {len(pictures)} 张图，{'有真人' if strangers else '没有真人'}"
                  f"{f'，对上了{join(characters)}' if characters else ''}")
        return vision.Sight(pictures, characters=characters, strangers=strangers)

    async def no_people_in(self, pictures):
        """True only if a call that gets nothing but the pictures and a fixed question, so nothing
        about the group, says no real people are in them. Unsure means she keeps to herself."""
        try:
            raw = await self._complete(self.client, self.vision_model, [
                {"role": "user", "content": context.with_pictures(render(self.prompts["person_check"]), pictures)}],
                temperature=0)
        except Exception as e:
            _log.warning(f"[Vision] 没判断出图里有没有真人: {str(e)[:200]}")
            return False
        return context.no_people(raw)

    async def characters_in(self, pictures):
        """Which gallery characters are in these pictures, from a call that gets only the reference
        pictures and these. If it fails or takes too long she goes without a name."""
        if not self.references:
            return []
        references = [(render(self.prompts["character_reference_note" if note else "character_reference"],
                              character=name, note=note), url)
                      for name, url, note in self.references]
        message = context.compare_pictures(references, render(self.prompts["character_check"]), pictures)
        try:
            raw = await asyncio.wait_for(self._complete(self.client, self.vision_model,
                                                        [{"role": "user", "content": message}], temperature=0),
                                         gallery.CHECK_SECONDS)
        except Exception as e:
            _log.warning(f"[Vision] 没比对出图里的角色: {type(e).__name__} {str(e)[:200]}")
            return []
        return context.parse_characters(raw, {name for name, _, _ in self.references})

    async def _sight(self, group, images):
        """What she has of this message's pictures. Just after real people were in one nothing
        changes for her, or 「这是小红吧」 would meet her memories of 小红 and she'd agree."""
        if not images:
            careful = self._now() < self.careful_until.get(group, 0)
            return vision.Sight(strangers=True) if careful else vision.NOTHING
        sight = await self.look(group, images)
        if sight.strangers:
            self.careful_until[group] = self._now() + vision.CAREFUL_SECONDS
        return sight

    async def _answer(self, group, member, name, window, length, found, sight, register, redo=None):
        """Her reply. If DeepSeek can't take the pictures she answers without them, and says so.
        Who the character check found goes right after the pictures: among the reference notes
        she kept to an earlier 「认不出来」 in the chat, and named the character 3 times in 10."""
        instructions, evidence = self._prompt(group, member, name, window, length, found, sight, redo)
        hint = render(self.prompts["picture_characters"], names=join(sight.characters)) if sight.characters else ""
        messages = context.build_messages(instructions, evidence, window, sight.pictures, hint)
        temperature = self.register_temperatures.get(register, self.temperature)
        if not sight.pictures:
            return await self._complete(self.client, self.model, messages, thinking=self.thinking,
                                        temperature=temperature)
        try:        # thinking stays off: in tests it saw no more, three times slower
            return await self._complete(self.client, self.vision_model, messages, temperature=temperature)
        except Exception as e:
            self.vision_off_until = self._now() + vision.RETRY_SECONDS
            _log.warning(f"[Vision] 看图失败，{vision.RETRY_SECONDS // 60} 分钟内先不看: {str(e)[:200]}")
        return await self._answer(group, member, name, window, length, found,
                                  vision.Sight(strangers=sight.strangers, missed=True), register, redo)

    async def _in_her_words(self, reply, *asked):
        """`reply`, or her second try at it when it has a phrase the style never says (never_say): the
        model's own stock lines, such as 「这话我不接」, which deepseek-flash fell back on when declining
        6 times in 240 where deepseek-v4-pro never did. One more call; if it fails, the first stands."""
        phrase = context.stock_phrase(reply, self._never_say(current()))
        if not phrase:
            return reply
        try:
            again = (await self._answer(*asked, redo=(reply, phrase))).strip()
        except Exception as e:
            _log.warning(f"[AIChat] 换个说法没成，用原来那条: {str(e)[:120]}")
            return reply
        _log.info(f"[AIChat] 回复里有「{phrase}」这类说法，让她重说了一次")
        return again or reply

    async def chat(self, message, text):
        group, member = message.group_openid, message.member_openid
        name = message.nickname or t("common.someone")
        images = message.images
        mark = render(self.prompts["picture_mark"]) if images else ""
        said = " ".join(filter(None, [mark, text])) or render(self.prompts["empty_mention"], member=name)
        self.store.add_turn(group, member, name, "user", said, self._now())
        window = context.select_window(self.store.recent_turns(group), self.context_tokens)
        if self.her_day and self._her_day() is None:
            self._spawn(self.plan_day())        # ready for her next reply; this one goes without
        sight = await self._sight(group, images)
        if sight.strangers:
            window = context.with_sender(window, member, render(self.prompts["someone"]))
        register, length, wanted = (await self.perceive(name, said, window) if self.dynamic or self.searcher
                                    else (None, None, None))
        if not self.dynamic:
            register = length = None
        # With a picture she can't know what to search for until she has looked at it.
        found = await self.look_up(group, *wanted) if wanted and not images else None
        asked = (group, member, name, window, length, found, sight, register)
        try:
            reply = (await self._answer(*asked)).strip()
        except Exception as e:
            await self._reply(message, t("ai.error", error=e))
            return
        if not reply:
            return
        reply = await self._in_her_words(reply, *asked)
        reply = await self._send_reply(message, reply)
        self.store.add_turn(group, None, current().name, "assistant", reply, self._now())
        # What's in a picture never becomes a memory, and a mistake about who's in it can't stick.
        if self.memory_on and member and not sight.strangers:
            self._spawn(self.observe(group, member, name, said, mark or reply))
        self._spawn(self.maybe_summarize(group))

    def _stickers_in(self, group):
        """{label: Sticker} of what she can send in a group: her own, then the ones its members sent
        in and an admin accepted (/投稿表情). Her own keep their labels."""
        if not self.member_stickers:
            return self.stickers
        hers = dict(self.stickers)
        for row in self.store.member_stickers(group):
            path = os.path.join(self.member_stickers_dir, row["file"])
            if row["label"] not in hers and os.path.isfile(path):
                hers[row["label"]] = stickers.Sticker(row["label"], row["note"], path)
        return hers

    def _notes(self, group, member):
        """A member's memories as they're numbered everywhere: long-term facts, then recent notes."""
        return list(self.store.memories(group, member)) + list(self.store.episodes(group, member))

    async def _send_reply(self, message, reply):
        """Send a reply: its text and sticker (_send_text), then a [语音:…] in it as a voice message
        under them. When she can't speak now, her words stay in the text. Returns the reply as it went
        out, for the transcript."""
        text, spoken = voice.extract(reply)
        if not spoken or not self._can_speak(message.group_openid):
            return await self._send_text(message, voice.as_text(reply))
        sent = await self._send_text(message, text)
        if await self._speak(message, spoken[0]):
            return "\n".join(part for part in (sent, f"[语音:{spoken[0]}]") if part)
        await self._reply(message, spoken[0])       # the voice didn't go: say it in words
        return "\n".join(part for part in (sent, spoken[0]) if part)

    async def _speak(self, message, words):
        """Her words as a voice message under the reply; False when it didn't go out. After a failure
        her voice lines go out as text for an hour."""
        line = voice.speakable(words)
        if not line:
            return False
        try:
            audio = await self.speaker.speak(line)
            await self.gw.send_voice(message.group_openid, audio, reply_to=message.id)
        except Exception as e:
            self.voice_off_until = self._now() + VOICE_RETRY_SECONDS
            _log.warning(f"[Voice] 语音没发出去，{VOICE_RETRY_SECONDS // 3600} 小时内她的语音改成文字: {e}")
            return False
        self.store.add_post(message.group_openid, "voice", self._now())
        return True

    async def _send_text(self, message, reply):
        """Send a reply, with any [表情:标签] in it as that sticker: inside the reply when QQ takes
        that, otherwise right under the text. Returns the reply as it went out, for the transcript."""
        hers = self._stickers_in(message.group_openid)
        clean, labels = stickers.extract(reply, hers) if hers else (reply, [])
        if labels and self.stickers_inline and time.monotonic() >= self.inline_off_until:
            try:
                await self._send_inline(message, clean, [hers[label] for label in labels])
                return "\n".join([clean] + [f"[表情:{label}]" for label in labels]).strip()
            except Exception as e:
                self.inline_off_until = time.monotonic() + INLINE_RETRY_SECONDS
                _log.warning(f"[Stickers] 表情包没能放进回复里，{INLINE_RETRY_SECONDS // 3600} 小时内改成单独发图: {e}")
        if clean:
            await self._reply(message, clean)
        sent = []
        for label in labels:
            sticker = hers[label]
            hosted = self.stickers_url and self.stickers.get(label) is sticker     # only her own are kept there
            url = f"{self.stickers_url.rstrip('/')}/{quote(os.path.basename(sticker.path))}" if hosted else None
            try:
                await self.gw.send_image(message.group_openid, sticker.path, reply_to=message.id, url=url)
                sent.append(label)
            except Exception as e:
                _log.warning(f"[AIChat] 表情包「{label}」没发出去: {e}")
        if not clean and not sent:          # nothing went out: say it in words instead
            clean = stickers.as_text(reply)
            if clean:
                await self._reply(message, clean)
        return "\n".join([clean] + [f"[表情:{label}]" for label in sent]).strip()

    async def _send_inline(self, message, text, chosen):
        """The reply and its stickers as one markdown message, each sticker sticker_size px."""
        body = stickers.as_markdown(text)
        parts = [body] if body else []
        for sticker in chosen:
            link = await self.library.link(sticker)
            parts.append(stickers.markdown_image(link, *stickers.display_size(sticker.path, self.sticker_size)))
        await self.gw.send_markdown(message.group_openid, "\n\n".join(parts), reply_to=message.id)

    async def observe(self, group, member, name, said, reply):
        """After a reply, like attention after a conversation: what the speaker said about themselves
        that matters becomes a recent note, memories that came up again hold longer, outdated ones go,
        and the impression of them is updated. Failures only cost a missed note."""
        lock = self.observing.setdefault((group, member), asyncio.Lock())
        async with lock:
            try:
                generation = self.generation.get(group, 0)
                known = self._notes(group, member)
                texts = [m["text"] for m in known]
                seen = self._impression(group, member)
                prompt = render(self.prompts["observer"], member=name, said=memory.excerpt(said),
                                reply=memory.excerpt(reply), known=memory.numbered(texts, "（还没有）"),
                                impression=seen or "（还没有）", max_text=memory.MAX_MEMORY_TEXT,
                                max_adds=memory.MAX_ADDS_PER_TURN, max_impression=memory.MAX_IMPRESSION_TEXT)
                raw = await self._complete(self.client, self.helper_model, [{"role": "user", "content": prompt}])
                if self.generation.get(group, 0) != generation:
                    return
                # The impression is the bot's own view, running jokes included: no seriousness check.
                impression = memory.parse_impression(raw)
                if impression and impression != seen:
                    self.store.set_impression(group, member, impression, self._now())
                notes, reinforce, forget = memory.parse_observation(raw, texts)
                for n in forget:
                    self.store.delete_memory(known[n - 1]["id"])
                for n in reinforce:
                    row = known[n - 1]
                    if row["kind"] == "fact":
                        self.store.reinforce(row["id"], self._now())
                    else:
                        self.store.raise_importance(row["id"])
                if notes and self.memory_check:
                    serious = await self.check_notes(name, said, reply, [text for text, _ in notes])
                    if self.generation.get(group, 0) != generation:
                        return
                    notes = [(text, importance) for text, importance in notes if text in serious]
                for text, importance in notes:
                    self.store.add_memory(group, member, text, self._now(), kind="episode", importance=importance)
                # Like Generative Agents' reflection: once recent notes add up, consolidate now.
                if sum(row["importance"] for row in self.store.episodes(group, member)) >= memory.CONSOLIDATE_AT:
                    self._spawn(self.consolidate(group, member))
            except Exception as e:
                _log.warning(f"[AIChat] 整理记忆失败: {e}")

    def _nickname(self, group, member):
        return self.nickname_of(group, member) or self.store.last_name(group, member) or t("common.someone")

    async def consolidate(self, group, member):
        """Like a night's sleep: fold a member's recent notes and older memories into a few general
        long-term facts, then let faded ones go. Runs nightly, or early when recent notes add up.
        A failed call keeps the notes for next time."""
        lock = self.observing.setdefault((group, member), asyncio.Lock())
        async with lock:
            try:
                generation = self.generation.get(group, 0)
                facts, notes = self.store.memories(group, member), self.store.episodes(group, member)
                if notes:
                    day = lambda ts: datetime.fromtimestamp(ts, self.tz)
                    prompt = render(
                        self.prompts["consolidation"], member=self._nickname(group, member),
                        facts=memory.numbered([f"{f['text']}（重要度 {f['importance']}）" for f in facts], "（还没有）"),
                        notes="\n".join(f"- {n['text']}（重要度 {n['importance']}，"
                                        f"{t('common.date', month=day(n['created_at']).month, day=day(n['created_at']).day)}）"
                                        for n in notes),
                        max_text=memory.MAX_MEMORY_TEXT, max_facts=self.memories_per_member)
                    raw = await self._complete(self.client, self.helper_model, [{"role": "user", "content": prompt}])
                    if self.generation.get(group, 0) != generation:
                        return
                    adds, updates, forget = memory.parse_consolidation(raw, len(facts), self.memories_per_member)
                    # A general fact can matter a little more than its notes did, not a lot more.
                    ceiling = max(n["importance"] for n in notes) + 1
                    adds = [(text, min(importance, ceiling)) for text, importance in adds]
                    now = self._now()
                    for n in forget:
                        self.store.delete_memory(facts[n - 1]["id"])
                    for n, text in updates:     # merged with something new: the topic came up again
                        self.store.set_memory_text(facts[n - 1]["id"], text)
                        self.store.reinforce(facts[n - 1]["id"], now)
                    kept = {f["text"] for i, f in enumerate(facts, 1) if i not in forget}
                    for text, importance in adds:
                        if text not in kept:
                            self.store.add_memory(group, member, text, now, kind="fact", importance=importance)
                    for note in notes:          # folded in or let go, either way done with
                        self.store.delete_memory(note["id"])
                self.fade(group, member)
            except Exception as e:
                _log.warning(f"[AIChat] 整理长期记忆失败: {e}")

    def fade(self, group, member):
        """Forget the long-term facts that have faded, and the weakest past memories_per_member."""
        now = self._now()
        facts = sorted(self.store.memories(group, member), key=lambda row: memory.fact_strength(row, now), reverse=True)
        for i, row in enumerate(facts):
            if i >= self.memories_per_member or memory.fact_strength(row, now) < memory.FORGET_BELOW:
                self.store.delete_memory(row["id"])

    async def check_notes(self, name, said, reply, facts):
        """The candidate notes `name` meant seriously, judged by one more cheap call on the
        same exchange. Jokes, banter, role-play and game talk are dropped; an unreadable
        answer keeps nothing, and a failed call is logged by observe(), keeping nothing."""
        prompt = render(self.prompts["memory_check"], member=name, said=memory.excerpt(said),
                        reply=memory.excerpt(reply), notes=memory.numbered(facts, ""))
        raw = await self._complete(self.client, self.helper_model, [{"role": "user", "content": prompt}])
        keep = memory.parse_check(raw, len(facts))
        if len(keep) < len(facts):      # only the count: logs outlive the chat itself
            _log.info(f"[AIChat] 核对记忆：{len(facts) - len(keep)} 条不像认真说的，没有记下")
        return [fact for i, fact in enumerate(facts, 1) if i in keep]

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

    async def comment_on_digest(self, digest):
        """One or two in-character sentences to end the weekly digest, or None.

        Only the digest itself -- counts and nicknames -- goes to DeepSeek. A failure
        just means the digest goes out without a comment.
        """
        if not (self.enabled and self.digest_comment):
            return None
        try:
            text = await self._complete(self.client, self.helper_model, [
                {"role": "system", "content": current().system_prompt},
                {"role": "user", "content": render(self.prompts["digest_comment"], digest=digest)},
            ])
        except Exception as e:
            _log.warning(f"[AIChat] 小报点评失败: {e}")
            return None
        text = text.strip().strip("“”\"")
        return text[:DIGEST_COMMENT_LIMIT] or None

    async def summarize_opinions(self, topic, opinions):
        """A few in-character lines on what members said in a 征集意见 that just ended, or None.

        Only the topic and the opinions go to DeepSeek, not who wrote them. A failure just means
        the list goes out without her summary.
        """
        if not (self.enabled and self.opinion_summary) or len(opinions) < OPINION_SUMMARY_MIN:
            return None
        listed = "\n".join(f"{i}. {text}" for i, text in enumerate(opinions, 1))
        try:
            text = await self._complete(self.client, self.helper_model, [
                {"role": "system", "content": current().system_prompt},
                {"role": "user", "content": render(self.prompts["opinion_summary"], topic=topic, opinions=listed)},
            ])
        except Exception as e:
            _log.warning(f"[AIChat] 意见征集的总结没写成: {e}")
            return None
        text = stickers.extract(voice.as_text(text.strip()), {})[0]
        return text[:OPINION_SUMMARY_LIMIT] or None

    async def host_soup(self, surface, truth, points, said, guess=False):
        """The verdict of 海龟汤's host on a member's question, or on their guess, as DeepSeek wrote
        it: modules/fun/soup.py reads it, and nobody sees it. One cheap call with the puzzle
        and what they said, and nothing else: no persona, no chat. Raises when the call fails."""
        prompt = render(self.prompts["soup_guess" if guess else "soup_question"], surface=surface, truth=truth,
                        points="\n".join(f"- {point}" for point in points), said=said)
        return await self._complete(self.client, self.helper_model, [{"role": "user", "content": prompt}],
                                    temperature=0)

    async def read_fortune(self, member, level, good, bad, said):
        """Her comment on someone's 今日运势 in light of their own latest messages, or None.

        Only the fortune and those messages go to DeepSeek, with her persona. A failure just means
        the style's own comment.
        """
        if not (self.enabled and self.fortune) or not said:
            return None
        persona = "\n\n".join(filter(None, [current().system_prompt, t("talking_to")]))
        prompt = render(self.prompts["fortune_comment"], member=member, level=level, good=join(good), bad=join(bad),
                        said="\n".join(f"- {text}" for text in said))
        try:
            text = await self._complete(self.client, self.helper_model, [
                {"role": "system", "content": persona},
                {"role": "user", "content": prompt},
            ])
        except Exception as e:
            _log.warning(f"[AIChat] 今日运势的解读没写成: {e}")
            return None
        text = stickers.extract(voice.as_text(text.strip()), {})[0]
        return text[:FORTUNE_COMMENT_LIMIT] or None

    # ------------------------------------------------------------------ her day, and speaking up

    async def plan_day(self):
        """Her day in the current style, planned by one cheap call the first time it's needed each
        day; it rides along with every reply after that. None when it can't be planned: a failed
        plan is asked for again after half an hour."""
        if not (self.enabled and self.her_day):
            return None
        style, now = current(), self.clock()
        day = now.date().isoformat()
        planned = self.store.day_plan(style.name, day)
        if planned is not None:
            return planned
        failed = self.plan_failed.get(style.name)
        if style.name in self.planning or (failed is not None and self._now() - failed < DAY_RETRY_SECONDS):
            return None
        self.planning.add(style.name)
        try:
            raw = await self._complete(self.client, self.helper_model, [
                {"role": "system", "content": style.system_prompt},
                {"role": "user", "content": render(self.prompts["day"], weekday=WEEKDAYS[now.weekday()],
                                                   date=t("common.date", month=now.month, day=now.day))},
            ])
            events = context.parse_day(raw)
            if not events:
                raise ValueError("回答里没有读到日常")
        except Exception as e:
            self.plan_failed[style.name] = self._now()
            _log.warning(f"[AIChat] 今天的日常没排出来，{DAY_RETRY_SECONDS // 60} 分钟后再试: {e}")
            return None
        finally:
            self.planning.discard(style.name)
        self.store.set_day_plan(style.name, day, events, self._now())
        _log.info(f"[AIChat] {style.name}今天的日常排好了：{len(events)} 件事")
        return events

    async def maybe_topic(self, group):
        """In a group from ai_chat.topics: speak up once nobody has for topics.quiet_hours, between
        topics.hours, at most topics.per_day times a day, and never twice into silence: until someone
        speaks after her last topic, she waits. True when she spoke."""
        now, ts = self.clock(), self._now()
        start, end = self.topic_hours
        heard = self._last_heard(group)
        if not start <= now.hour < end or ts - heard < self.quiet_hours * 3600 or ts < self.topic_wait.get(group, 0):
            return False
        if self.store.posts_since(group, "topic", self._midnight()) >= self.topics_per_day:
            return False
        last = self.store.last_post(group, "topic")
        if last is not None and last >= heard:
            return False
        hours = int((ts - heard) // 3600)
        with STYLES.scope(group):
            try:
                text = await self.write_topic(group, hours)
                if not text:
                    raise ValueError("回答是空的")
                await self.gw.send(group, text)
            except Exception as e:
                self.topic_wait[group] = ts + 3600
                _log.warning(f"[AIChat] 话题没开成，一小时后再看: {e}")
                return False
            self.store.add_turn(group, None, current().name, "assistant", text, self._now())
        self.store.add_post(group, "topic", self._now())
        _log.info(f"[AIChat] 群里 {hours} 小时没人说话，她开了个话题")
        return True

    async def write_topic(self, group, hours):
        """What she says to restart a quiet group, in the current style: her persona and the topic
        rules, the same evidence as a reply (the time, her day, today's game, the summary), the recent
        chat, and a cue in place of a member's message. Plain text: any markers become words or go."""
        await self.plan_day()
        style = current()
        window = context.select_window(self.store.recent_turns(group), self.context_tokens)
        instructions = [style.system_prompt, *self._samples(style), render(self.prompts["topic"], hours=hours),
                        self._her_day() and render(self.prompts["day_rules"])]
        evidence = [self._today(group, window)]
        summary = self.store.summary(group)
        if summary:
            evidence.append(f"{render(self.prompts['summary_prefix'])}\n{summary['summary']}")
        instructions, evidence = context.compose(instructions, evidence, render(self.prompts["evidence_policy"]),
                                                 render(self.prompts["evidence_title"]))
        messages = context.build_messages(instructions, evidence, window)
        cue = render(self.prompts["topic_cue"], hours=hours)
        if messages[-1]["role"] == "user":
            messages[-1]["content"] += "\n" + cue
        else:
            messages.append({"role": "user", "content": cue})
        text = await self._complete(self.client, self.model, messages, thinking=self.thinking,
                                    temperature=self.temperature)
        return stickers.extract(voice.as_text(text.strip()), {})[0]

    async def tick(self):
        """Every few minutes, for each group from ai_chat.topics: her day planned ahead from the
        morning on, and a topic if the group has gone quiet."""
        for group in self.topic_groups:
            try:
                if self.clock().hour >= DAY_START_HOUR:
                    with STYLES.scope(group):
                        await self.plan_day()
                await self.maybe_topic(group)
            except Exception as e:
                _log.error(f"[AIChat] 开话题出错: {e}")

    # ------------------------------------------------------------------ commands

    async def cmd_my_memory(self, message, args):
        """/我的记忆: what the bot has noted about me in this group."""
        if not self.memory_on:
            await self._reply(message, t("ai.memory.disabled"))
            return
        group, member = message.group_openid, message.member_openid
        facts, recent = self.store.memories(group, member), self.store.episodes(group, member)
        seen, called = self._impression(group, member), self.pet_name_for(group, member)
        if not facts and not recent and not seen and not called:
            await self._reply(message, t("ai.memory.empty"))
            return
        lines = [t("ai.memory.title", member=message.nickname or t("common.someone"), count=len(facts) + len(recent))]
        lines += [t("ai.memory.row", n=i, text=row["text"]) for i, row in enumerate(facts, 1)]
        if recent:
            lines.append(t("ai.memory.recent"))
            lines += [t("ai.memory.row", n=i, text=row["text"]) for i, row in enumerate(recent, len(facts) + 1)]
        if seen:
            lines.append(t("ai.memory.impression", text=seen))
        if called:
            lines.append(t("ai.memory.pet_name", nickname=called))
        lines.append(t("ai.memory.footer"))
        buttons = [(t("ai.memory.forget_button"), "/忘记 ")] if facts or recent else []    # typed in, for the number
        if seen:
            buttons.append((t("ai.memory.forget_impression_button"), "/忘记 印象"))
        await self._reply(message, "\n".join(lines), buttons=[buttons])

    async def cmd_forget(self, message, args):
        """/忘记 序号…, /忘记 印象 or /忘记 全部: delete my own memories, or the bot's impression of me."""
        group, member = message.group_openid, message.member_openid
        if args in FORGET_ALL_WORDS:
            count = self.store.clear_memories(group, member)
            await self._reply(message, t("ai.memory.forgot_all", count=count))
            return
        if args in FORGET_IMPRESSION_WORDS:
            row = self.store.impression(group, member)
            if row:
                self.store.delete_memory(row["id"])
            await self._reply(message, t("ai.memory.forgot_impression" if row else "ai.memory.no_impression"))
            return
        numbers = [int(n) for n in re.findall(r"\d+", args)]
        if not numbers:
            await self._reply(message, t("ai.memory.usage"))
            return
        rows = self._notes(group, member)
        missing = [n for n in numbers if not 1 <= n <= len(rows)]
        if missing:
            await self._reply(message, t("ai.memory.no_such", n=join([str(n) for n in missing])))
            return
        for n in sorted(set(numbers)):
            self.store.delete_memory(rows[n - 1]["id"])
        await self._reply(message, t("ai.memory.forgot", n=join([str(n) for n in sorted(set(numbers))])))

    # ---- the nickname she gives someone

    def pet_name_for(self, group, member):
        """The nickname she gave a member (/外号), or None: for her replies to them, and their /我的状态."""
        row = self.store.pet_name(group, member) if self.enabled and self.pet_names and member else None
        return row["text"] if row else None

    async def cmd_pet_name(self, message, args):
        """/外号: she gives the member a nickname, from what she remembers of them and how they take
        part. /外号 换 asks for another and /外号 不要 for none. It shows on their /我的状态, and she
        uses it now and then."""
        group, member = message.group_openid, message.member_openid
        name = message.nickname or t("common.someone")
        if not self.pet_names:
            await self._reply(message, t("ai.pet_name.disabled"))
            return
        row = self.store.pet_name(group, member)
        if args in PET_NAME_DROP_WORDS:
            if row:
                self.store.delete_memory(row["id"])
            await self._reply(message, t("ai.pet_name.dropped", nickname=row["text"]) if row else t("ai.pet_name.none"))
            return
        buttons = [[(t("ai.pet_name.again_button"), "/外号 换"), (t("ai.pet_name.drop_button"), "/外号 不要")]]
        if row and args not in PET_NAME_AGAIN_WORDS:
            await self._reply(message, t("ai.pet_name.mine", nickname=row["text"]), buttons=buttons)
            return
        if self._tried("pet_name", group, member) >= PET_NAMES_PER_DAY:
            await self._reply(message, t("ai.pet_name.daily", max=PET_NAMES_PER_DAY))
            return
        known = [m["text"] for m in self._notes(group, member)] if self.memory_on else []
        seen = self._impression(group, member) if self.memory_on else None
        habits = self.habits_of(group, member) if self.habits_of else None
        if not known and not seen and not (habits and habits["badges"]):
            await self._reply(message, t("ai.pet_name.too_soon"))
            return
        self._try("pet_name", group, member)
        named = await self._name_someone(name, known, seen, habits, row["text"] if row else None)
        if named is None:
            await self._reply(message, t("ai.pet_name.failed"))
            return
        self.store.set_pet_name(group, member, named[0], self._now())
        await self._reply(message, t("ai.pet_name.given", member=name, nickname=named[0], why=named[1]),
                          buttons=buttons)

    async def _name_someone(self, member, known, seen, habits, before):
        """(nickname, her line on why) for a member, from one call with her persona and what she knows
        of them, which is all that goes to DeepSeek. None if it fails or the answer can't be used."""
        who = t("talking_to")
        persona = "\n\n".join(filter(None, [current().system_prompt, who]))
        if habits:
            habits = render(self.prompts["pet_name_habits"], valid=habits["valid"], night=habits["night"],
                            morning=habits["morning"], best=habits["best"],
                            badges=join(habits["badges"]) or "（还没有）")
        # Who they are is said twice: after her persona alone she named captains like everyone else 4 times in 12.
        prompt = render(self.prompts["pet_name"], member=member, known=memory.numbered(known, "（还没有）"),
                        impression=seen or "（还没有）", habits=habits or "（不清楚）",
                        before=render(self.prompts["pet_name_before"], before=before) if before else "",
                        who=render(self.prompts["pet_name_who"], who=who) if who else "")
        try:
            raw = await self._complete(self.client, self.helper_model, [
                {"role": "system", "content": persona},
                {"role": "user", "content": prompt},
            ])
        except Exception as e:
            _log.warning(f"[AIChat] 外号没起成: {str(e)[:120]}")
            return None
        named = context.parse_pet_name(raw)
        return named and (named[0], stickers.extract(voice.as_text(named[1]), {})[0])

    # ---- what members offer her, which an admin accepts: what they teach her, the stickers they send in

    def _offering(self, offered):
        """Whether members can offer her this at all."""
        return self.teach if offered is TAUGHT else self.member_stickers

    @staticmethod
    def _room(offered):
        """(how many of these a group can have, how many more can be waiting for an admin)."""
        return (TAUGHT_MAX, TAUGHT_WAITING) if offered is TAUGHT else (MEMBER_STICKERS_MAX, MEMBER_STICKERS_WAITING)

    @staticmethod
    def _offered_fields(row):
        """What a line about one of them fills in: its number, its own words, and who offered it."""
        fields = {key: row[key] for key in ("id", "keys", "text", "label", "note") if key in row.keys()}
        return dict(fields, member=row["nickname"] or t("common.someone"))

    @staticmethod
    def _decide_buttons(offered, rows):
        """A row under each one that waits for an admin: keep it (QQ asks first), or don't."""
        return [[(t(f"{offered.lines}.keep_button", id=row["id"]), f"{offered.keep} {row['id']}",
                  t(f"{offered.lines}.ask_keep", id=row["id"])),
                 (t(f"{offered.lines}.drop_button", id=row["id"]), f"{offered.drop} {row['id']}")] for row in rows[:5]]

    def _remove_offered(self, offered, row):
        self.store.delete_offering(offered.table, row["id"])
        if offered is STICKERS:
            self._drop_sticker_file(row["file"])

    def _drop_sticker_file(self, file):
        """A sticker a member sent in is gone: so is its picture. QQ's library lets go of its copy the
        next time it's full."""
        stickers.drop_file(self.member_stickers_dir, file)
        if self.library is not None:
            self.library.files.discard(file)

    async def _may_offer(self, message, offered, what, per_day):
        """Whether this member can offer her one more now: whether they're an admin, who offer as many
        as they like and needn't wait; or None once they've been told why not."""
        group, member = message.group_openid, message.member_openid
        admin = self.is_admin(group, member, message.role)
        most, waiting = self._room(offered)
        if not admin and self._tried(what, group, member) >= per_day:
            await self._reply(message, t(f"{offered.lines}.daily", max=per_day))
        elif len(self.store.offered(offered.table, group)) >= most:
            await self._reply(message, t(f"{offered.lines}.full", max=most))
        elif not admin and len(self.store.offered(offered.table, group, accepted=False)) >= waiting:
            await self._reply(message, t(f"{offered.lines}.queue_full", max=waiting))
        else:
            return admin
        return None

    async def _offer(self, message, offered, entry, admin):
        """Tell the group what became of what a member just offered: kept at once when an admin offered
        it, otherwise waiting, with the admins' buttons under it."""
        row = self.store.offering(offered.table, message.group_openid, entry)
        if admin:
            await self._reply(message, t(f"{offered.lines}.learned", **self._offered_fields(row)))
        else:
            await self._reply(message, t(f"{offered.lines}.waiting", **self._offered_fields(row)),
                              buttons=self._decide_buttons(offered, [row]))

    async def _take_back(self, message, offered, args):
        """<the offer command> 删除 编号: whoever offered it, or an admin, takes one back."""
        group = message.group_openid
        number = re.search(r"\d+", args)
        if not number:
            await self._reply(message, t(f"{offered.lines}.delete_usage"))
            return
        row = self.store.offering(offered.table, group, int(number.group(0)))
        if row is None:
            await self._reply(message, t(f"{offered.lines}.not_found", id=number.group(0)))
        elif row["member_openid"] != message.member_openid and not self.is_admin(group, message.member_openid,
                                                                                message.role):
            await self._reply(message, t(f"{offered.lines}.not_yours"))
        else:
            self._remove_offered(offered, row)
            await self._reply(message, t(f"{offered.lines}.deleted", **self._offered_fields(row)))

    async def cmd_offered(self, offered, message, args):
        """/她学会的 or /她的表情, with a page number if wanted: what this group's members have given
        her, the latest first, and what still waits for an admin."""
        group = message.group_openid
        if not self._offering(offered):
            await self._reply(message, t(f"{offered.lines}.disabled"))
            return
        kept = self.store.offered(offered.table, group)[::-1]
        waiting = self.store.offered(offered.table, group, accepted=False)
        more = [(t(f"{offered.lines}.offer_button"), f"{offered.offer} ")]      # typed in, for them to finish
        if not kept and not waiting:
            await self._reply(message, t(f"{offered.lines}.list_empty"), buttons=[more])
            return
        pages = max(1, -(-len(kept) // LIST_PAGE))
        number = re.search(r"\d+", args)
        page = min(pages, max(1, int(number.group(0)))) if number else 1
        lines = [t(f"{offered.lines}.list_title", count=len(kept))]
        lines += [t(f"{offered.lines}.list_row", **self._offered_fields(row))
                  for row in kept[(page - 1) * LIST_PAGE:page * LIST_PAGE]]
        if page < pages:
            lines.append(t(f"{offered.lines}.list_more", page=page + 1))
        if waiting and page == 1:
            lines.append(t(f"{offered.lines}.list_waiting", count=len(waiting)))
            lines += [t(f"{offered.lines}.list_row", **self._offered_fields(row)) for row in waiting]
        admin = self.is_admin(group, message.member_openid, message.role)
        await self._reply_list(message, lines,
                               buttons=self._decide_buttons(offered, waiting) if admin and waiting else [more])

    async def _decided(self, offered, message, args):
        """The one an admin's keep or drop command is about, or None once they've been told why not.
        With no number it's the one that's waiting, when there is just one."""
        group = message.group_openid
        if not self._offering(offered) or not self.is_admin(group, message.member_openid, message.role):
            await self._reply(message, t("common.not_admin" if self._offering(offered) else f"{offered.lines}.disabled"))
            return None
        number = re.search(r"\d+", args)
        waiting = self.store.offered(offered.table, group, accepted=False)
        if not number and len(waiting) != 1:
            await self._reply(message, t(f"{offered.lines}.decide_usage"))
            return None
        row = self.store.offering(offered.table, group, int(number.group(0))) if number else waiting[0]
        if row is None:
            await self._reply(message, t(f"{offered.lines}.not_found", id=number.group(0)))
        return row

    async def cmd_keep(self, offered, message, args):
        """/记下 编号 or /收下 编号 (admins): she keeps what a member offered."""
        row = await self._decided(offered, message, args)
        if row is None:
            return
        most = self._room(offered)[0]
        if not row["accepted"] and len(self.store.offered(offered.table, message.group_openid)) >= most:
            await self._reply(message, t(f"{offered.lines}.full", max=most))
            return
        self.store.accept_offering(offered.table, row["id"])
        await self._reply(message, t(f"{offered.lines}.kept", **self._offered_fields(row)))

    async def cmd_drop(self, offered, message, args):
        """/不记 编号 or /不收 编号 (admins): she doesn't keep it, or lets go of one she had kept."""
        row = await self._decided(offered, message, args)
        if row is not None:
            self._remove_offered(offered, row)
            await self._reply(message, t(f"{offered.lines}.dropped", **self._offered_fields(row)))

    async def cmd_teach(self, message, args):
        """/教她 词：意思: what a word means in this group, for her to know when it comes up. It waits for
        an admin's /记下 unless an admin wrote it. /教她 删除 编号 takes back one's own."""
        group, member = message.group_openid, message.member_openid
        if not self.teach:
            await self._reply(message, t("ai.teach.disabled"))
            return
        words = args.split()
        if words and words[0] in DELETE_WORDS:
            await self._take_back(message, TAUGHT, " ".join(words[1:]))
            return
        known, waiting = self.store.taught(group), self.store.taught(group, accepted=False)
        limits = dict(max_keys=context.TEACH_KEYS, min_key=context.TEACH_KEY[0], max_key=context.TEACH_KEY[1],
                      min_text=context.TEACH_TEXT[0], max_text=context.TEACH_TEXT[1])
        if not args:
            await self._reply(message, t("ai.teach.usage", count=len(known), **limits),
                              buttons=[[(t("ai.teach.list_button"), TAUGHT.listing)]])
            return
        try:
            keys, meaning = context.parse_teaching(args)
        except ValueError as e:
            await self._reply(message, t(f"ai.teach.bad_{e}", **limits))
            return
        if memory.looks_sensitive(args):
            await self._reply(message, t("ai.teach.refused.personal"))
            return
        if any(set(row["keys"].split("、")) == set(keys) and row["text"] == meaning for row in known + waiting):
            await self._reply(message, t("ai.teach.known"))
            return
        admin = await self._may_offer(message, TAUGHT, "teach", TEACH_PER_DAY)
        if admin is None:
            return
        self._try("teach", group, member)
        why = await self._teach_refusal(keys, meaning)
        if why:
            await self._reply(message, t(f"ai.teach.refused.{why}"))
            return
        entry = self.store.add_taught(group, member, message.nickname, keys, meaning, self._now(), accepted=admin)
        _log.info(f"[AIChat] 有人教了她一条群里的说法（#{entry}），{'管理员教的，已经记下' if admin else '等管理员点头'}")
        await self._offer(message, TAUGHT, entry, admin)

    async def _teach_refusal(self, keys, meaning):
        """Why something a member wants to teach her can't be kept, or None. One cheap call that sees
        only those words. If it fails, the admin who accepts it is the check."""
        prompt = render(self.prompts["teach_check"], keys="、".join(keys), text=meaning)
        try:
            raw = await self._complete(self.client, self.helper_model, [{"role": "user", "content": prompt}],
                                       temperature=0)
        except Exception as e:
            _log.warning(f"[AIChat] 群友教的这一条没查成，留给管理员看: {str(e)[:120]}")
            return None
        return context.teach_refusal(raw)

    async def cmd_submit(self, message, args):
        """/投稿表情 [标签], sent with a picture or while quoting one: a sticker for her to use in this
        group. It's looked at twice, for real people and for what it's good for, and then waits for an
        admin's /收下 unless an admin sent it. /投稿表情 删除 编号 takes back one's own."""
        group, member = message.group_openid, message.member_openid
        if not self.member_stickers:
            await self._reply(message, t("ai.submit.disabled"))
            return
        words = args.split()
        if words and words[0] in DELETE_WORDS:
            await self._take_back(message, STICKERS, " ".join(words[1:]))
            return
        megabytes = stickers.SUBMIT_BYTES // (1024 * 1024)
        if not message.images:
            await self._reply(message, t("ai.submit.usage", mb=megabytes),
                              buttons=[[(t("ai.submit.list_button"), STICKERS.listing)]])
            return
        wanted = stickers.clean_label(args) if args else None
        if args and wanted is None:
            await self._reply(message, t("ai.submit.bad_label", max=stickers.SUBMIT_LABEL))
            return
        admin = await self._may_offer(message, STICKERS, "sticker", SUBMITS_PER_DAY)
        if admin is None:
            return
        if self.store.posts_since(group, "sticker", self._midnight()) >= GROUP_SUBMITS_PER_DAY:
            await self._reply(message, t("ai.submit.group_daily"))
            return
        try:
            data, kind = await self.fetch_picture(message.images[0][0].get("url"))
        except vision.PictureError as e:
            await self._reply(message, t("ai.submit.fetch_failed", why=t(f"ai.picture_test.why.{e.reason}", detail=e.detail)))
            return
        digest = hashlib.sha1(data).hexdigest()
        taken = self.store.member_stickers(group) + self.store.member_stickers(group, accepted=False)
        if kind not in stickers.SUBMIT_FORMATS:
            await self._reply(message, t("ai.submit.bad_format"))
        elif len(data) > stickers.SUBMIT_BYTES:
            await self._reply(message, t("ai.submit.too_big", mb=megabytes))
        elif any(row["digest"] == digest for row in taken):
            await self._reply(message, t("ai.submit.known"))
        else:
            self._try("sticker", group, member)
            self.store.add_post(group, "sticker", self._now())
            drafted = await self._look_at_sticker(vision.data_url(data, kind))
            if not isinstance(drafted, tuple):
                await self._reply(message, t(f"ai.submit.refused.{drafted}" if drafted else "ai.submit.failed"))
                return
            label = stickers.unique_label(wanted or drafted[0], {*self.stickers, *(row["label"] for row in taken)})
            entry = self.store.add_member_sticker(group, member, message.nickname, label, drafted[1], digest,
                                                  self._now(), accepted=admin)
            file = stickers.keep_file(self.member_stickers_dir, entry, data, kind)
            self.store.set_member_sticker_file(entry, file)
            if self.library is not None:
                self.library.files.add(file)
            _log.info(f"[Stickers] 有人投稿了一张表情（#{entry}），{'管理员投的，已经收下' if admin else '等管理员点头'}")
            await self._offer(message, STICKERS, entry, admin)

    async def _look_at_sticker(self, picture):
        """What two calls that get only the picture say of a sticker a member sent in: (label, when
        to use it); or why it can't be used, "people" among the reasons; or None when it couldn't be
        looked at. Real people, or not being sure there are none, turns it down."""
        if not await self.no_people_in([picture]):
            return "people"
        try:
            raw = await self._complete(self.client, self.vision_model, [
                {"role": "user", "content": context.with_pictures(render(self.prompts["sticker_draft"]), [picture])}],
                temperature=0)
        except Exception as e:
            _log.warning(f"[Stickers] 投稿的表情没看成: {str(e)[:200]}")
            return None
        return stickers.parse_draft(raw)

    async def cmd_reset(self, message, args):
        """/重置对话 (admins): start the group's conversation over. Members' memories stay."""
        if not self.is_admin(message.group_openid, message.member_openid, message.role):
            await self._reply(message, t("common.not_admin"))
            return
        self.store.reset_chat(message.group_openid)
        self.heard_since.pop(message.group_openid, None)
        self._deleted(message.group_openid)
        await self._reply(message, t("ai.reset"))

    async def cmd_picture_test(self, message, args):
        """/看图测试 (admins): what QQ really sends for a picture, and whether it can be fetched. The
        picture is held in memory for a moment: it isn't kept, and it doesn't go to DeepSeek."""
        if not self.is_admin(message.group_openid, message.member_openid, message.role):
            await self._reply(message, t("common.not_admin"))
            return
        images = message.images
        if images:
            lines = [t("ai.picture_test.title", count=len(images))]
            for n, (attachment, quoted) in enumerate(images[:vision.MAX_PICTURES], 1):
                lines.append(await self._picture_row(n, attachment, quoted))
        elif message.message_type == vision.QUOTE:
            lines = [t("ai.picture_test.quote_empty")]
        else:
            await self._reply(message, t("ai.picture_test.usage"))
            return
        lines.append(t("ai.picture_test.shape", shape=vision.shape(message.raw, hosts=False)))
        await self._reply(message, "\n".join(lines))

    async def _picture_row(self, n, attachment, quoted):
        """A line of /看图测试: the picture as QQ describes it, and what fetching it did."""
        started = time.monotonic()
        try:
            data, kind = await self.fetch_picture(attachment.get("url"))
            result = t("ai.picture_test.ok", format=kind, kb=len(data) // 1024,
                       seconds=f"{time.monotonic() - started:.1f}")
        except vision.PictureError as e:
            result = t("ai.picture_test.failed", why=t(f"ai.picture_test.why.{e.reason}", detail=e.detail))
        return t("ai.picture_test.row", n=n, where=t(f"ai.picture_test.{'quoted' if quoted else 'own'}"),
                 kind=attachment.get("content_type"), width=attachment.get("width") or "?",
                 height=attachment.get("height") or "?", result=result)

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
            for row in self.store.offerings_by(STICKERS.table, group, member):
                self._drop_sticker_file(row["file"])
            self.store.forget_member(group, member)
            self._deleted(group)

    def forget_memories(self, group, member):
        """Someone's memories and impression, when they've been gone long enough. Unlike a deletion on
        request this keeps the group's summary, which the group still needs and has long moved on, and
        what they taught her and the stickers they sent in, which stay the group's without their name."""
        if self.enabled:
            self.store.clear_memories(group, member)
            for offered in (TAUGHT, STICKERS):
                for row in self.store.offerings_by(offered.table, group, member):
                    if not row["accepted"]:
                        self._remove_offered(offered, row)
                self.store.disown_offerings(offered.table, group, member)

    def forget_group(self, group):
        if self.enabled:
            for accepted in (True, False):
                for row in self.store.member_stickers(group, accepted):
                    self._drop_sticker_file(row["file"])
            self.store.forget_group(group)
            for waiting in (self.heard_since, self.heard, self.heard_saved, self.topic_wait):
                waiting.pop(group, None)
            self.tries = {key: tries for key, tries in self.tries.items() if key[1] != group}
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
        for offered in (TAUGHT, STICKERS):      # nobody accepted these: they go
            for row in self.store.stale_offerings(offered.table, self._now() - OFFER_WAIT_DAYS * DAY):
                self._remove_offered(offered, row)
        if not self.memory_on:
            return
        # The night's consolidation, like sleep; notes left over from a missed night go too.
        now = self.clock()
        night = now.hour == CONSOLIDATE_HOUR and self.consolidated_on != now.date()
        if night:
            self.consolidated_on = now.date()
        for group, member in self.store.members_with("episode"):
            oldest = min(row["created_at"] for row in self.store.episodes(group, member))
            if night or self._now() - oldest >= NOTES_LEFT_DAYS * DAY:
                await self.consolidate(group, member)
        for group, member in self.store.members_with("fact"):
            self.fade(group, member)

    async def run_scheduler(self):
        if not self.enabled:
            return
        _log.info(f"[AIChat] AI 聊天：模型 {self.model}，上下文约 {self.context_tokens} token，"
                  f"长期记忆{'开启' if self.memory_on else '关闭'}，聊天记录保留 {self.keep_days} 天，"
                  f"回复温度{'每次自动判断' if self.dynamic else f'固定 {self.temperature}'}，"
                  f"{'群里所有的聊天都进上下文' if self.hear_all else '只看 @ 的消息'}，"
                  f"{f'联网搜索每群每天最多 {self.search_per_day} 次' if self.searcher else '不联网搜索'}")
        if self.special:
            # The start of each ID, to check against what /我的ID says.
            _log.info(f"[AIChat] 特别的群友 {len(self.special)} 位，ID 开头：{'、'.join(o[:8] for o in self.special)}")
        if self.vision_groups:
            _log.info(f"[AIChat] {len(self.vision_groups)} 个群里她看得到 @ 她的图，模型 {self.vision_model}，"
                      f"{len(self.references)} 个角色有参考图")
        if self.topic_groups:
            _log.info(f"[AIChat] {len(self.topic_groups)} 个群安静 {self.quiet_hours:g} 小时后她会开话题："
                      f"{self.topic_hours[0]}–{self.topic_hours[1]} 点之间，每天最多 {self.topics_per_day} 次")
        if self.speaker is not None:
            _log.info(f"[Voice] 语音已打开：每个群每天最多 {self.voice_per_day} 条")
        housekept = None
        while True:
            if housekept is None or time.monotonic() - housekept >= 3600:
                housekept = time.monotonic()
                try:
                    await self.housekeeping()
                except Exception as e:
                    _log.error(f"[AIChat] 清理聊天记录出错: {e}")
            await self.tick()
            await asyncio.sleep(TICK_SECONDS)
