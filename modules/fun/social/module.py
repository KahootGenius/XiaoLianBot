"""Social commands: 今日运势, birthdays, polls and 翻牌子.

- /今日运势: a fortune per member per day, worded entirely by the style file.
- /生日 10-25: members opt in; on the day, the bot's reply to their first
  message wishes them happy birthday (QQ rations messages a bot starts on its
  own, so it waits for them to speak, as the newcomer greeting does).
- /投票 问题 选项…, /投 A, /结束投票: one open poll per group; anyone may start
  one; results list who voted for what.
- /翻牌子: a joke lucky draw among this week's active members, weighted by how
  much they posted; once a day each. @ a few people to draw among them instead.

Every line comes from the style files (social.*) via t().
"""
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
# What members type. Listed so team-game actions can't take them.
COMMANDS = ("/今日运势", "/生日", "/本月生日", "/投票", "/结束投票", "/投", "/翻牌子")
DELETE_WORDS = ("删除", "取消", "清除")


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


class SocialModule:
    def __init__(self, gateway, store=None, clock=None, is_admin=None, activity=None, rng=None):
        self.gw = gateway
        self.store = store or FunStore(root_path("data", "fun.db"))
        self.tz = pytz.timezone('Asia/Shanghai')
        self.clock = clock or (lambda: datetime.now(self.tz))
        self.is_admin = is_admin or (lambda group, member, role=None: False)
        # (group, since_ts) -> [(member_openid, nickname, valid messages)], or None where
        # the group manager doesn't track the group.
        self.activity = activity or (lambda group, since_ts: None)
        self.rng = rng or random.Random()
        self.commands = {"/今日运势": self.cmd_fortune, "/生日": self.cmd_birthday, "/本月生日": self.cmd_month,
                         "/投票": self.cmd_poll, "/投": self.cmd_vote, "/结束投票": self.cmd_close,
                         "/翻牌子": self.cmd_flip}
        self.pruned = None

    # ------------------------------------------------------------------ helpers

    def _now(self):
        return self.clock().astimezone(self.tz)

    def _name(self, message):
        return message.nickname or t("common.someone")

    async def _reply(self, message, text, buttons=None):
        try:
            await self.gw.send(message.group_openid, text, reply_to=message.id, buttons=buttons)
        except Exception as e:
            _log.error(f"[Social] 回复失败: {e}")

    def _prune(self, now):
        today = now.date().isoformat()
        if self.pruned != today:
            self.pruned = today
            self.store.prune((now.date() - timedelta(days=2)).isoformat(),
                             int(now.timestamp()) - POLL_KEEP_DAYS * DAY)

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
        await self._reply(message, t("social.birthday.wish", member=self._name(message)))

    async def handle_social(self, message):
        text = (message.content or "").strip()
        match = re.match(r"/[^\s@/]+", text)
        if not match:
            return False
        word, args = match.group(0), text[match.end():].strip()
        vote = re.fullmatch(r"/投([A-Ia-i1-9])", word)       # "/投A" without a space
        if vote:
            word, args = "/投", vote.group(1)
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
        await self._reply(message, t("social.birthday.saved", date=t("common.date", month=month, day=day)))

    async def cmd_month(self, message, args):
        """/本月生日: everyone in this group who shared a birthday in this month."""
        month = self._now().month
        rows = self.store.birthdays_in(message.group_openid, month)
        if not rows:
            await self._reply(message, t("social.birthday.month_none", month=month))
            return
        lines = [t("social.birthday.month_title", month=month, count=len(rows))]
        lines += [t("social.birthday.month_row", date=t("common.date", month=r["month"], day=r["day"]),
                    member=r["nickname"] or t("common.someone")) for r in rows]
        await self._reply(message, "\n".join(lines))

    def today_for_ai(self, group):
        """For AI chat: today's birthdays, and the open poll as (question, [(label, option, votes)]).
        Reads only; a poll past its time counts as closed."""
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
        return {"birthdays": birthdays, "poll": poll}

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
        self.store.vote(poll["id"], message.member_openid, message.nickname, index, int(self._now().timestamp()))
        total = len(self.store.votes(poll["id"]))
        await self._reply(message, t("social.poll.voted", member=self._name(message), label=LETTERS[index],
                                     option=poll["options"][index], total=total))

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

    # ------------------------------------------------------------------ 翻牌子

    async def cmd_flip(self, message, args):
        group, member = message.group_openid, message.member_openid
        now = self._now()
        today = now.date().isoformat()
        day, drawn = self.store.daily(group, member, "flip")
        if day == today:
            await self._reply(message, t("social.flip.again", target=drawn or t("common.someone")))
            return
        named = {openid: nickname for openid, nickname in message.mentioned_members if openid != member}
        if named:
            pool = [(nickname or t("common.someone"), 1) for nickname in named.values()]
        else:
            rows = self.activity(group, int(now.timestamp()) - WEEK)
            if rows is None:
                await self._reply(message, t("social.flip.need_mentions"))
                return
            pool = [(r["nickname"] or t("common.someone"), r["n"]) for r in rows if r["member_openid"] != member]
            if not pool:
                await self._reply(message, t("social.flip.empty"))
                return
        target = self.rng.choices([name for name, _ in pool], weights=[n for _, n in pool])[0]
        self.store.mark_daily(group, member, "flip", today, target)
        await self._reply(message, t("social.flip.result", member=self._name(message), target=target))

    # ------------------------------------------------------------------ housekeeping

    def forget_member(self, group, member):
        self.store.forget_member(group, member)

    def forget_group(self, group):
        self.store.forget_group(group)


def _leap(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
