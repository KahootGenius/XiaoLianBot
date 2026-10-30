"""Social commands: 今日运势, birthdays, polls, 征集意见 and 翻牌子.

- /今日运势: a fortune per member per day, worded entirely by the style file.
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

Every line comes from the style files (social.*) via t().
"""
import math
import random
import re
from datetime import datetime, timedelta
import pytz
from botpy import logging
from core.config import root_path
from core.style import current, join, render, t
from modules.fun.social import fortune
from modules.fun.social.store import FunStore


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
# What members type. Listed so team-game actions can't take them.
COMMANDS = ("/今日运势", "/生日", "/本月生日", "/投票", "/结束投票", "/投", "/征集意见", "/意见征集", "/意见",
            "/结束征集", "/结束征集意见", "/翻牌子")
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
                 nickname_of=None, count=None):
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
        self.rng = rng or random.Random()
        # Members with their own chances of drawing particular people (config.yaml social.flip.special).
        social = (config or {}).get("social")
        flip = social.get("flip") if isinstance(social, dict) else None
        self.flip_special = read_flip_special(flip.get("special") if isinstance(flip, dict) else None)
        if self.flip_special:
            totals = "、".join(f"{sum(table.values()):g}%" for table in self.flip_special.values())
            _log.info(f"[Social] 翻牌子特别名单：{len(self.flip_special)} 位群友（名单里的人合计 {totals}，其余照常抽）")
        # Set by main.py when AI chat is on: (topic, [opinions]) -> her summary of them, or None.
        self.opinion_summarizer = None
        self.commands = {"/今日运势": self.cmd_fortune, "/生日": self.cmd_birthday, "/本月生日": self.cmd_month,
                         "/投票": self.cmd_poll, "/投": self.cmd_vote, "/结束投票": self.cmd_close,
                         "/征集意见": self.cmd_call, "/意见征集": self.cmd_call, "/意见": self.cmd_opinion,
                         "/结束征集": self.cmd_end_call, "/结束征集意见": self.cmd_end_call, "/翻牌子": self.cmd_flip}
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

    def _prune(self, now):
        today = now.date().isoformat()
        if self.pruned != today:
            self.pruned = today
            ts = int(now.timestamp())
            self.store.prune((now.date() - timedelta(days=2)).isoformat(), ts - POLL_KEEP_DAYS * DAY,
                             ts - OPINION_KEEP_DAYS * DAY)

    # ------------------------------------------------------------------ intake

    async def observe(self, message):
        """Every message the bot sees: a birthday wish on the member's first one of their day."""
        group, member = message.group_openid, message.member_openid
        if not member or message.is_bot:
            return
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
        lines = [t("social.fortune.title", member=name, date=t("common.date", month=now.month, day=now.day)),
                 t("social.fortune.level", level=level),
                 t("social.fortune.good_line", items=join(good)),
                 t("social.fortune.bad_line", items=join(bad)),
                 render(comment, member=name, level=level)]
        await self._reply(message, "\n".join(lines), buttons=[[(t("social.fortune.button"), "/今日运势")]])

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
        rows = self.store.birthdays_in(message.group_openid, month)
        register = [[(t("social.birthday.register_button"), "/生日 ")]]      # typed in, for the date
        if not rows:
            await self._reply(message, t("social.birthday.month_none", month=month), buttons=register)
            return
        lines = [t("social.birthday.month_title", month=month, count=len(rows))]
        lines += [t("social.birthday.month_row", date=t("common.date", month=r["month"], day=r["day"]),
                    member=r["nickname"] or t("common.someone")) for r in rows]
        await self._reply(message, "\n".join(lines), buttons=register)

    def today_for_ai(self, group):
        """For AI chat: today's birthdays, the open poll as (question, [(label, option, votes)]), and
        the open 征集意见 as (topic, opinions so far). Reads only; anything past its time counts as
        closed."""
        today = self._now().date()
        birthdays = []
        for row in self.store.birthdays_in(group, today.month):
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
        return {"birthdays": birthdays, "poll": poll, "opinions": opinions}

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

    # ------------------------------------------------------------------ housekeeping

    def forget_member(self, group, member):
        self.store.forget_member(group, member)

    def forget_group(self, group):
        self.store.forget_group(group)


def _leap(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
