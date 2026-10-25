"""The group manager.

Tracks 有效发言 for the groups listed in config.yaml, sends the day-25
reminder, handles leave and exemptions, prepares removals (with admin
confirmation unless running fully automatic), and runs the fun parts:
status cards, leaderboard, achievements and the weekly digest.

Every line it says comes from the style files (styles/*.yaml) via t().
"""
import asyncio
import json
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta
import pytz
from botpy import logging
from core.config import root_path
from core.style import STYLES, join, t
from modules.manager import achievements, classifier, digest, rules
from modules.manager.openapi import OpenAPIError
from modules.manager.store import Store


_log = logging.get_logger()

# Words admins may type after /管理模式. What the bot calls each mode comes from the style.
MODE_ALIASES = {"演练": "dry_run", "确认": "confirm", "自动": "auto",
                "dry_run": "dry_run", "confirm": "confirm", "auto": "auto"}

DAY = 86400
MESSAGE_RETENTION_DAYS = 35     # activity metadata; covers the 30-day rule plus the weekly digest
ACTION_RETENTION_DAYS = 180     # audit log of reminders, leaves, removals
DEPARTED_RETENTION_DAYS = 180   # stats of removed/departed members, kept in case they rejoin
SNIPPET_LENGTH = 30
EXCLUDE_GRACE_DAYS = 7
MAX_MESSAGE_LENGTH = 900


@dataclass
class ManagerSettings:
    groups: list = field(default_factory=list)
    report_group: str = ""
    admins: set = field(default_factory=set)
    mode: str = "dry_run"
    check_time: str = "20:00"
    max_leave_days: int = 90
    self_service_leave: bool = True
    max_removals_per_day: int = 15
    breaker_ratio: float = 0.1
    announce_removals: bool = False
    mention_format: str = "text"
    rules_text: str = ""            # empty: the style's manager.rules.default_text
    store_snippets: bool = True
    greet_newcomers: bool = True    # welcome someone the first time they speak
    announce_achievements: bool = True
    announcements_per_hour: int = 3
    digest_enabled: bool = True
    digest_weekday: int = 7         # ISO weekday: 1 = Monday ... 7 = Sunday
    digest_time: str = "21:00"
    policy: rules.Policy = field(default_factory=rules.Policy)
    valid_message: classifier.ClassifierSettings = field(default_factory=classifier.ClassifierSettings)

    @classmethod
    def from_config(cls, cfg):
        cfg = cfg or {}
        s = cls()
        s.groups = [str(g) for g in cfg.get("groups") or []]
        s.report_group = str(cfg.get("report_group") or "")
        s.admins = {str(a) for a in cfg.get("admins") or []}
        for key in ("mode", "check_time", "max_leave_days", "self_service_leave", "max_removals_per_day",
                    "breaker_ratio", "announce_removals", "mention_format", "rules_text", "store_snippets",
                    "greet_newcomers"):
            if cfg.get(key) is not None:
                setattr(s, key, cfg[key])
        s.mode = MODE_ALIASES.get(str(s.mode), "dry_run")
        s.policy = rules.Policy(**{k: cfg[k] for k in ("remind_day", "remove_day", "min_notice_days", "exempt_admins")
                                   if cfg.get(k) is not None})
        s.valid_message = classifier.ClassifierSettings.from_config(cfg.get("valid_message"))
        badges = cfg.get("achievements") or {}
        s.announce_achievements = badges.get("announce", True)
        s.announcements_per_hour = badges.get("max_per_hour", 3)
        weekly = cfg.get("weekly_digest") or {}
        s.digest_enabled = weekly.get("enabled", True)
        s.digest_weekday = weekly.get("weekday", 7)
        s.digest_time = weekly.get("time", "21:00")
        return s


def _clock_time(text):
    hour, minute = str(text).split(":")
    return dtime(int(hour), int(minute))


def _parse_time(value):
    """RFC 3339 timestamp from the open API -> epoch seconds."""
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _as_bool(value):
    return None if value is None else bool(value)


def _shorten(text, limit):
    return text if len(text) <= limit else text[:limit] + "…"


# /成员表 fits about 8 members per phone screen.
MEMBER_PAGE_SIZE = 8
# /成员表 filter words. The same groupings as the web table's filter chips.
TABLE_FILTERS = {
    "提醒": lambda r: r["status_key"] == "reminded",
    "快到期": lambda r: r["days"] is not None and r["days"] >= 20 and r["status_key"] not in ("exempt", "leave", "gone"),
    "无效": lambda r: r["latest_valid"] is False,
    "有效": lambda r: r["latest_valid"] is True,
    "请假": lambda r: r["status_key"] == "leave",
    "豁免": lambda r: r["status_key"] == "exempt",
    "已移出": lambda r: r["status_key"] == "gone",
}


class ManagerModule:
    def __init__(self, gateway, config=None, store=None, clock=None, styles=None):
        self.settings = ManagerSettings.from_config((config or {}).get("manager"))
        self.gw = gateway
        self.styles = styles or STYLES
        self.tz = pytz.timezone('Asia/Shanghai')
        self.clock = clock or (lambda: datetime.now(self.tz))
        self.store = store or Store(root_path("data", "manager.db"))
        self.recent = {}           # group -> classifier.RecentMessages
        self.announced = {}        # group -> timestamps of recent achievement announcements
        self.forget_hooks = []     # f(group, member), called by /删除数据 so other modules forget too
        self.task = None
        self.commands = self._build_commands()

    @property
    def enabled(self):
        return bool(self.settings.groups)

    # ------------------------------------------------------------------ helpers

    def manages(self, group):
        return group in self.settings.groups

    def is_report_group(self, group):
        return bool(self.settings.report_group) and group == self.settings.report_group and not self.manages(group)

    def is_admin(self, group, member, role=None):
        if member in self.settings.admins or role in ("owner", "admin"):
            return True
        # Everyone in the dedicated admin group is an admin.
        if self.is_report_group(group):
            return True
        row = self.store.member(group, member)
        return bool(row and row["role"] in ("owner", "admin"))

    def known_nickname(self, group, member):
        """The nickname a member last posted under in a managed group, or None."""
        row = self.store.member(group, member)
        return row["nickname"] if row else None

    def mode(self, group):
        row = self.store.group(group)
        return (row["mode"] if row and row["mode"] else None) or self.settings.mode

    def _date(self, ts):
        d = datetime.fromtimestamp(ts, self.tz)
        return t("common.date", month=d.month, day=d.day)

    def _name(self, row_or_name):
        if row_or_name is None:
            return t("common.someone")
        if isinstance(row_or_name, str):
            return row_or_name or t("common.someone")
        return row_or_name["nickname"] or t("common.someone")

    def _mode_name(self, mode):
        return t(f"manager.modes.{mode}")

    def _mode_hint(self, mode):
        return t(f"manager.mode_hints.{mode}")

    def _mention(self, row):
        fmt = self.settings.mention_format
        if fmt == "tag":
            return f'<qqbot-at-user id="{row["member_openid"]}" />'
        if fmt == "text":
            return t("manager.mention_text", member=self._name(row))
        return fmt.format(openid=row["member_openid"], nickname=self._name(row))

    def _snapshot(self, row, leave):
        leave_end = None
        if leave is not None:
            leave_end = leave["end_at"] if leave["ended_at"] is None else min(leave["end_at"], leave["ended_at"])
        return rules.MemberSnapshot(
            member_openid=row["member_openid"],
            nickname=row["nickname"] or "",
            first_seen=row["first_seen"],
            joined_at=row["joined_at"],
            rejoined_at=row["rejoined_at"],
            last_valid_at=row["last_valid_at"],
            reminded_at=row["reminded_at"],
            exempt=bool(row["exempt"]),
            is_admin=row["member_openid"] in self.settings.admins or row["role"] in ("owner", "admin"),
            is_bot=bool(row["is_bot"]),
            status=row["status"],
            leave_start=leave["start_at"] if leave is not None else None,
            leave_end=leave_end,
        )

    def _tracking_start(self, group, now):
        """When we started seeing every message, or now if we haven't yet (so nobody looks silent)."""
        g = self.store.ensure_group(group)
        return g["tracking_start"] if g["tracking_start"] is not None else int(now.timestamp())

    def _tracking_problem(self, group):
        """Why the inactivity rule can't be trusted right now, or None if it can."""
        g = self.store.ensure_group(group)
        if g["tracking_start"] is None:
            return t("manager.feed.never")
        if g["at_seen_at"] and (g["full_seen_at"] or 0) < g["at_seen_at"] - 2 * DAY:
            return t("manager.feed.lost")
        return None

    def _evaluate_all(self, group, now):
        tracking_start = self._tracking_start(group, now)
        leaves = self.store.latest_leaves(group)
        result = []
        for row in self.store.members(group):
            snap = self._snapshot(row, leaves.get(row["member_openid"]))
            verdict = rules.evaluate(snap, now, self.settings.policy, tracking_start, self.tz)
            result.append((row, snap, verdict))
        return result

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

    async def _send(self, group, text):
        for chunk in self._split(text):
            await self.gw.send(group, chunk)

    async def _reply(self, message, text):
        try:
            for chunk in self._split(text):
                await self.gw.send(message.group_openid, chunk, reply_to=message.id)
        except Exception as e:
            _log.error(f"[Manager] 回复失败: {e}")

    async def _report(self, group, text, important=False):
        """Tell the admins. Goes to the admin group if one is configured; otherwise
        only important messages (removal lists, safety stops) go to the group itself."""
        target = self.settings.report_group if self.settings.report_group and self.settings.report_group != group else None
        if target is None and important:
            target = group
        if target is None:
            return
        try:
            await self._send(target, text)
        except Exception as e:
            _log.error(f"[Manager] 发送管理通知失败: {e}")

    # ------------------------------------------------------------------ intake

    async def observe(self, message):
        """Record one group message. Both event types call this; repeats are ignored."""
        group, member = message.group_openid, message.member_openid
        if not self.manages(group) or not member or message.is_bot:
            return
        now = self.clock()
        ts = int(now.timestamp())
        local = now.astimezone(self.tz)
        self.store.ensure_group(group)
        # Before the duplicate check: an @-message may arrive as both event
        # types, and the every-message one must be noted either way.
        self.store.saw_event(group, getattr(message, "event_type", None), ts)
        if self.store.has_message(message.id):
            return
        group_row = self.store.group(group)
        before = self.store.member(group, member)

        recent = self.recent.setdefault(group, classifier.RecentMessages(minutes=self.settings.valid_message.repeat_minutes))
        judgement = classifier.judge(
            message.raw_content,
            attachments=message.attachments_raw,
            message_type=message.message_type,
            mentions_bot=message.mentions_bot,
            member=member,
            recent=recent,
            now_ts=ts,
            settings=self.settings.valid_message,
        )
        self.store.record_message(message.id, group, member, ts, local.strftime("%Y-%m-%d"), local.hour,
                                  judgement.valid, judgement.kind)
        recent.remember(member, judgement, ts)
        self.store.touch_member(group, member, message.nickname, message.role, message.is_bot, ts)

        if before is None or before["last_message_at"] is None:
            await self._greet(message, before, group_row, ts)
        if before is not None and before["status"] != "active":
            # Removed or left earlier, and talking again: they rejoined.
            self.store.mark_rejoined(group, member, ts)
            self.store.log(ts, group, member, "rejoin")
            before = self.store.member(group, member)
        if judgement.valid:
            await self._on_valid(message, judgement, before, group_row, now)

    async def _greet(self, message, before, group_row, ts):
        """Welcome someone the first time they speak.

        QQ sends official bots no "member joined" event, so a first message is
        the earliest sign of a newcomer. Until the bot has seen every message
        for remove_day days, an old member who simply hadn't spoken yet looks
        the same, so the greeting then uses wording that fits both.
        """
        tracking_start = group_row["tracking_start"]
        # Without every-message access, "first message" only means "first @ to the bot".
        if not self.settings.greet_newcomers or tracking_start is None:
            return
        if self.is_admin(message.group_openid, message.member_openid, message.role):
            return
        window = self.settings.policy.remove_day * DAY
        joined = before["joined_at"] if before is not None else None
        new = ts - joined < window if joined is not None else ts - tracking_start >= window
        key = "manager.greet.new" if new else "manager.greet.first_words"
        await self._reply(message, t(key, member=self._name(message.nickname)))

    async def _on_valid(self, message, judgement, before, group_row, now):
        group, member = message.group_openid, message.member_openid
        ts = int(now.timestamp())
        local = now.astimezone(self.tz)
        today = local.date()

        reminded = comeback = False
        streak = best = 0
        streak_day = None
        if before is not None:
            snap = self._snapshot(before, self.store.latest_leave(group, member))
            tracking_start = group_row["tracking_start"] if group_row["tracking_start"] is not None else ts
            anchor = rules.anchor_of(snap, tracking_start, ts)
            reminded = before["reminded_at"] is not None and before["reminded_at"] >= anchor
            silent_days = rules.day_diff(anchor, now, self.tz)
            comeback = reminded or (before["last_valid_at"] is not None
                                    and silent_days >= achievements.COMEBACK_DAYS)
            streak, best, streak_day = before["streak"], before["best_streak"], before["streak_day"]

        first_of_day = streak_day != today.isoformat()
        if first_of_day:
            streak = streak + 1 if streak_day == (today - timedelta(days=1)).isoformat() else 1
        best = max(best, streak)
        snippet = judgement.text[:SNIPPET_LENGTH] if self.settings.store_snippets else None
        self.store.mark_valid(group, member, ts, snippet, streak, best, today.isoformat())
        self.store.bump(group, member, achievements.counter_increments(judgement, local, first_of_day))

        replies = []
        if reminded:
            self.store.clear_reminded(group, member)
            self.store.log(ts, group, member, "reminder_cleared")
            replies.append(t("manager.welcome_back"))
        replies += self._award_badges(group, member, message.nickname, streak, comeback, ts)
        for text in replies:
            await self._reply(message, text)

    def _award_badges(self, group, member, nickname, streak, comeback, ts):
        counters = self.store.counters(group, member)
        texts = []
        for code in achievements.qualified(counters, streak, comeback):
            if not self.store.award(group, member, code, ts):
                continue
            badge = achievements.BY_CODE[code]
            self.store.log(ts, group, member, "achievement", code)
            if badge.announce and self.settings.announce_achievements and self._may_announce(group, ts):
                texts.append(t("manager.achievement", member=self._name(nickname),
                               badge=f"{badge.icon}{badge.name}", desc=badge.desc))
        return texts

    def _may_announce(self, group, ts):
        recent = self.announced.setdefault(group, deque())
        while recent and ts - recent[0] > 3600:
            recent.popleft()
        if len(recent) >= self.settings.announcements_per_hour:
            return False
        recent.append(ts)
        return True

    # ------------------------------------------------------------------ commands

    def _build_commands(self):
        # where: "member" = managed groups only; "admin" = managed groups or the
        # admin group; "any" = every group (setup helpers).
        table = (
            (("/我的状态", "/状态"), self.cmd_status, False, "member"),
            (("/排行榜", "/排行"), self.cmd_leaderboard, False, "member"),
            (("/成就",), self.cmd_achievements, False, "member"),
            (("/请假",), self.cmd_leave, False, "member"),
            (("/销假",), self.cmd_end_leave, False, "member"),
            (("/群规",), self.cmd_rules, False, "member"),
            (("/我的ID",), self.cmd_my_id, False, "any"),
            (("/群ID",), self.cmd_group_id, False, "any"),
            (("/请假名单",), self.cmd_leave_list, True, "admin"),
            (("/撤销请假",), self.cmd_revoke_leave, True, "admin"),
            (("/管理帮助",), self.cmd_admin_help, True, "admin"),
            (("/管理状态",), self.cmd_manager_status, True, "admin"),
            (("/管理模式",), self.cmd_mode, True, "admin"),
            (("/豁免名单",), self.cmd_exempt_list, True, "admin"),
            (("/豁免",), self.cmd_exempt, True, "member"),
            (("/取消豁免",), self.cmd_unexempt, True, "member"),
            (("/删除数据",), self.cmd_forget_member, True, "member"),
            (("/不活跃",), self.cmd_inactive, True, "admin"),
            (("/成员表",), self.cmd_member_table, True, "admin"),
            (("/成员",), self.cmd_member_detail, True, "admin"),
            (("/待移出",), self.cmd_pending, True, "admin"),
            (("/确认移出",), self.cmd_confirm, True, "admin"),
            (("/取消移出",), self.cmd_cancel_batch, True, "admin"),
            (("/排除",), self.cmd_exclude, True, "admin"),
            (("/已移出",), self.cmd_mark_removed, True, "admin"),
            (("/立即检查",), self.cmd_check_now, True, "admin"),
            (("/小报",), self.cmd_digest_now, True, "admin"),
            (("/艾特测试",), self.cmd_mention_test, True, "member"),
        )
        entries = [(name, handler, admin_only, where)
                   for names, handler, admin_only, where in table for name in names]
        # Longest first, so "/请假名单" wins over "/请假".
        return sorted(entries, key=lambda e: len(e[0]), reverse=True)

    def _target_group(self, group, where):
        if where == "any" or self.manages(group):
            return group
        if where == "admin" and self.is_report_group(group) and self.settings.groups:
            return self.settings.groups[0]
        return None

    async def handle_manager(self, message):
        text = (message.content or "").strip()
        for name, handler, admin_only, where in self.commands:
            if not text.startswith(name):
                continue
            target = self._target_group(message.group_openid, where)
            if target is None:
                await self._reply(message, t("manager.not_managed"))
                return True
            if admin_only and not self.is_admin(message.group_openid, message.member_openid, message.role):
                await self._reply(message, t("common.not_admin"))
                return True
            try:
                await handler(message, target, text[len(name):].strip())
            except Exception as e:
                _log.error(f"[Manager] 指令 {name} 出错: {e}")
                await self._reply(message, t("common.error", command=name, error=e))
            return True
        return False

    # ---- member commands ----

    async def cmd_status(self, message, group, args):
        now = self.clock()
        ts = int(now.timestamp())
        member = message.member_openid
        self.store.ensure_member(group, member, message.nickname, ts)
        row = self.store.member(group, member)
        snap = self._snapshot(row, self.store.latest_leave(group, member))
        verdict = rules.evaluate(snap, now, self.settings.policy, self._tracking_start(group, now), self.tz)
        counters = self.store.counters(group, member)
        rank, week_count = self._rank(self.store.valid_counts(group, ts - 7 * DAY), member)
        badges = self.store.achievements(group, member)

        lines = [t("manager.status.title", member=self._name(row))]
        if row["last_valid_at"]:
            ago = rules.day_diff(row["last_valid_at"], now, self.tz)
            when = t("manager.status.today") if ago == 0 else t("manager.status.days_ago", days=ago)
            snippet = t("manager.status.snippet", text=row["last_valid_snippet"]) if row["last_valid_snippet"] else ""
            lines.append(t("manager.status.last_valid", date=self._date(row["last_valid_at"]), ago=when, snippet=snippet))
        else:
            lines.append(t("manager.status.no_valid"))
        yesterday = (now.astimezone(self.tz).date() - timedelta(days=1)).isoformat()
        streak = row["streak"] if row["streak_day"] and row["streak_day"] >= yesterday else 0
        lines.append(t("manager.status.streak", streak=streak, best=row["best_streak"]))
        lines.append(t("manager.status.week", count=week_count) + (t("manager.status.rank", rank=rank) if rank else ""))
        lines.append(t("manager.status.total", count=counters.get("valid", 0), earned=len(badges),
                       total=len(achievements.BADGES)))
        status, comment = self._status_line(verdict, snap, now)
        lines.append(t("manager.status.line", status=status))
        lines.append(comment)
        await self._reply(message, "\n".join(lines))

    def _status_line(self, verdict, snap, now):
        def state(key, **kwargs):
            return t(f"manager.status.states.{key}", **kwargs)

        def comment(key, **kwargs):
            return t(f"manager.status.comments.{key}", **kwargs)

        if verdict.action == "exempt":
            return state("exempt"), comment("exempt")
        if verdict.action == "admin":
            return state("admin"), comment("exempt")
        if verdict.action == "leave":
            return state("leave", date=self._date(snap.leave_end)), comment("on_leave")
        if verdict.action == "remove":
            return state("remove", days=verdict.days), comment("quiet")
        if verdict.reminded:
            return state("reminded", left=verdict.days_left), comment("reminded", left=verdict.days_left)
        if verdict.action == "remind":
            return state("remind", days=verdict.days), comment("quiet")
        if snap.last_valid_at is not None and rules.day_diff(snap.last_valid_at, now, self.tz) == 0:
            return state("today"), comment("active")
        until_reminder = self.settings.policy.remind_day - verdict.days
        if verdict.days == 0:
            # The clock was just reset by joining, a leave ending, or tracking starting.
            status = state("fresh", left=until_reminder)
        else:
            status = state("counting", days=verdict.days, left=until_reminder)
        lively = snap.last_valid_at is not None and verdict.days < 7
        return status, comment("active" if lively else "quiet")

    def _rank(self, rows, member):
        for i, r in enumerate(rows, 1):
            if r["member_openid"] == member:
                return i, r["n"]
        return None, 0

    async def cmd_leaderboard(self, message, group, args):
        days = 30 if "月" in args else 7
        ts = int(self.clock().timestamp())
        rows = self.store.valid_counts(group, ts - days * DAY)
        if not rows:
            await self._reply(message, t("manager.leaderboard.empty"))
            return
        places = ("first", "second", "third")
        lines = [t("manager.leaderboard.title", days=days)]
        for i, r in enumerate(rows[:10]):
            if i < len(places):
                place = t(f"manager.leaderboard.{places[i]}")
            else:
                place = t("manager.leaderboard.other", n=i + 1)
            lines.append(t("manager.leaderboard.row", place=place, member=self._name(r), count=r["n"]))
        rank, count = self._rank(rows, message.member_openid)
        if rank and rank > 10:
            lines.append(t("manager.leaderboard.you", rank=rank, count=count))
        await self._reply(message, "\n".join(lines))

    async def cmd_achievements(self, message, group, args):
        earned = {r["code"]: r["earned_at"] for r in self.store.achievements(group, message.member_openid)}
        lines = [t("manager.badges.title", member=self._name(message.nickname), earned=len(earned),
                   total=len(achievements.BADGES))]
        for badge in achievements.BADGES:
            if badge.code in earned:
                lines.append(t("manager.badges.row", icon=badge.icon, badge=badge.name, desc=badge.desc,
                               date=self._date(earned[badge.code])))
        locked = [b.name for b in achievements.BADGES if b.code not in earned]
        lines.append(t("manager.badges.locked", names=join(locked)) if locked else t("manager.badges.all_done"))
        await self._reply(message, "\n".join(lines))

    async def cmd_leave(self, message, group, args):
        requester = message.member_openid
        admin = self.is_admin(message.group_openid, requester, message.role)
        targets = message.mentioned_members
        if targets and not admin:
            await self._reply(message, t("common.not_admin"))
            return
        if not targets:
            if not self.settings.self_service_leave and not admin:
                await self._reply(message, t("manager.leave.ask_admin"))
                return
            targets = [(requester, message.nickname)]
        text = re.sub(r"@\S+", " ", args)
        match = re.search(r"\d+", text)
        days = int(match.group()) if match else 0
        if days < 1:
            await self._reply(message, t("manager.leave.usage", max=self.settings.max_leave_days))
            return
        if days > self.settings.max_leave_days:
            await self._reply(message, t("manager.leave.too_long", max=self.settings.max_leave_days))
            return
        reason = (text[:match.start()] + text[match.end():]).replace("天", "", 1).strip() or t("manager.leave.no_reason")
        ts = int(self.clock().timestamp())
        end_ts = ts + days * DAY
        ids = []
        for openid, nickname in targets:
            self.store.ensure_member(group, openid, nickname, ts)
            ids.append(self.store.add_leave(group, openid, ts, end_ts, reason, requester))
            self.store.log(ts, group, openid, "leave", f"{days}天 {reason}", requester)
        names = join([self._name(n) for _, n in targets])
        until = self._date(end_ts)
        await self._reply(message, t("manager.leave.granted", member=names, until=until))
        if not admin:
            await self._report(group, t("manager.leave.notice", member=names, days=days, until=until, reason=reason,
                                        ids=" ".join(map(str, ids))))

    async def cmd_end_leave(self, message, group, args):
        ts = int(self.clock().timestamp())
        leave = self.store.end_leave(group, message.member_openid, ts)
        if leave is None:
            await self._reply(message, t("manager.leave.none"))
            return
        self.store.log(ts, group, message.member_openid, "leave_ended")
        await self._reply(message, t("manager.leave.ended"))

    async def cmd_rules(self, message, group, args):
        c = self.settings.valid_message
        lines = [
            t("manager.rules.title"),
            (self.settings.rules_text or t("manager.rules.default_text")).strip(),
            "",
            t("manager.rules.explain", min_units=c.min_units, min_short=min(c.min_units_question, c.min_units_reply)),
            t("manager.rules.check_yourself"),
        ]
        await self._reply(message, "\n".join(lines))

    async def cmd_my_id(self, message, group, args):
        await self._reply(message, t("manager.ids.member", id=message.member_openid))

    async def cmd_group_id(self, message, group, args):
        await self._reply(message, t("manager.ids.group", id=message.group_openid))

    # ---- admin commands ----

    async def cmd_admin_help(self, message, group, args):
        await self._reply(message, t("manager.admin_help"))

    async def cmd_manager_status(self, message, group, args):
        now = self.clock()
        g = self.store.ensure_group(group)
        mode = self.mode(group)
        evaluated = self._evaluate_all(group, now)

        def count(action):
            return sum(1 for _, _, v in evaluated if v.action == action)

        def capability(value):
            return t({True: "manager.status_report.available", False: "manager.status_report.unavailable",
                      None: "manager.status_report.unknown"}[value])

        reminded = sum(1 for _, _, v in evaluated if v.reminded)
        member_list, remove = _as_bool(g["member_list_api"]), _as_bool(g["remove_api"])
        if g["tracking_start"] is None:
            since = t("manager.status_report.not_started")
        else:
            since = t("manager.status_report.since", date=self._date(g["tracking_start"]),
                      day=rules.day_diff(g["tracking_start"], now, self.tz) + 1)
        problem = self._tracking_problem(group)
        where = "to_admin_group" if self.settings.report_group else "to_this_group"
        lines = [
            t("manager.status_report.title"),
            t("manager.status_report.mode", mode=self._mode_name(mode), hint=self._mode_hint(mode)),
            since,
            t("manager.status_report.feed_ok") if problem is None else t("manager.status_report.feed_bad", problem=problem),
            t("manager.status_report.members", count=len(evaluated), leave=count("leave"), exempt=count("exempt"),
              reminded=reminded),
            t("manager.status_report.apis", member_list=capability(member_list), remove=capability(remove)),
            t("manager.status_report.schedule", time=self.settings.check_time,
              where=t(f"manager.status_report.{where}")),
        ]
        if member_list is not True:
            lines.append(t("manager.status_report.no_member_list"))
        await self._reply(message, "\n".join(lines))

    async def cmd_mode(self, message, group, args):
        if not args:
            mode = self.mode(group)
            await self._reply(message, t("manager.mode.current", mode=self._mode_name(mode), hint=self._mode_hint(mode)))
            return
        mode = MODE_ALIASES.get(args.split()[0])
        if mode is None:
            await self._reply(message, t("manager.mode.invalid"))
            return
        self.store.ensure_group(group)
        self.store.set_group_mode(group, mode)
        self.store.log(int(self.clock().timestamp()), group, None, "mode", mode, message.member_openid)
        await self._reply(message, t("manager.mode.switched", mode=self._mode_name(mode), hint=self._mode_hint(mode)))

    def _targets_and_reason(self, message, args):
        return message.mentioned_members, re.sub(r"@\S+", " ", args).strip()

    async def cmd_exempt(self, message, group, args):
        targets, reason = self._targets_and_reason(message, args)
        if not targets:
            await self._reply(message, t("manager.exempt.usage"))
            return
        ts = int(self.clock().timestamp())
        for openid, nickname in targets:
            self.store.ensure_member(group, openid, nickname, ts)
            self.store.set_exempt(group, openid, True, reason or None)
            self.store.log(ts, group, openid, "exempt", reason, message.member_openid)
        await self._reply(message, t("manager.exempt.added", members=join([self._name(n) for _, n in targets])))

    async def cmd_unexempt(self, message, group, args):
        targets, _ = self._targets_and_reason(message, args)
        if not targets:
            await self._reply(message, t("manager.exempt.remove_usage"))
            return
        ts = int(self.clock().timestamp())
        for openid, nickname in targets:
            self.store.ensure_member(group, openid, nickname, ts)
            self.store.set_exempt(group, openid, False)
            self.store.log(ts, group, openid, "unexempt", None, message.member_openid)
        await self._reply(message, t("manager.exempt.removed", members=join([self._name(n) for _, n in targets])))

    async def cmd_forget_member(self, message, group, args):
        """Delete everything recorded about someone, on their request (privacy policy, section 七)."""
        targets, _ = self._targets_and_reason(message, args)
        if not targets:
            await self._reply(message, t("manager.forget.usage"))
            return
        for openid, _ in targets:
            self.store.forget_member(group, openid)
            for hook in self.forget_hooks:
                hook(group, openid)
        await self._reply(message, t("manager.forget.done", members=join([self._name(n) for _, n in targets])))

    async def cmd_exempt_list(self, message, group, args):
        rows = self.store.exempt_members(group)
        if not rows:
            await self._reply(message, t("manager.exempt.empty"))
            return
        lines = [t("manager.exempt.list_title", count=len(rows))]
        for i, r in enumerate(rows, 1):
            reason = t("manager.exempt.reason", reason=r["exempt_reason"]) if r["exempt_reason"] else ""
            lines.append(t("manager.exempt.list_row", n=i, member=self._name(r), reason=reason))
        await self._reply(message, "\n".join(lines))

    async def cmd_leave_list(self, message, group, args):
        ts = int(self.clock().timestamp())
        leaves = self.store.active_leaves(group, ts)
        if not leaves:
            await self._reply(message, t("manager.leave.list_empty"))
            return
        rows = self.store.find_members(group, [l["member_openid"] for l in leaves])
        lines = [t("manager.leave.list_title", count=len(leaves))]
        lines += [t("manager.leave.list_row", id=l["id"], member=self._name(rows.get(l["member_openid"])),
                    until=self._date(l["end_at"]), reason=l["reason"])
                  for l in leaves]
        await self._reply(message, "\n".join(lines))

    async def cmd_revoke_leave(self, message, group, args):
        ts = int(self.clock().timestamp())
        revoked = []
        for openid, nickname in message.mentioned_members:
            if self.store.end_leave(group, openid, ts):
                revoked.append(self._name(nickname))
                self.store.log(ts, group, openid, "leave_revoked", None, message.member_openid)
        for leave_id in map(int, re.findall(r"\d+", re.sub(r"@\S+", " ", args))):
            leave = self.store.leave(leave_id)
            if leave is None or leave["ended_at"] is not None or leave["end_at"] <= ts:
                continue
            self.store.end_leave_by_id(leave_id, ts)
            self.store.log(ts, leave["group_openid"], leave["member_openid"], "leave_revoked", None, message.member_openid)
            revoked.append(self._name(self.store.member(leave["group_openid"], leave["member_openid"])))
        if revoked:
            await self._reply(message, t("manager.leave.revoked", members=join(revoked)))
        else:
            await self._reply(message, t("manager.leave.revoke_none"))

    async def _keep_private(self, message):
        """Lists of members' activity stay out of the main group once an admin group exists."""
        report_group = self.settings.report_group
        if self.manages(message.group_openid) and report_group and report_group != message.group_openid:
            await self._reply(message, t("manager.private_only"))
            return True
        return False

    async def cmd_inactive(self, message, group, args):
        if await self._keep_private(message):
            return
        match = re.search(r"\d+", args)
        limit = int(match.group()) if match else 15
        evaluated = [x for x in self._evaluate_all(group, self.clock())
                     if x[2].action in ("ok", "remind", "remove")]
        evaluated.sort(key=lambda x: x[2].days, reverse=True)
        if not evaluated:
            await self._reply(message, t("manager.inactive.empty"))
            return
        lines = [t("manager.inactive.title", count=min(limit, len(evaluated)))]
        for i, (row, _, verdict) in enumerate(evaluated[:limit], 1):
            tag = t("manager.inactive.reminded_tag") if verdict.reminded else ""
            lines.append(t("manager.inactive.row", n=i, member=self._name(row), days=verdict.days, tag=tag))
        await self._reply(message, "\n".join(lines))

    # ---- member table, phone-sized ----

    def _list_status(self, r):
        """The status as short as a phone line allows; nothing at all when it's simply 正常."""
        if r["status_key"] == "ok":
            return ""
        if r["status_key"] == "reminded":
            return t("manager.table.reminded")
        return r["status"].replace(" ", "")

    def _member_entry(self, i, r):
        head = [f"{i}. {_shorten(r['nickname'], 8)}"]
        if r["days"] is not None and r["status_key"] not in ("exempt", "gone"):
            head.append(t("manager.table.days", days=r["days"]))
        status = self._list_status(r)
        if status:
            head.append(status)
        verdict = ""
        if r["latest_valid"] is not None:
            mark = t("manager.table.valid_mark") if r["latest_valid"] else t("manager.table.invalid_mark")
            verdict = mark + r["latest_kind"]
        lines = [" · ".join(head), t("manager.table.latest", date=r["latest_at_text"][:5] or "—", verdict=verdict).rstrip()]
        if r["latest_valid"] is not True:
            lines.append(t("manager.table.last_valid", date=r["last_valid_at_text"][:5] or t("manager.table.no_valid")))
        return lines

    async def cmd_member_table(self, message, group, args):
        if await self._keep_private(message):
            return
        words = args.split()
        chosen = next((w for w in words if w in TABLE_FILTERS), None)
        page_word = next((w for w in words if w.isdigit()), None)
        rows = self.member_table(group)
        everyone = [r for r in rows if r["status_key"] != "gone"]
        shown = [r for r in rows if TABLE_FILTERS[chosen](r)] if chosen else everyone
        if not shown:
            await self._reply(message, t("manager.table.empty"))
            return
        pages = (len(shown) + MEMBER_PAGE_SIZE - 1) // MEMBER_PAGE_SIZE
        page = min(max(int(page_word or 1), 1), pages)

        def count(name):
            return sum(1 for r in everyone if TABLE_FILTERS[name](r))

        lines = [
            t("manager.table.title", filter=t("manager.table.filter", filter=chosen) if chosen else "",
              page=page, pages=pages),
            t("manager.table.summary", count=len(everyone), reminded=count("提醒"), due=count("快到期"),
              leave=count("请假")),
        ]
        start = (page - 1) * MEMBER_PAGE_SIZE
        for i, r in enumerate(shown[start:start + MEMBER_PAGE_SIZE], start + 1):
            lines += self._member_entry(i, r)
        footer = []
        if page < pages:
            footer.append(t("manager.table.next", args=f"{chosen + ' ' if chosen else ''}{page + 1}"))
        footer.append(t("manager.table.detail_hint"))
        lines.append("　".join(footer))
        if not chosen:
            lines.append(t("manager.table.filters", words=" ".join(TABLE_FILTERS)))
        await self._reply(message, "\n".join(lines))

    def _member_card(self, r):
        status = r["status"]
        if r["status_key"] == "reminded":
            status += t("manager.card.left", days=r["days_left"])
        if r["days"] is not None and r["status_key"] in ("ok", "reminded", "due"):
            status += t("manager.card.days", days=r["days"])
        if r["latest_valid"] is None:
            verdict = t("manager.card.expired") if r["latest_at_text"] else ""
        else:
            verdict = t("manager.card.valid" if r["latest_valid"] else "manager.card.invalid", kind=r["latest_kind"])
        lines = [
            t("manager.card.title", member=r["nickname"]),
            t("manager.card.status", status=status),
            t("manager.card.latest", time=r["latest_at_text"] or "—", verdict=verdict).rstrip(),
            t("manager.card.last_valid", time=r["last_valid_at_text"] or t("manager.card.never")),
        ]
        if r["snippet"]:
            lines.append(t("manager.card.snippet", text=r["snippet"]))
        lines.append(t("manager.card.counts", week=r["valid_7d"], total=r["valid_total"]))
        lines.append(t("manager.card.first_seen", time=r["first_seen_text"]))
        return "\n".join(lines)

    async def cmd_member_detail(self, message, group, args):
        if await self._keep_private(message):
            return
        rows = self.member_table(group)
        mentioned = {openid for openid, _ in message.mentioned_members}
        name = re.sub(r"@\S+", " ", args).strip()
        if mentioned:
            found = [r for r in rows if r["member_openid"] in mentioned]
        elif name:
            found = ([r for r in rows if r["nickname"] == name]
                     or [r for r in rows if name.lower() in r["nickname"].lower()])
        else:
            await self._reply(message, t("manager.card.usage"))
            return
        if not found:
            await self._reply(message, t("manager.card.not_found", query=name or t("manager.card.this_member")))
        elif len(found) == 1 or mentioned:
            await self._reply(message, "\n\n".join(self._member_card(r) for r in found[:3]))
        else:
            names = join([r["nickname"] for r in found[:8]]) + (t("manager.card.and_more") if len(found) > 8 else "")
            await self._reply(message, t("manager.card.ambiguous", count=len(found), names=names))

    # ---- removal batches ----

    def _pick_batch(self, group, args, statuses=("pending", "manual")):
        """The batch named by "#12"/"12" in args, else the newest open batch of the group."""
        match = re.search(r"#?(\d+)", args or "")
        if match:
            batch = self.store.batch(int(match.group(1)))
            return batch if batch and batch["status"] in statuses else None
        candidates = [b for b in self.store.open_batches(group) if b["status"] in statuses]
        return candidates[-1] if candidates else None

    def _format_batch(self, batch):
        openids = json.loads(batch["members"])
        rows = self.store.find_members(batch["group_openid"], openids)
        manual = batch["status"] == "manual"
        title = "manager.batch.manual_title" if manual else "manager.batch.pending_title"
        lines = [t(title, id=batch["id"], count=len(openids))]
        for i, openid in enumerate(openids, 1):
            row = rows.get(openid)
            last = self._date(row["last_valid_at"]) if row and row["last_valid_at"] else t("manager.batch.never_valid")
            reminded = t("manager.batch.reminded", date=self._date(row["reminded_at"])) if row and row["reminded_at"] else ""
            lines.append(t("manager.batch.row", n=i, member=self._name(row), last=last, reminded=reminded))
        if manual:
            lines.append(t("manager.batch.how_manual", id=batch["id"]))
        else:
            lines.append(t("manager.batch.how_pending", id=batch["id"], grace=EXCLUDE_GRACE_DAYS))
        return "\n".join(lines)

    async def cmd_pending(self, message, group, args):
        batches = self.store.open_batches(group)
        if not batches:
            await self._reply(message, t("manager.batch.none_open"))
            return
        await self._reply(message, "\n\n".join(self._format_batch(b) for b in batches))

    async def cmd_confirm(self, message, group, args):
        batch = self._pick_batch(group, args, statuses=("pending",))
        if batch is None:
            await self._reply(message, t("manager.batch.none_pending"))
            return
        await self._reply(message, await self._execute_batch(batch, actor=message.member_openid))

    async def cmd_cancel_batch(self, message, group, args):
        batch = self._pick_batch(group, args)
        if batch is None:
            await self._reply(message, t("manager.batch.none_to_cancel"))
            return
        ts = int(self.clock().timestamp())
        self.store.update_batch(batch["id"], status="cancelled", decided_by=message.member_openid, ts=ts)
        self.store.log(ts, batch["group_openid"], None, "batch_cancelled", str(batch["id"]), message.member_openid)
        await self._reply(message, t("manager.batch.cancelled", id=batch["id"]))

    async def cmd_exclude(self, message, group, args):
        batch_match = re.search(r"#(\d+)", args)
        batch = self._pick_batch(group, f"#{batch_match.group(1)}" if batch_match else "")
        indexes = {int(n) for n in re.findall(r"(?<!#)\b\d+\b", re.sub(r"#\d+", " ", args))}
        if batch is None or not indexes:
            await self._reply(message, t("manager.batch.exclude_usage"))
            return
        openids = json.loads(batch["members"])
        excluded = [openids[i - 1] for i in sorted(indexes) if 1 <= i <= len(openids)]
        if not excluded:
            await self._reply(message, t("manager.batch.exclude_bad"))
            return
        ts = int(self.clock().timestamp())
        bg = batch["group_openid"]
        for openid in excluded:
            # A short leave keeps tomorrow's check from listing them again.
            self.store.add_leave(bg, openid, ts, ts + EXCLUDE_GRACE_DAYS * DAY, t("manager.batch.exclude_reason"),
                                 message.member_openid)
            self.store.log(ts, bg, openid, "excluded", str(batch["id"]), message.member_openid)
        remaining = [o for o in openids if o not in excluded]
        if remaining:
            self.store.update_batch(batch["id"], members=remaining)
            await self._reply(message, self._format_batch(self.store.batch(batch["id"])))
        else:
            self.store.update_batch(batch["id"], members=[], status="cancelled", decided_by=message.member_openid, ts=ts)
            await self._reply(message, t("manager.batch.all_excluded", id=batch["id"]))

    async def cmd_mark_removed(self, message, group, args):
        batch = self._pick_batch(group, args, statuses=("manual",))
        if batch is None:
            await self._reply(message, t("manager.batch.none_manual"))
            return
        ts = int(self.clock().timestamp())
        bg = batch["group_openid"]
        openids = json.loads(batch["members"])
        self.store.set_status(bg, openids, "removed", ts)
        for openid in openids:
            self.store.log(ts, bg, openid, "removed_manually", str(batch["id"]), message.member_openid)
        self.store.update_batch(batch["id"], status="done", decided_by=message.member_openid, ts=ts)
        await self._reply(message, t("manager.batch.marked", id=batch["id"], count=len(openids)))
        if self.settings.announce_removals:
            with self.styles.scope(bg):
                await self._send(bg, t("manager.removal_notice", count=len(openids)))

    async def cmd_check_now(self, message, group, args):
        summary = await self.daily_check(group, manual=True)
        await self._reply(message, summary)

    async def cmd_digest_now(self, message, group, args):
        await self._reply(message, self.build_digest(group))

    async def cmd_mention_test(self, message, group, args):
        mention = f'<qqbot-at-user id="{message.member_openid}" />'
        await self._reply(message, t("manager.mention_test", mention=mention))

    # ------------------------------------------------------------------ daily check

    async def _capabilities(self, group, now):
        """Probe the invite-only APIs at most once a day; results are cached in the database."""
        ts = int(now.timestamp())
        g = self.store.ensure_group(group)
        if g["probed_at"] is None or rules.day_diff(g["probed_at"], now, self.tz) >= 1:
            try:
                found = await self.gw.probe(group)
            except Exception as e:
                _log.warning(f"[Manager] 接口探测失败: {e}")
                found = {}
            as_int = lambda v: None if v is None else int(v)
            self.store.set_capabilities(group, as_int(found.get("member_list")), as_int(found.get("remove")), ts)
            g = self.store.group(group)
        return {"member_list": _as_bool(g["member_list_api"]), "remove": _as_bool(g["remove_api"])}

    async def _sync_members(self, group, ts):
        """Pull the full member list, so people who never speak are tracked too."""
        try:
            members = await self.gw.list_members(group)
        except Exception as e:
            _log.warning(f"[Manager] 获取群成员列表失败: {e}")
            return
        if not members:
            return
        seen = set()
        for m in members:
            openid = m.get("member_openid")
            if not openid:
                continue
            seen.add(openid)
            joined = _parse_time(m.get("joined_at"))
            row = self.store.member(group, openid)
            if row is not None and row["status"] != "active":
                gone_at = row["removed_at"] or 0
                if (joined is not None and joined > gone_at) or (joined is None and ts - gone_at > DAY):
                    self.store.mark_rejoined(group, openid, ts, joined_at=joined)
                    self.store.log(ts, group, openid, "rejoin")
            self.store.sync_member(group, openid, m.get("username"), m.get("member_role"), m.get("bot"), joined, ts)
        for row in self.store.members(group):
            if row["member_openid"] not in seen:
                self.store.set_status(group, [row["member_openid"]], "left", ts)
                self.store.log(ts, group, row["member_openid"], "left")

    async def daily_check(self, group, manual=False):
        """Apply the rule to one group. Returns a summary for admins."""
        now = self.clock()
        ts = int(now.timestamp())
        self.store.ensure_group(group)
        problem = self._tracking_problem(group)
        if problem:
            text = t("manager.daily.warning", problem=problem)
            # Say it once a week, not every evening.
            week = "%d-W%02d" % now.astimezone(self.tz).isocalendar()[:2]
            if not manual and self.store.get_kv(f"warned:{group}") != week:
                self.store.set_kv(f"warned:{group}", week)
                await self._report(group, text, important=True)
            return text
        mode = self.mode(group)
        caps = await self._capabilities(group, now)
        if caps["member_list"]:
            await self._sync_members(group, ts)

        for batch in self.store.open_batches(group):
            if batch["status"] == "pending" and rules.day_diff(batch["created_at"], now, self.tz) >= 1:
                self.store.update_batch(batch["id"], status="expired", ts=ts)

        evaluated = self._evaluate_all(group, now)
        to_remind = [x for x in evaluated if x[2].action == "remind"]
        to_remove = sorted((x for x in evaluated if x[2].action == "remove"), key=lambda x: x[2].days, reverse=True)

        if mode == "dry_run":
            summary = self._dry_run_summary(to_remind, to_remove)
            if not manual and (to_remind or to_remove):
                await self._report(group, summary)
            return summary

        parts = []
        if to_remind:
            reminded = await self._send_reminders(group, to_remind, ts)
            if reminded:
                line = t("manager.daily.reminded", count=len(reminded), members=join([self._name(r) for r in reminded]))
                parts.append(line)
                if not manual:
                    await self._report(group, line)
        if to_remove:
            # Removal lists and safety stops report themselves.
            parts.append(await self._handle_removals(group, to_remove, mode, caps, ts))
        return "\n\n".join(parts) or t("manager.daily.nothing")

    def _dry_run_summary(self, to_remind, to_remove):
        def names(items):
            return join([t("manager.daily.dry_member", member=self._name(r), days=v.days) for r, _, v in items])

        lines = [t("manager.daily.dry_title")]
        if to_remind:
            lines.append(t("manager.daily.dry_remind", count=len(to_remind), members=names(to_remind)))
        if to_remove:
            lines.append(t("manager.daily.dry_remove", count=len(to_remove), members=names(to_remove)))
        if len(lines) == 1:
            lines.append(t("manager.daily.nothing"))
        return "\n".join(lines)

    async def _send_reminders(self, group, items, ts):
        """One @ message per batch of 20, grouped by how many days are left."""
        by_wording = {}
        for row, _, verdict in items:
            by_wording.setdefault((verdict.days, verdict.days_left), []).append(row)
        reminded = []
        for (days, left), rows in sorted(by_wording.items()):
            for i in range(0, len(rows), 20):
                chunk = rows[i:i + 20]
                text = t("manager.remind", mentions=" ".join(self._mention(r) for r in chunk), days=days, left=left)
                try:
                    await self._send(group, text)
                except Exception as e:
                    # Not marked as reminded, so tomorrow's check tries again.
                    _log.error(f"[Manager] 发送提醒失败: {e}")
                    continue
                openids = [r["member_openid"] for r in chunk]
                self.store.set_reminded(group, openids, ts)
                for openid in openids:
                    self.store.log(ts, group, openid, "remind", f"{days}天")
                reminded += chunk
        return reminded

    async def _handle_removals(self, group, items, mode, caps, ts):
        limit = self.settings.max_removals_per_day
        ceiling = max(limit, int(len(self.store.members(group)) * self.settings.breaker_ratio))
        if len(items) > ceiling:
            text = t("manager.daily.breaker", count=len(items), limit=ceiling)
            self.store.log(ts, group, None, "breaker", str(len(items)))
            await self._report(group, text, important=True)
            return text
        already = set()
        for batch in self.store.open_batches(group):
            already.update(json.loads(batch["members"]))
        openids = [row["member_openid"] for row, _, _ in items[:limit] if row["member_openid"] not in already]
        if not openids:
            return t("manager.batch.waiting")
        if caps["remove"] is False:
            status = "manual"
        else:
            status = "pending"
        batch_id = self.store.create_batch(group, openids, status, ts)
        batch = self.store.batch(batch_id)
        if mode == "auto" and status == "pending":
            return await self._execute_batch(batch, actor="auto")
        text = self._format_batch(batch)
        await self._report(group, text, important=True)
        return text

    async def _execute_batch(self, batch, actor):
        group = batch["group_openid"]
        openids = json.loads(batch["members"])
        ts = int(self.clock().timestamp())
        try:
            await self.gw.remove_members(group, openids)
        except OpenAPIError as e:
            if e.no_permission:
                g = self.store.group(group)
                self.store.set_capabilities(group, g["member_list_api"], 0, ts)
                self.store.update_batch(batch["id"], status="manual", decided_by=actor, ts=ts)
                text = self._format_batch(self.store.batch(batch["id"]))
                await self._report(group, text, important=True)
                return text
            text = t("manager.batch.failed_api", error=e.message or e, trace=e.trace_id, id=batch["id"])
            await self._report(group, text, important=True)
            return text
        except Exception as e:
            text = t("manager.batch.failed", error=e, id=batch["id"])
            await self._report(group, text, important=True)
            return text

        rows = self.store.find_members(group, openids)
        self.store.set_status(group, openids, "removed", ts)
        for openid in openids:
            self.store.log(ts, group, openid, "remove", str(batch["id"]), actor)
        self.store.update_batch(batch["id"], status="executed", decided_by=actor, ts=ts)
        text = t("manager.batch.removed", count=len(openids), members=join([self._name(rows.get(o)) for o in openids]))
        if actor == "auto":
            await self._report(group, text)
        if self.settings.announce_removals:
            with self.styles.scope(group):
                await self._send(group, t("manager.removal_notice", count=len(openids)))
        return text

    # ------------------------------------------------------------------ weekly digest

    def build_digest(self, group):
        now = self.clock()
        ts = int(now.timestamp())
        since = ts - 7 * DAY
        tracking_start = self._tracking_start(group, now)
        totals = self.store.totals(group, since, ts)
        previous = None
        if since - 7 * DAY >= tracking_start:
            previous = self.store.totals(group, since - 7 * DAY, since)["valid"]
        top = [(self._name(r), r["n"]) for r in self.store.valid_counts(group, since, ts)[:3]]
        local = now.astimezone(self.tz).date()
        return digest.build(
            start=local - timedelta(days=6),
            end=local,
            totals=totals,
            previous_valid=previous,
            busiest_hour=self.store.busiest_hour(group, since, ts),
            top=top,
            newcomers=self.store.newcomers(group, since, ts, tracking_start),
            badges=self.store.achievements_since(group, since),
            on_leave=len(self.store.active_leaves(group, ts)),
        )

    # ------------------------------------------------------------------ member table

    def _stamp(self, ts):
        return datetime.fromtimestamp(ts, self.tz).strftime("%m-%d %H:%M") if ts else ""

    def _table_status(self, verdict, snap, row):
        """(label, key) for the member table; the key drives its filters."""
        def label(key, **kwargs):
            return t(f"manager.states.{key}", **kwargs)

        if verdict.action == "gone":
            return label("removed" if row["status"] == "removed" else "left"), "gone"
        if verdict.action == "bot":
            return label("bot"), "gone"
        if verdict.action == "exempt":
            return label("exempt"), "exempt"
        if verdict.action == "admin":
            return label("admin"), "exempt"
        if verdict.action == "leave":
            return label("leave", date=self._date(snap.leave_end)), "leave"
        if verdict.action == "remove":
            return label("remove"), "due"
        if verdict.reminded:
            return label("reminded", date=self._date(snap.reminded_at)), "reminded"
        if verdict.action == "remind":
            return label("remind"), "due"
        return label("ok"), "ok"

    def _kind_label(self, kind):
        """Why a message counted or not, in words; unknown kinds pass through as they are."""
        if not kind:
            return ""
        return t(f"manager.kinds.{kind}") if self.styles.default().lookup(f"manager.kinds.{kind}") else kind

    def member_table(self, group):
        """One row per recorded member: their latest message, whether it counted, and where they stand."""
        now = self.clock()
        ts = int(now.timestamp())
        tracking_start = self._tracking_start(group, now)
        leaves = self.store.latest_leaves(group)
        week = {r["member_openid"]: r["n"] for r in self.store.valid_counts(group, ts - 7 * DAY)}
        rows = []
        for row in self.store.member_activity(group):
            snap = self._snapshot(row, leaves.get(row["member_openid"]))
            verdict = rules.evaluate(snap, now, self.settings.policy, tracking_start, self.tz)
            status, status_key = self._table_status(verdict, snap, row)
            latest_at = row["latest_ts"] or row["last_message_at"]
            counted = verdict.action not in ("gone", "bot")
            rows.append({
                "member_openid": row["member_openid"],
                "nickname": self._name(row),
                "status": status,
                "status_key": status_key,
                "latest_at": latest_at,
                "latest_at_text": self._stamp(latest_at),
                # None once the message has aged out of the 35-day message log.
                "latest_valid": None if row["latest_valid"] is None else bool(row["latest_valid"]),
                "latest_kind": self._kind_label(row["latest_kind"]),
                "last_valid_at": row["last_valid_at"],
                "last_valid_at_text": self._stamp(row["last_valid_at"]),
                "snippet": row["last_valid_snippet"] or "",
                "days": verdict.days if counted else None,
                "days_left": verdict.days_left if counted else None,
                "valid_7d": week.get(row["member_openid"], 0),
                "valid_total": row["valid_total"],
                "first_seen": row["first_seen"],
                "first_seen_text": self._stamp(row["first_seen"]),
            })
        rows.sort(key=lambda r: (r["days"] is None, -(r["days"] or 0)))
        return rows

    # ------------------------------------------------------------------ scheduler

    async def tick(self):
        """One scheduler pass: daily check, weekly digest, data cleanup -- each at most once per period."""
        now = self.clock()
        ts = int(now.timestamp())
        local = now.astimezone(self.tz)
        today = local.date().isoformat()
        for group in self.settings.groups:
            with self.styles.scope(group):
                await self._tick_group(group, local, today)
        if self.store.get_kv("pruned") != today:
            self.store.set_kv("pruned", today)
            self.store.prune(ts - MESSAGE_RETENTION_DAYS * DAY, ts - ACTION_RETENTION_DAYS * DAY,
                             ts - DEPARTED_RETENTION_DAYS * DAY)

    async def _tick_group(self, group, local, today):
        self.store.ensure_group(group)
        if local.time() >= _clock_time(self.settings.check_time) and self.store.get_kv(f"daily:{group}") != today:
            # Mark first: if the check crashes halfway, members must not be reminded twice.
            self.store.set_kv(f"daily:{group}", today)
            try:
                await self.daily_check(group)
            except Exception as e:
                _log.error(f"[Manager] 每日检查出错: {e}")
        week = "%d-W%02d" % local.isocalendar()[:2]
        if (self.settings.digest_enabled and local.isoweekday() == self.settings.digest_weekday
                and local.time() >= _clock_time(self.settings.digest_time)
                and self.store.get_kv(f"digest:{group}") != week):
            self.store.set_kv(f"digest:{group}", week)
            try:
                await self._send(group, self.build_digest(group))
            except Exception as e:
                _log.error(f"[Manager] 发送每周小报出错: {e}")

    def forget_group(self, group):
        """The bot was removed from a group: delete everything recorded about it."""
        self.store.forget_group(group)
        self.recent.pop(group, None)
        _log.info(f"[Manager] 机器人已被移出群 {group}，已删除该群的全部记录")

    async def run_scheduler(self):
        if not self.enabled:
            _log.info("[Manager] 群管理未开启（其他功能照常）。开启方法：在要管理的群里 @小莲 /群ID，"
                      "把结果填进 config.yaml 的 manager.groups，然后重启。")
            return
        _log.info(f"[Manager] 群管理已启用：{len(self.settings.groups)} 个群，模式 {self._mode_name(self.settings.mode)}")
        while True:
            try:
                await self.tick()
            except Exception as e:
                _log.error(f"[Manager] 定时任务出错: {e}")
            await asyncio.sleep(30)
