"""Social commands: 今日运势, birthdays, polls, 征集意见, 翻牌子 and 海龟汤.

- /今日运势: a fortune per member per day, worded entirely by the style file. With AI chat's
  fortune reading on, her comment on it takes in what the member has said lately.
- /生日 10-25: members opt in; on the day, the bot's reply to their first
  message wishes them happy birthday (QQ rations messages a bot starts on its
  own, so it waits for them to speak, as the newcomer greeting does).
- /投票 问题 选项…, /投 A, /结束投票: one open poll per group; anyone may start
  one; results list who voted for what.
- /征集意见 主题, /意见 内容, /结束征集: an admin asks the group for opinions, one
  call open per group; members add a few short ones each. Ending it lists them
  all, then her summary when AI chat is on.
- /翻牌子: a joke lucky draw among this week's active members, weighted by how
  much they posted; once a day each, admins as often as they like. @ a few
  people to draw among them instead.
  config.yaml's social.flip.special gives particular members their own chances.
- /海龟汤, /问 是非题, /猜 汤底: a lateral-thinking puzzle she hosts, one open per
  group. The puzzles and the reading of her verdicts are in modules/fun/soup.py;
  the verdicts themselves come from AI chat.

Every line comes from the style files (social.*) via t().
"""
import asyncio
import math
import random
import re
from collections import deque
from datetime import datetime, timedelta
import pytz
from botpy import logging
from core.config import root_path
from core.style import current, join, render, t
from modules.fun import soup
from modules.fun.social import fortune
from modules.fun.social.store import FunStore
from modules.manager.classifier import clean_text


_log = logging.get_logger()

DAY = 86400
WEEK = 7 * DAY
POLL_DAYS = 7               # an open poll closes by itself after this long
POLL_KEEP_DAYS = 30         # closed polls (and their votes) are deleted after this long
MAX_OPTIONS = 9
LETTERS = "ABCDEFGHI"
OPINION_DAYS = 30           # a 征集意见 nobody ends closes by itself after this long, without a list
OPINION_KEEP_DAYS = 30      # ended ones (and their opinions) are deleted after this long
OPINIONS_PER_MEMBER = 3
OPINION_CHARS = 200
OPINIONS_MAX = 60           # per 征集意见, so the list at the end stays readable
MAX_MESSAGE_LENGTH = 900    # longer replies go out as several messages
RECENT_KEPT = 20            # each member's latest messages held in memory, only while fortune reading is on,
RECENT_SECONDS = DAY        # for a day, which is all that's read; what's older goes within the hour
SWEEP_SECONDS = 3600
RECENT_CHARS = 200          # characters kept of each
READING_SECONDS = 12        # how long /今日运势 waits for her reading before using the style's comment
SOUP_HOURS = 24             # a 海龟汤 nobody finishes closes by itself after this long
SOUP_KEEP_DAYS = 7          # a finished one is deleted, with its questions and guesses, after this long
SOUP_QUESTION = 60          # characters in a question...
SOUP_GUESS = 150            # ...and in a guess
SOUP_TURNS = 150            # questions one soup takes; after that it's guess or give up
SOUP_PER_MINUTE = 6         # questions and guesses one member can send a minute: each is a call to DeepSeek
SOUP_HINT_EVERY = 3         # questions the group has to ask for each hint
SOUP_CLUES = 6              # answered questions /海龟汤 shows again
SOUP_SECONDS = 20           # how long she waits for a verdict
SOUP_HINT_WORDS = ("提示", "线索")
SOUP_END_WORDS = ("结束", "放弃", "揭晓", "答案", "汤底")
# What members type. Listed so team-game actions can't take them.
COMMANDS = ("/今日运势", "/生日", "/本月生日", "/投票", "/结束投票", "/投", "/征集意见", "/意见征集", "/意见",
            "/结束征集", "/结束征集意见", "/翻牌子", "/海龟汤", "/问", "/提问", "/猜")
DELETE_WORDS = ("删除", "取消", "清除")
TAKE_BACK_WORDS = ("撤回",) + DELETE_WORDS


def _pool(value):
    """A style entry used as a pool to draw from: always a list."""
    if value is None:
        return []
    return [str(v) for v in value] if isinstance(value, list) else [str(value)]


def parse_birthday(text):
    """(month, day) from "10-25", "10月25日", "10/25" or "1025", or None."""
    text = text.strip()
    match = re.fullmatch(r"(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*[日号]?", text) or re.fullmatch(r"(\d{2})(\d{2})", text)
    if not match:
        return None
    month, day = int(match.group(1)), int(match.group(2))
    try:
        datetime(2000, month, day)          # a leap year, so 2-29 is allowed
    except ValueError:
        return None
    return month, day


def read_flip_special(raw):
    """config.yaml's social.flip.special as {flipper: {member: chance in percent}}. A chance may be
    written 60 or "60%"; one that isn't a positive number, or is the flipper themself, is left
    out with a warning."""
    if not raw:
        return {}
    if not isinstance(raw, dict):
        _log.warning("[Social] social.flip.special 要写成「翻牌子的人: {被翻到的人: 几率}」，先不用")
        return {}
    tables = {}
    for flipper, table in raw.items():
        flipper = str(flipper)
        if not isinstance(table, dict):
            _log.warning(f"[Social] social.flip.special 里 {flipper[:8]}… 下面要写成「成员ID: 几率」，先不用这一位")
            continue
        chances = {}
        for member, chance in table.items():
            member = str(member)
            try:
                chance = float(str(chance).strip().rstrip("%"))
            except ValueError:
                chance = 0.0
            if math.isfinite(chance) and chance > 0 and member != flipper:
                chances[member] = chance
            else:
                _log.warning(f"[Social] social.flip.special：{flipper[:8]}… 翻到 {member[:8]}… 的几率要写大于 0 的数，"
                             "也不能是自己；这一行先不用")
        if chances:
            tables[flipper] = chances
    return tables


def _short(text, limit=30):
    return text if len(text) <= limit else text[:limit] + "…"


class SocialModule:
    def __init__(self, gateway, store=None, clock=None, is_admin=None, activity=None, rng=None, config=None,
                 nickname_of=None, count=None, has_left=None, find_nickname=None, soups=None):
        self.gw = gateway
        self.store = store or FunStore(root_path("data", "fun.db"))
        self.tz = pytz.timezone('Asia/Shanghai')
        self.clock = clock or (lambda: datetime.now(self.tz))
        self.is_admin = is_admin or (lambda group, member, role=None: False)
        # (group, since_ts) -> [(member_openid, nickname, valid messages)], or None where
        # the group manager doesn't track the group.
        self.activity = activity or (lambda group, since_ts: None)
        # (group, member_openid) -> their nickname while they're in a group the manager tracks ("" when
        # they have none), else None. For people in a 翻牌子 table who haven't posted this week.
        self.nickname_of = nickname_of or (lambda group, member: None)
        # (group, member_openid, nickname, counter) -> congratulations for any badge that earned, from
        # the group manager's achievements. Birthdays, polls voted in and 征集 joined each count once.
        self.count = count or (lambda group, member, nickname, key: [])
        # (group, member_openid) -> whether the group manager knows they've left: their birthdays stay
        # stored, but aren't listed or mentioned unless they come back.
        self.has_left = has_left or (lambda group, member: False)
        self.rng = rng or random.Random()
        # Members with their own chances of drawing particular people (config.yaml social.flip.special).
        social = (config or {}).get("social")
        flip = social.get("flip") if isinstance(social, dict) else None
        self.flip_special = read_flip_special(flip.get("special") if isinstance(flip, dict) else None)
        if self.flip_special:
            totals = "、".join(f"{sum(table.values()):g}%" for table in self.flip_special.values())
            _log.info(f"[Social] 翻牌子特别名单：{len(self.flip_special)} 位群友（名单里的人合计 {totals}，其余照常抽）")
            # (member_openid) -> their nickname in a group the manager tracks, or None if none knows them.
            if find_nickname is not None:
                self._report_flip_special(find_nickname)
        # Set by main.py when AI chat is on: (topic, [opinions]) -> her summary of them, or None.
        self.opinion_summarizer = None
        # Set by main.py when AI chat's fortune reading is on: (nickname, level, [宜], [忌], [their recent
        # messages]) -> her comment on the fortune, or None. Only while it's set are messages kept below.
        self.fortune_reader = None
        self.recent = {}        # (group, member) -> deque of (timestamp, text), in memory only
        self.swept = 0          # when messages past RECENT_SECONDS were last let go
        self.readings = {}      # (group, member, date) -> her comment, so asking again gives the same one
        # /海龟汤: {code: Puzzle} from prompts/soups.yaml; config.yaml's social.soup: false leaves it out.
        if soups is None:
            soups, problems = soup.load() if not isinstance(social, dict) or social.get("soup", True) else ({}, [])
            for problem in problems:
                _log.warning(f"[Social] 海龟汤的题库：{problem}")
        self.soups = soups
        # Set by main.py when AI chat is on: (汤面, 汤底, [关键点], what a member said, whether it's a
        # guess) -> the host's verdict as DeepSeek wrote it, read by modules/fun/soup.py. No host, no game.
        self.soup_host = None
        self.soup_pace = {}     # (group, member) -> when their latest questions and guesses came
        self.soup_told = set()  # those already told to slow down this time
        self.commands = {"/今日运势": self.cmd_fortune, "/生日": self.cmd_birthday, "/本月生日": self.cmd_month,
                         "/投票": self.cmd_poll, "/投": self.cmd_vote, "/结束投票": self.cmd_close,
                         "/征集意见": self.cmd_call, "/意见征集": self.cmd_call, "/意见": self.cmd_opinion,
                         "/结束征集": self.cmd_end_call, "/结束征集意见": self.cmd_end_call, "/翻牌子": self.cmd_flip,
                         "/海龟汤": self.cmd_soup, "/问": self.cmd_ask, "/提问": self.cmd_ask, "/猜": self.cmd_guess}
        self.pruned = None

    # ------------------------------------------------------------------ helpers

    def _now(self):
        return self.clock().astimezone(self.tz)

    def _name(self, message):
        return message.nickname or t("common.someone")

    def _split(self, text):
        chunks, current = [], ""
        for line in text.split("\n"):
            if current and len(current) + len(line) + 1 > MAX_MESSAGE_LENGTH:
                chunks.append(current)
                current = line
            else:
                current = f"{current}\n{line}" if current else line
        if current:
            chunks.append(current)
        return chunks

    async def _reply(self, message, text, buttons=None):
        try:
            chunks = self._split(text)
            for i, chunk in enumerate(chunks):
                await self.gw.send(message.group_openid, chunk, reply_to=message.id,
                                   buttons=buttons if i == len(chunks) - 1 else None)
        except Exception as e:
            _log.error(f"[Social] 回复失败: {e}")

    def _report_flip_special(self, find_nickname):
        """Log each special table with the nickname behind every ID, so a mistyped one shows."""
        unknown = set()

        def who(member):
            name = find_nickname(member)
            if name is None:
                unknown.add(member)
                return f"？（{member[:8]}…）"
            return f"{name or '没有昵称'}（{member[:8]}…）"

        for flipper, table in self.flip_special.items():
            drawn = "、".join(f"{who(member)} {chance:g}%" for member, chance in table.items())
            _log.info(f"[Social]   {who(flipper)} 翻到 {drawn}")
        if unknown:
            _log.warning(f"[Social] 翻牌子特别名单里有 {len(unknown)} 个 ID 在管理的群里找不到（上面的「？」）："
                         "多半是抄错了，或者 TA 还没在群里说过话。成员活跃表里点昵称就能复制 ID")

    def _prune(self, now):
        today = now.date().isoformat()
        if self.pruned != today:
            self.pruned = today
            ts = int(now.timestamp())
            # A 海龟汤 past its 24 hours gets a day more before it's closed here: until then the next
            # question still finds it, and the group is told what its truth was.
            self.store.close_stale(ts, POLL_DAYS * DAY, OPINION_DAYS * DAY, SOUP_HOURS * 3600, soup_grace=DAY)
            self.store.prune((now.date() - timedelta(days=2)).isoformat(), ts - POLL_KEEP_DAYS * DAY,
                             ts - OPINION_KEEP_DAYS * DAY, ts - SOUP_KEEP_DAYS * DAY)
            self.readings = {key: text for key, text in self.readings.items() if key[2] == today}
            self._sweep(ts)

    def _sweep(self, ts):
        """Let go of kept messages older than RECENT_SECONDS, and of anyone with none left."""
        self.swept = ts
        for key in list(self.recent):
            kept = self.recent[key]
            while kept and kept[0][0] <= ts - RECENT_SECONDS:
                kept.popleft()
            if not kept:
                del self.recent[key]

    # ------------------------------------------------------------------ intake

    async def observe(self, message):
        """Every message the bot sees: a birthday wish on the member's first one of their day, and,
        while fortune reading is on, their latest messages, in memory only."""
        group, member = message.group_openid, message.member_openid
        if not member or message.is_bot:
            return
        if self.fortune_reader is not None:
            ts = int(self._now().timestamp())
            text = " ".join(clean_text(message.content).split())      # no emoji or mention tags, so no IDs
            if text:
                self.recent.setdefault((group, member), deque(maxlen=RECENT_KEPT)).append((ts, text[:RECENT_CHARS]))
            if ts - self.swept >= SWEEP_SECONDS:
                self._sweep(ts)
        row = self.store.birthday(group, member)
        if row is None:
            return
        today = self._now().date()
        month, day = row["month"], row["day"]
        if (month, day) == (2, 29) and not _leap(today.year):
            day = 28                        # celebrated on the 28th in other years
        if (today.month, today.day) != (month, day):
            return
        if self.store.daily(group, member, "birthday")[0] == today.isoformat():
            return
        self.store.mark_daily(group, member, "birthday", today.isoformat())
        lines = [t("social.birthday.wish", member=self._name(message))]
        lines += self.count(group, member, message.nickname, "birthday")
        await self._reply(message, "\n".join(lines))

    async def handle_social(self, message):
        text = (message.content or "").strip()
        match = re.match(r"/[^\s@/]+", text)
        if not match:
            return False
        word, args = match.group(0), text[match.end():].strip()
        vote = re.fullmatch(r"/投([A-Ia-i1-9])", word)       # "/投A" without a space
        if vote:
            word, args = "/投", vote.group(1)
        opinion = re.fullmatch(r"/意见[:：]?(.+)", word)       # "/意见想玩狼人杀" without a space
        if opinion and word not in self.commands:
            word, args = "/意见", f"{opinion.group(1)} {args}".strip()
        said = re.fullmatch(r"(/问|/提问|/猜)[:：]?(.+)", word)    # "/问他是光头吗" without a space
        if said and word not in self.commands:
            word, args = said.group(1), f"{said.group(2)} {args}".strip()
        handler = self.commands.get(word)
        if handler is None:
            return False
        self._prune(self._now())
        try:
            await handler(message, args)
        except Exception as e:
            _log.error(f"[Social] 指令 {word} 出错: {e}")
            await self._reply(message, t("common.error", command=word, error=e))
        return True

    # ------------------------------------------------------------------ 今日运势

    async def cmd_fortune(self, message, args):
        style, now = current(), self._now()
        pools = {key: _pool(style.lookup(f"social.fortune.{key}")) for key in ("levels", "good", "bad", "comments")}
        seed = f"{message.group_openid}:{message.member_openid}:{now.date().isoformat()}"
        level, good, bad, comment = fortune.draw(seed, **pools)
        name = self._name(message)
        reading = await self._reading(message, name, level, good, bad, now)
        lines = [t("social.fortune.title", member=name, date=t("common.date", month=now.month, day=now.day)),
                 t("social.fortune.level", level=level),
                 t("social.fortune.good_line", items=join(good)),
                 t("social.fortune.bad_line", items=join(bad)),
                 reading or render(comment, member=name, level=level)]
        await self._reply(message, "\n".join(lines), buttons=[[(t("social.fortune.button"), "/今日运势")]])

    async def _reading(self, message, name, level, good, bad, now):
        """Her comment on today's fortune in light of what the member has said lately, or None for
        the style's own: without fortune reading, with nothing worth reading, or if it fails."""
        if self.fortune_reader is None:
            return None
        key = (message.group_openid, message.member_openid, now.date().isoformat())
        if key in self.readings:
            return self.readings[key]
        now_ts = int(now.timestamp())
        said = fortune.pick_recent([text for ts, text in self.recent.get(key[:2], ()) if ts > now_ts - RECENT_SECONDS])
        if not said:
            return None
        try:
            reading = await asyncio.wait_for(self.fortune_reader(name, level, good, bad, said), READING_SECONDS)
        except Exception as e:
            _log.warning(f"[Social] 今日运势的解读没写成: {type(e).__name__} {str(e)[:120]}")
            return None
        if reading:
            self.readings[key] = reading
        return reading

    # ------------------------------------------------------------------ birthdays

    async def cmd_birthday(self, message, args):
        group, member = message.group_openid, message.member_openid
        if not args:
            row = self.store.birthday(group, member)
            if row is None:
                await self._reply(message, t("social.birthday.none"))
            else:
                await self._reply(message, t("social.birthday.mine",
                                             date=t("common.date", month=row["month"], day=row["day"])))
            return
        if args in DELETE_WORDS:
            self.store.delete_birthday(group, member)
            await self._reply(message, t("social.birthday.deleted"))
            return
        parsed = parse_birthday(args)
        if parsed is None:
            await self._reply(message, t("social.birthday.usage"))
            return
        month, day = parsed
        self.store.set_birthday(group, member, message.nickname, month, day)
        await self._reply(message, t("social.birthday.saved", date=t("common.date", month=month, day=day)),
                          buttons=[[(t("social.birthday.month_button"), "/本月生日")]])

    async def cmd_month(self, message, args):
        """/本月生日: everyone in this group who shared a birthday in this month."""
        month = self._now().month
        rows = self._birthdays_in(message.group_openid, month)
        register = [[(t("social.birthday.register_button"), "/生日 ")]]      # typed in, for the date
        if not rows:
            await self._reply(message, t("social.birthday.month_none", month=month), buttons=register)
            return
        lines = [t("social.birthday.month_title", month=month, count=len(rows))]
        lines += [t("social.birthday.month_row", date=t("common.date", month=r["month"], day=r["day"]),
                    member=r["nickname"] or t("common.someone")) for r in rows]
        await self._reply(message, "\n".join(lines), buttons=register)

    def _birthdays_in(self, group, month):
        return [r for r in self.store.birthdays_in(group, month) if not self.has_left(group, r["member_openid"])]

    def today_for_ai(self, group):
        """For AI chat: today's birthdays, the open poll as (question, [(label, option, votes)]), and
        the open 征集意见 as (topic, opinions so far). Reads only; anything past its time counts as
        closed."""
        today = self._now().date()
        birthdays = []
        for row in self._birthdays_in(group, today.month):
            day = 28 if (row["month"], row["day"]) == (2, 29) and not _leap(today.year) else row["day"]
            if day == today.day:
                birthdays.append(row["nickname"] or t("common.someone"))
        poll = self.store.open_poll(group)
        if poll is not None and int(self._now().timestamp()) - poll["created_at"] >= POLL_DAYS * DAY:
            poll = None
        if poll is not None:
            votes = [v["choice"] for v in self.store.votes(poll["id"])]
            poll = (poll["question"], [(LETTERS[i], option, votes.count(i)) for i, option in enumerate(poll["options"])])
        call, opinions = self.store.open_call(group), None
        if call is not None and int(self._now().timestamp()) - call["created_at"] < OPINION_DAYS * DAY:
            opinions = (call["topic"], len(self.store.opinions(call["id"])))
        # The open 海龟汤 as the players see it. Its truth stays here: she can't let slip what she isn't told.
        cooking = self.store.open_soup(group)
        puzzle = self.soups.get(cooking["puzzle"]) if cooking is not None else None
        if puzzle is not None and int(self._now().timestamp()) - cooking["created_at"] >= SOUP_HOURS * 3600:
            puzzle = None
        return {"birthdays": birthdays, "poll": poll, "opinions": opinions, "soup": puzzle and puzzle.surface}

    # ------------------------------------------------------------------ polls

    def _open_poll(self, group):
        """The group's open poll, closing it first if it has run out of time."""
        poll = self.store.open_poll(group)
        if poll is not None and int(self._now().timestamp()) - poll["created_at"] >= POLL_DAYS * DAY:
            self.store.close_poll(poll["id"], poll["created_at"] + POLL_DAYS * DAY)
            return None
        return poll

    def _results(self, poll, closed=False):
        votes = self.store.votes(poll["id"])
        title = "social.poll.closed_title" if closed else "social.poll.title"
        lines = [t(title, question=poll["question"], creator=poll["creator_name"] or t("common.someone"),
                   total=len(votes))]
        for i, option in enumerate(poll["options"]):
            voters = [v["nickname"] or t("common.someone") for v in votes if v["choice"] == i]
            key = "social.poll.option" if voters else "social.poll.option_empty"
            lines.append(t(key, label=LETTERS[i], option=option, count=len(voters), voters=join(voters)))
        if not closed:
            lines.append(t("social.poll.footer", first=LETTERS[0], last=LETTERS[len(poll["options"]) - 1]))
        return "\n".join(lines)

    def _vote_buttons(self, poll):
        buttons = [(f"{LETTERS[i]}. {option[:6]}", f"/投 {LETTERS[i]}") for i, option in enumerate(poll["options"])]
        return [buttons[i:i + 5] for i in range(0, len(buttons), 5)]

    async def cmd_poll(self, message, args):
        """/投票 shows the open poll; /投票 问题 选项1 选项2 … starts one."""
        group = message.group_openid
        poll = self._open_poll(group)
        if not args:
            if poll is None:
                await self._reply(message, t("social.poll.none"))
            else:
                await self._reply(message, self._results(poll), buttons=self._vote_buttons(poll))
            return
        if poll is not None:
            await self._reply(message, t("social.poll.busy", question=poll["question"]))
            return
        parts = [p.strip() for p in (args.splitlines() if "\n" in args else args.split()) if p.strip()]
        if len(parts) < 3:
            await self._reply(message, t("social.poll.usage"))
            return
        if len(parts) - 1 > MAX_OPTIONS:
            await self._reply(message, t("social.poll.too_many", max=MAX_OPTIONS))
            return
        poll_id = self.store.create_poll(group, parts[0], parts[1:], message.member_openid, message.nickname,
                                         int(self._now().timestamp()))
        poll = self.store.poll(poll_id)
        await self._reply(message, self._results(poll), buttons=self._vote_buttons(poll))

    async def cmd_vote(self, message, args):
        """/投 A (or /投 1): vote in the open poll; voting again changes the vote."""
        poll = self._open_poll(message.group_openid)
        if poll is None:
            await self._reply(message, t("social.poll.none"))
            return
        choice = args.strip().upper()[:1]
        count = len(poll["options"])
        index = LETTERS.find(choice) if choice.isalpha() else (int(choice) - 1 if choice.isdigit() else -1)
        if not 0 <= index < count:
            await self._reply(message, t("social.poll.bad_choice", first=LETTERS[0], last=LETTERS[count - 1]))
            return
        first = self.store.vote(poll["id"], message.member_openid, message.nickname, index,
                                int(self._now().timestamp()))
        total = len(self.store.votes(poll["id"]))
        lines = [t("social.poll.voted", member=self._name(message), label=LETTERS[index],
                   option=poll["options"][index], total=total)]
        if first:                           # changing a vote doesn't count again
            lines += self.count(message.group_openid, message.member_openid, message.nickname, "vote")
        await self._reply(message, "\n".join(lines))

    async def cmd_close(self, message, args):
        """/结束投票: the poll's starter or an admin closes it and shows the result."""
        poll = self._open_poll(message.group_openid)
        if poll is None:
            await self._reply(message, t("social.poll.none"))
            return
        if poll["creator"] != message.member_openid and not self.is_admin(
                message.group_openid, message.member_openid, message.role):
            await self._reply(message, t("social.poll.not_yours"))
            return
        self.store.close_poll(poll["id"], int(self._now().timestamp()))
        await self._reply(message, self._results(poll, closed=True))

    # ------------------------------------------------------------------ 征集意见

    def _open_call(self, group):
        """The group's open 征集意见, closing it first if nobody ended it in time."""
        call = self.store.open_call(group)
        if call is not None and int(self._now().timestamp()) - call["created_at"] >= OPINION_DAYS * DAY:
            self.store.close_call(call["id"], call["created_at"] + OPINION_DAYS * DAY)
            return None
        return call

    @staticmethod
    def _people(opinions):
        return len({o["member_openid"] for o in opinions})

    async def cmd_call(self, message, args):
        """/征集意见 shows the open call; /征集意见 主题 starts one (admins)."""
        group = message.group_openid
        call = self._open_call(group)
        typed = [[(t("social.opinion.button"), "/意见 ")]]        # typed in, for their opinion
        if not args:
            if call is None:
                await self._reply(message, t("social.opinion.none"))
                return
            opinions = self.store.opinions(call["id"])
            await self._reply(message, t("social.opinion.status", topic=call["topic"],
                                         creator=call["creator_name"] or t("common.someone"),
                                         people=self._people(opinions), count=len(opinions),
                                         max=OPINIONS_PER_MEMBER), buttons=typed)
            return
        if not self.is_admin(group, message.member_openid, message.role):
            await self._reply(message, t("common.not_admin"))
            return
        if call is not None:
            await self._reply(message, t("social.opinion.busy", topic=call["topic"]))
            return
        topic = " ".join(args.split())          # on one line, as it heads every reply about it
        self.store.create_call(group, topic, message.member_openid, message.nickname, int(self._now().timestamp()))
        await self._reply(message, t("social.opinion.started", creator=self._name(message), topic=topic,
                                     max=OPINIONS_PER_MEMBER), buttons=typed)

    async def cmd_opinion(self, message, args):
        """/意见 内容 adds an opinion to the open call, a few each; /意见 撤回 takes back the latest."""
        group, member = message.group_openid, message.member_openid
        call = self._open_call(group)
        if call is None:
            await self._reply(message, t("social.opinion.none"))
            return
        text = args.lstrip(":： ").strip()
        if not text:
            await self._reply(message, t("social.opinion.usage", topic=call["topic"]))
            return
        if text in TAKE_BACK_WORDS:
            taken = self.store.take_back(call["id"], member)
            if taken is None:
                await self._reply(message, t("social.opinion.nothing_to_take_back"))
            else:
                await self._reply(message, t("social.opinion.taken_back", text=_short(taken)))
            return
        opinions = self.store.opinions(call["id"])
        if len(text) > OPINION_CHARS:
            await self._reply(message, t("social.opinion.too_long", max=OPINION_CHARS))
        elif sum(o["member_openid"] == member for o in opinions) >= OPINIONS_PER_MEMBER:
            await self._reply(message, t("social.opinion.member_full", max=OPINIONS_PER_MEMBER))
        elif len(opinions) >= OPINIONS_MAX:
            await self._reply(message, t("social.opinion.full", max=OPINIONS_MAX))
        else:
            self.store.add_opinion(call["id"], member, message.nickname, text, int(self._now().timestamp()))
            opinions = self.store.opinions(call["id"])
            lines = [t("social.opinion.added", member=self._name(message), people=self._people(opinions),
                       count=len(opinions))]
            if self.store.join_call(call["id"], member):      # once per 征集, even after a 撤回
                lines += self.count(group, member, message.nickname, "opinion")
            await self._reply(message, "\n".join(lines))

    async def cmd_end_call(self, message, args):
        """/结束征集: the call's starter or an admin ends it. Every opinion is listed, then her summary."""
        call = self._open_call(message.group_openid)
        if call is None:
            await self._reply(message, t("social.opinion.none"))
            return
        if call["creator"] != message.member_openid and not self.is_admin(
                message.group_openid, message.member_openid, message.role):
            await self._reply(message, t("social.opinion.not_yours"))
            return
        self.store.close_call(call["id"], int(self._now().timestamp()))
        opinions = self.store.opinions(call["id"])
        if not opinions:
            await self._reply(message, t("social.opinion.ended_empty", topic=call["topic"]))
            return
        lines = [t("social.opinion.ended_title", topic=call["topic"], people=self._people(opinions),
                   count=len(opinions))]
        lines += [t("social.opinion.row", n=i, member=o["nickname"] or t("common.someone"), text=o["text"])
                  for i, o in enumerate(opinions, 1)]
        await self._reply(message, "\n".join(lines))
        if self.opinion_summarizer is not None:
            summary = await self.opinion_summarizer(call["topic"], [o["text"] for o in opinions])
            if summary:
                await self._reply(message, t("social.opinion.summary", summary=summary))

    # ------------------------------------------------------------------ 翻牌子

    async def cmd_flip(self, message, args):
        group, member = message.group_openid, message.member_openid
        now = self._now()
        today = now.date().isoformat()
        day, drawn = self.store.daily(group, member, "flip")
        if day == today and not self.is_admin(group, member, message.role):     # admins flip as often as they like
            await self._reply(message, t("social.flip.again", target=drawn or t("common.someone")))
            return
        named = {openid: nickname for openid, nickname in message.mentioned_members if openid != member}
        if named:
            pool = {openid: (nickname or t("common.someone"), 1) for openid, nickname in named.items()}
        else:
            rows = self.activity(group, int(now.timestamp()) - WEEK)
            if rows is None:
                await self._reply(message, t("social.flip.need_mentions"))
                return
            pool = {r["member_openid"]: (r["nickname"] or t("common.someone"), r["n"])
                    for r in rows if r["member_openid"] != member}
        favoured = self._favoured(group, member, pool, only_pool=bool(named))
        if not pool and not favoured:
            await self._reply(message, t("social.flip.empty"))
            return
        target = self._draw(pool, favoured)
        self.store.mark_daily(group, member, "flip", today, target)
        await self._reply(message, t("social.flip.result", member=self._name(message), target=target),
                          buttons=[[(t("social.flip.button"), "/翻牌子")]])

    def _favoured(self, group, member, pool, only_pool):
        """{openid: (name, chance)}: the people in the flipper's own table (social.flip.special) who
        can come up here. With @s, only those @-ed; otherwise also anyone in the table who is still
        in the group but hasn't posted this week."""
        favoured = {}
        for openid, chance in self.flip_special.get(member, {}).items():
            if openid in pool:
                favoured[openid] = (pool[openid][0], chance)
            elif not only_pool:
                nickname = self.nickname_of(group, openid)
                if nickname is not None:
                    favoured[openid] = (nickname or t("common.someone"), chance)
        return favoured

    def _draw(self, pool, favoured):
        """Who comes up. Each favoured person comes up their chance (in percent) of the time; the
        rest of the time it's the usual draw among everyone else, weighted. When the chances add up
        to 100 or more, or nobody else is left, it's only among the favoured."""
        others = [(name, weight) for openid, (name, weight) in pool.items() if openid not in favoured]
        if favoured and (not others or self.rng.random() * 100 < sum(chance for _, chance in favoured.values())):
            names, weights = zip(*favoured.values())
        else:
            names, weights = zip(*others)
        return self.rng.choices(names, weights=weights)[0]

    # ------------------------------------------------------------------ 海龟汤

    def _soup(self, group):
        """(the group's open soup, its puzzle, None), or (None, None, a puzzle that just went cold).
        A soup that has sat for SOUP_HOURS, or whose puzzle is no longer in the file, is closed as
        nobody's; the first time that's noticed the group is told what its truth was."""
        row = self.store.open_soup(group)
        if row is None:
            return None, None, None
        puzzle = self.soups.get(row["puzzle"])
        if puzzle is not None and int(self._now().timestamp()) - row["created_at"] < SOUP_HOURS * 3600:
            return row, puzzle, None
        self.store.close_soup(row["id"], row["created_at"] + SOUP_HOURS * 3600)
        return None, None, puzzle

    @staticmethod
    def _cold(puzzle):
        return [t("social.soup.expired", title=puzzle.title, truth=puzzle.truth)] if puzzle else []

    @staticmethod
    def _soup_buttons():
        return [[(t("social.soup.ask_button"), "/问 "), (t("social.soup.guess_button"), "/猜 "),      # typed in
                 (t("social.soup.hint_button"), "/海龟汤 提示")]]

    async def cmd_soup(self, message, args):
        """/海龟汤 opens a puzzle, or shows the one that's open. /海龟汤 提示 gives its next hint;
        /海龟汤 结束, from whoever opened it or an admin, gives up and tells the truth."""
        if not self.soups or self.soup_host is None:
            await self._reply(message, t("social.soup.disabled" if not self.soups else "social.soup.no_ai"))
            return
        row, puzzle, cold = self._soup(message.group_openid)
        word = args.split()[0] if args.split() else ""
        if row is None and word in SOUP_HINT_WORDS + SOUP_END_WORDS:
            await self._reply(message, "\n".join(self._cold(cold) + [t("social.soup.none")]))
        elif word in SOUP_HINT_WORDS:
            await self._soup_hint(message, row, puzzle)
        elif word in SOUP_END_WORDS:
            await self._soup_end(message, row, puzzle)
        elif row is None:
            await self._soup_start(message, self._cold(cold))
        else:
            await self._soup_status(message, row, puzzle)

    async def _soup_start(self, message, lines):
        """Open the group's next puzzle: one it hasn't had, until it has had them all."""
        group = message.group_openid
        played = self.store.soup_played(group)
        puzzle = soup.pick(self.soups, played, self.rng)
        if all(code in played for code in self.soups):      # it has had them all: from the top again
            self.store.reset_soup_played(group)
        self.store.start_soup(group, puzzle.code, message.member_openid, message.nickname,
                              int(self._now().timestamp()))
        lines.append(t("social.soup.started", title=puzzle.title, surface=puzzle.surface, starter=self._name(message)))
        await self._reply(message, "\n".join(lines), buttons=self._soup_buttons())

    async def _soup_status(self, message, row, puzzle):
        asked = self.store.soup_turns(row["id"])
        lines = [t("social.soup.status", title=puzzle.title, surface=puzzle.surface, asked=len(asked),
                   hints=row["hints"])]
        clues = [turn for turn in asked if turn["answer"] != "unrelated"][-SOUP_CLUES:]
        if clues:
            lines.append(t("social.soup.clues"))
            lines += [t("social.soup.clue", question=turn["text"], answer=t(f"social.soup.short.{turn['answer']}"))
                      for turn in clues]
        await self._reply(message, "\n".join(lines), buttons=self._soup_buttons())

    async def _soup_hint(self, message, row, puzzle):
        """The puzzle's next hint, once the group has asked SOUP_HINT_EVERY more questions for it."""
        asked, given = len(self.store.soup_turns(row["id"])), row["hints"]
        if given >= len(puzzle.hints):
            await self._reply(message, t("social.soup.no_more_hints" if puzzle.hints else "social.soup.no_hints"))
        elif asked < (given + 1) * SOUP_HINT_EVERY:
            await self._reply(message, t("social.soup.hint_later", more=(given + 1) * SOUP_HINT_EVERY - asked))
        else:
            self.store.add_soup_hint(row["id"])
            await self._reply(message, t("social.soup.hint", n=given + 1, total=len(puzzle.hints),
                                         hint=puzzle.hints[given]), buttons=self._soup_buttons())

    async def _soup_end(self, message, row, puzzle):
        """Give up: whoever opened it, or an admin; anyone, once whoever opened it is gone."""
        if row["starter"] not in ("", message.member_openid) and not self.is_admin(
                message.group_openid, message.member_openid, message.role):
            await self._reply(message, t("social.soup.not_yours"))
            return
        self.store.close_soup(row["id"], int(self._now().timestamp()))
        await self._reply(message, t("social.soup.ended", title=puzzle.title, truth=puzzle.truth),
                          buttons=[[(t("social.soup.again_button"), "/海龟汤")]])

    def _soup_too_fast(self, group, member):
        """Whether this member is past SOUP_PER_MINUTE questions and guesses a minute, and whether
        they still have to be told: once, then nothing, since answering each would flood too."""
        key, now = (group, member), self._now().timestamp()
        recent = self.soup_pace.setdefault(key, deque())
        while recent and now - recent[0] >= 60:
            recent.popleft()
        if len(recent) < SOUP_PER_MINUTE:
            recent.append(now)
            self.soup_told.discard(key)
            return False, False
        tell = key not in self.soup_told
        self.soup_told.add(key)
        return True, tell

    async def _soup_verdict(self, puzzle, said, guess):
        """What the host makes of a question or a guess, as soup.parse_answer or parse_guess reads
        it; None if she couldn't say."""
        try:
            raw = await asyncio.wait_for(self.soup_host(puzzle.surface, puzzle.truth, list(puzzle.points), said, guess),
                                         SOUP_SECONDS)
        except Exception as e:
            _log.warning(f"[Social] 海龟汤的主持人没答上来: {type(e).__name__} {str(e)[:120]}")
            return None
        return soup.parse_guess(raw or "") if guess else soup.parse_answer(raw or "")

    async def cmd_ask(self, message, args):
        """/问 是非题: she answers 是, 不是, 无关 or 是也不是 from the open soup's truth."""
        await self._soup_say(message, args, guess=False)

    async def cmd_guess(self, message, args):
        """/猜 真相: whoever covers the puzzle's key points first has solved it."""
        await self._soup_say(message, args, guess=True)

    async def _soup_say(self, message, args, guess):
        group, member, name = message.group_openid, message.member_openid, self._name(message)
        row, puzzle, cold = self._soup(group) if self.soups and self.soup_host is not None else (None, None, None)
        if row is None:
            await self._reply(message, "\n".join(self._cold(cold) + [t("social.soup.none")]))
            return
        said = " ".join(args.lstrip(":： ").split())
        most = SOUP_GUESS if guess else SOUP_QUESTION
        asked = len(self.store.soup_turns(row["id"]))
        if not said:
            await self._reply(message, t("social.soup.guess_usage" if guess else "social.soup.ask_usage"))
            return
        if len(said) > most:
            await self._reply(message, t("social.soup.too_long", max=most))
            return
        if not guess and asked >= SOUP_TURNS:
            await self._reply(message, t("social.soup.enough", max=SOUP_TURNS))
            return
        fast, tell = self._soup_too_fast(group, member)
        if fast:
            if tell:
                await self._reply(message, t("social.soup.too_fast"))
            return
        verdict = await self._soup_verdict(puzzle, said, guess)
        ts = int(self._now().timestamp())
        still = self.store.open_soup(group)
        if still is None or still["id"] != row["id"]:       # it ended while she was thinking
            await self._reply(message, t("social.soup.none"))
        elif verdict is None:
            await self._reply(message, t("social.soup.failed"))
        elif not guess:
            answer, key = verdict
            if answer == soup.INVALID:
                await self._reply(message, t("social.soup.invalid"))
                return
            self.store.add_soup_turn(row["id"], member, message.nickname, "ask", said, answer, ts)
            line = t("social.soup.asked", member=name, question=said, answer=t(f"social.soup.answers.{answer}"))
            await self._reply(message, line + (t("social.soup.key") if key else ""), buttons=self._soup_buttons())
        elif verdict != soup.RIGHT:
            self.store.add_soup_turn(row["id"], member, message.nickname, "guess", said, verdict, ts)
            await self._reply(message, t(f"social.soup.{verdict}", member=name, guess=said),
                              buttons=self._soup_buttons())
        elif not self.store.close_soup(row["id"], ts, member, message.nickname):
            await self._reply(message, t("social.soup.none"))      # someone else got there first
        else:
            self.store.add_soup_turn(row["id"], member, message.nickname, "guess", said, verdict, ts)
            lines = [t("social.soup.solved", member=name, title=puzzle.title, truth=puzzle.truth, asked=asked)]
            lines += self.count(group, member, message.nickname, "soup")
            await self._reply(message, "\n".join(lines), buttons=[[(t("social.soup.again_button"), "/海龟汤")]])

    # ------------------------------------------------------------------ housekeeping

    def forget_member(self, group, member):
        self.store.forget_member(group, member)
        self.recent.pop((group, member), None)
        self.soup_pace.pop((group, member), None)
        self.readings = {key: text for key, text in self.readings.items() if key[:2] != (group, member)}

    def forget_group(self, group):
        self.store.forget_group(group)
        self.recent = {key: kept for key, kept in self.recent.items() if key[0] != group}
        self.soup_pace = {key: kept for key, kept in self.soup_pace.items() if key[0] != group}
        self.readings = {key: text for key, text in self.readings.items() if key[0] != group}


def _leap(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
