"""The group's inactivity rule as pure functions, so it can be tested with any clock.

连续 30 天无有效发言、且未请假/未豁免 → 移出；第 25 天 @ 提醒一次；移出前不另行警告。

Days are counted in calendar days (Asia/Shanghai) from an "anchor": the latest
moment the member's clock was reset -- a valid message, joining or rejoining,
the end of a leave, or the day tracking started.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Policy:
    remind_day: int = 25
    remove_day: int = 30
    # Minimum days between the @ reminder and removal. If the bot was down on
    # someone's day 25, removal waits until they've had this much warning.
    min_notice_days: int = 5
    exempt_admins: bool = True


@dataclass
class MemberSnapshot:
    member_openid: str
    nickname: str
    first_seen: int                      # epoch seconds
    joined_at: Optional[int] = None      # known only from the member-list API
    rejoined_at: Optional[int] = None
    last_valid_at: Optional[int] = None
    reminded_at: Optional[int] = None
    exempt: bool = False
    is_admin: bool = False
    is_bot: bool = False
    status: str = "active"
    leave_start: Optional[int] = None    # latest leave
    leave_end: Optional[int] = None      # its effective end (early return included)


@dataclass
class Verdict:
    action: str          # ok | remind | remove | exempt | admin | leave | bot | gone
    days: int = 0        # calendar days without valid speech
    anchor: int = 0
    days_left: int = 0   # days left to post before removal becomes possible
    reminded: bool = False


def day_diff(earlier_ts, now, tz):
    """Calendar days between an epoch timestamp and `now`, in the bot's timezone."""
    earlier = datetime.fromtimestamp(earlier_ts, tz).date()
    return (now.astimezone(tz).date() - earlier).days


def on_leave(m, now_ts):
    return m.leave_start is not None and m.leave_start <= now_ts < (m.leave_end or 0)


def anchor_of(m, tracking_start, now_ts):
    # Without the member-list API we don't know when someone joined; the first
    # time we saw them is the fair stand-in, or a newcomer who only said "大家好"
    # would be judged as silent since tracking began.
    joined = m.joined_at if m.joined_at is not None else m.first_seen
    candidates = [tracking_start, joined, m.last_valid_at, m.rejoined_at]
    if m.leave_end is not None and m.leave_end <= now_ts:
        candidates.append(m.leave_end)
    return max(c for c in candidates if c is not None)


def evaluate(m, now, policy, tracking_start, tz):
    now_ts = int(now.timestamp())
    if m.status != "active":
        return Verdict("gone")
    if m.is_bot:
        return Verdict("bot")
    anchor = anchor_of(m, tracking_start, now_ts)
    days = max(day_diff(anchor, now, tz), 0)
    left = max(policy.remove_day - days, 0)
    if m.exempt:
        return Verdict("exempt", days, anchor, left)
    if m.is_admin and policy.exempt_admins:
        return Verdict("admin", days, anchor, left)
    if on_leave(m, now_ts):
        return Verdict("leave", days, anchor, left)

    # A reminder only counts if it came after the clock's last reset.
    reminded = m.reminded_at is not None and m.reminded_at >= anchor
    if reminded:
        since_reminder = day_diff(m.reminded_at, now, tz)
        left = max(policy.remove_day - days, policy.min_notice_days - since_reminder, 0)
        if days >= policy.remove_day and since_reminder >= policy.min_notice_days:
            return Verdict("remove", days, anchor, 0, reminded=True)
        return Verdict("ok", days, anchor, left, reminded=True)
    if days >= policy.remind_day:
        return Verdict("remind", days, anchor, max(policy.remove_day - days, policy.min_notice_days))
    return Verdict("ok", days, anchor, left)
