"""End-to-end runs of the group manager over simulated weeks, with a fake QQ."""
import itertools
import unittest
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import pytz
from modules.manager.module import ManagerModule
from modules.manager.openapi import OpenAPIError
from modules.manager.store import Store

TZ = pytz.timezone('Asia/Shanghai')
START = TZ.localize(datetime(2026, 11, 1, 10, 0))
STICKER = '<faceType=1,faceId="14",ext="eyJ0ZXh0Ijoi5b6u56yRIn0=">'
GROUP, ADMIN_GROUP = "G", "R"
_ids = itertools.count(1)


@dataclass
class FakeMessage:
    group_openid: str
    member_openid: str
    nickname: str
    raw_content: str
    content: str = ""
    id: str = field(default_factory=lambda: f"msg{next(_ids)}")
    event_type: str = "GROUP_MESSAGE_CREATE"
    is_bot: bool = False
    role: str = None
    message_type: int = None
    attachments_raw: list = field(default_factory=list)
    mentions_bot: bool = False
    mentioned_members: list = field(default_factory=list)


class FakeGateway:
    """Stands in for QQGateway. `member_list` / `remove` say whether the invite-only APIs are granted."""

    def __init__(self, member_list=False, remove=False, members=()):
        self.member_list, self.remove = member_list, remove
        self.members = {m["member_openid"]: m for m in members}
        self.sent = []          # (group, content, reply_to)
        self.buttons = []       # (group, rows of (label, command)) sent under replies
        self.removed = []       # (group, [openids])

    async def send(self, group, content, reply_to=None, buttons=None):
        self.sent.append((group, content, reply_to))
        if buttons:
            self.buttons.append((group, buttons))

    async def probe(self, group):
        return {"member_list": self.member_list, "remove": self.remove}

    async def list_members(self, group):
        if not self.member_list:
            raise OpenAPIError(403, 11253, "应用无接口访问权限")
        return list(self.members.values())

    async def remove_members(self, group, openids):
        if not self.remove:
            raise OpenAPIError(403, 11253, "应用无接口访问权限")
        self.removed.append((group, list(openids)))
        for openid in openids:
            self.members.pop(openid, None)

    def texts(self, group):
        return [content for g, content, _ in self.sent if g == group]


def api_member(openid, nickname, role="member", bot=False):
    return {"member_openid": openid, "username": nickname, "member_role": role, "bot": bot,
            "joined_at": "2026-01-01T08:00:00+08:00"}


class ManagerTestCase(unittest.IsolatedAsyncioTestCase):
    def make(self, gateway, **manager_config):
        config = {"manager": {"groups": [GROUP], "report_group": ADMIN_GROUP, "admins": ["ADMIN"], **manager_config}}
        self.now = START
        self.gw = gateway
        self.store = Store(":memory:")
        self.addCleanup(self.store.db.close)
        self.mgr = ManagerModule(gateway, config, store=self.store, clock=lambda: self.now)
        return self.mgr

    def at(self, day, hour):
        self.now = START.replace(hour=hour) + timedelta(days=day)

    async def post(self, member, text, group=GROUP, command=False, **kwargs):
        message = FakeMessage(group, member, kwargs.pop("nickname", f"小{member}"), text, content=text, **kwargs)
        await self.mgr.observe(message)
        if command:
            await self.mgr.handle_manager(message)
        return message

    async def run_days(self, first, last, daily_posts):
        """For each day: members post at noon, then the 20:00 check runs."""
        for day in range(first, last + 1):
            self.at(day, 12)
            for member, text in daily_posts(day):
                await self.post(member, text)
            self.at(day, 20)
            await self.mgr.daily_check(GROUP)

    def status(self, member):
        return self.store.member(GROUP, member)["status"]


class AutoModeWithFullAccessTest(ManagerTestCase):
    """Whitelisted bot: sees lurkers through the member list and removes by itself."""

    async def test_full_cycle(self):
        members = [api_member(m, f"小{m}") for m in "ABCD"]
        members += [api_member("ADMIN", "管理员", role="admin"), api_member("BOT", "别的机器人", bot=True)]
        self.make(FakeGateway(member_list=True, remove=True, members=members), mode="auto")

        def posts(day):
            yield "A", f"第{day}天：我觉得这个问题可以换个思路解决"
            yield "B", STICKER                  # only ever sends stickers
            # C never says anything; D takes leave below.

        await self.run_days(0, 9, posts)
        self.at(10, 12)
        await self.post("D", "/请假 40 期末考试", command=True)
        self.assertIn("假期到", self.gw.texts(GROUP)[-1])
        await self.run_days(10, 24, posts)
        self.assertFalse(any("@小" in t for t in self.gw.texts(GROUP) if "群规" in t))

        await self.run_days(25, 25, posts)
        reminder = [t for t in self.gw.texts(GROUP) if "@小B" in t]
        self.assertEqual(len(reminder), 1)
        self.assertIn("@小C", reminder[0])
        self.assertNotIn("@小A", reminder[0])
        self.assertNotIn("@小D", reminder[0])           # on leave
        self.assertNotIn("管理员", reminder[0])          # admins don't count
        self.assertEqual(self.store.member(GROUP, "A")["reminded_at"], None)

        await self.run_days(26, 29, posts)
        self.assertEqual(self.gw.removed, [])            # notice period still running
        await self.run_days(30, 30, posts)
        self.assertEqual(len(self.gw.removed), 1)
        self.assertEqual(set(self.gw.removed[0][1]), {"B", "C"})
        self.assertEqual((self.status("B"), self.status("C"), self.status("A")), ("removed", "removed", "active"))
        self.assertTrue(any("已按群规移出 2 人" in t for t in self.gw.texts(ADMIN_GROUP)))

        # The fun parts along the way.
        self.assertTrue(any("七日之约" in t for t in self.gw.texts(GROUP)))
        self.at(30, 21)
        await self.post("A", "/排行", command=True)
        self.assertIn("小A", self.gw.texts(GROUP)[-1])
        await self.post("D", "/我的状态", command=True)
        self.assertIn("请假中", self.gw.texts(GROUP)[-1])

        # B re-applies and comes back: counted afresh, not instantly flagged.
        self.gw.members["B"] = dict(api_member("B", "小B"), joined_at=self.now.isoformat())
        await self.run_days(31, 31, lambda day: [("B", "我回来啦，最近在忙毕业设计的事情")])
        self.assertEqual(self.status("B"), "active")
        await self.run_days(32, 50, lambda day: [])
        self.assertNotIn("B", {m for _, batch in self.gw.removed[1:] for m in batch})


class ConfirmModeWithoutAccessTest(ManagerTestCase):
    """Bot not whitelisted: only people who post are tracked; admins remove by hand."""

    async def test_manual_removal_flow(self):
        self.make(FakeGateway(), mode="confirm")
        await self.run_days(0, 30, lambda day: [("A", f"今天第{day}次聊聊这个新版本的变化"), ("B", STICKER)])
        reminders = [t for t in self.gw.texts(GROUP) if "@小B" in t]
        self.assertEqual(len(reminders), 1)
        manual = [t for t in self.gw.texts(ADMIN_GROUP) if "需要手动移出" in t]
        self.assertEqual(len(manual), 1)
        self.assertIn("小B", manual[0])
        self.assertIn("/已移出", manual[0])

        await self.post("SOMEONE", "/已移出", group=ADMIN_GROUP, command=True)
        self.assertEqual(self.status("B"), "removed")
        self.assertIn("已记为移出", self.gw.texts(ADMIN_GROUP)[-1])

    async def test_reminded_member_who_speaks_up_is_kept(self):
        self.make(FakeGateway(), mode="confirm")
        await self.run_days(0, 26, lambda day: [("B", STICKER)])
        self.assertTrue(self.store.member(GROUP, "B")["reminded_at"])
        self.at(27, 12)
        before = len(self.gw.sent)
        await self.post("B", "抱歉最近太忙了，这周末的活动我也想参加")
        replies = [content for _, content, _ in self.gw.sent[before:]]
        self.assertTrue(any(word in replies[0] for word in ("提醒解除", "提醒就作废")))
        self.assertIn("好久不见", replies[1])         # the comeback badge
        self.assertIsNone(self.store.member(GROUP, "B")["reminded_at"])
        await self.run_days(28, 34, lambda day: [])
        self.assertEqual(self.store.open_batches(GROUP), [])


class ConfirmModeWithRemovalAccessTest(ManagerTestCase):
    async def test_exclude_then_confirm(self):
        self.make(FakeGateway(remove=True), mode="confirm")
        await self.run_days(0, 30, lambda day: [("B", STICKER), ("C", STICKER)] if day == 0 else [])
        pending = [t for t in self.gw.texts(ADMIN_GROUP) if "待确认的移出名单" in t]
        self.assertEqual(len(pending), 1)
        self.assertEqual(self.gw.removed, [])

        await self.post("ADMIN", "/排除 1", group=ADMIN_GROUP, command=True)
        await self.post("ADMIN", "/确认移出", group=ADMIN_GROUP, command=True)
        self.assertEqual(len(self.gw.removed), 1)
        self.assertEqual(len(self.gw.removed[0][1]), 1)
        kept = ({"B", "C"} - set(self.gw.removed[0][1])).pop()
        self.assertEqual(self.status(kept), "active")       # excluded: 7-day grace


class SafetyTest(ManagerTestCase):
    async def test_dry_run_only_reports(self):
        self.make(FakeGateway(), mode="dry_run")
        await self.run_days(0, 31, lambda day: [("B", STICKER)] if day == 0 else [])
        self.assertFalse(any("@小B" in t for t in self.gw.texts(GROUP)))
        self.assertTrue(any("演练模式" in t and "会提醒" in t for t in self.gw.texts(ADMIN_GROUP)))
        self.assertIsNone(self.store.member(GROUP, "B")["reminded_at"])

    async def test_circuit_breaker_stops_mass_removal(self):
        members = [api_member(f"L{i}", f"潜水{i}") for i in range(10)]
        self.make(FakeGateway(member_list=True, remove=True, members=members), mode="auto", max_removals_per_day=2)
        await self.run_days(0, 30, lambda day: [("A", "开个头：这周有人一起研究新版本吗？")] if day == 0 else [])
        self.assertEqual(self.gw.removed, [])
        self.assertTrue(any("超过安全上限" in t for t in self.gw.texts(ADMIN_GROUP)))

    async def test_same_message_from_both_events_counts_once(self):
        self.make(FakeGateway())
        message = FakeMessage(GROUP, "A", "小A", "这个问题我觉得可以用第二种方法解决")
        await self.mgr.observe(message)
        await self.mgr.observe(message)
        self.assertEqual(self.store.counters(GROUP, "A")["valid"], 1)

    async def test_status_card_tells_the_truth(self):
        self.make(FakeGateway())
        await self.post("A", "/我的状态", command=True)
        card = self.gw.texts(GROUP)[-1]
        self.assertIn("还没有记录到", card)
        self.assertNotIn("今天已有效发言", card)
        await self.post("A", "这个问题我觉得可以用第二种方法解决")
        await self.post("A", "/我的状态", command=True)
        card = self.gw.texts(GROUP)[-1]
        self.assertIn("今天已有效发言", card)
        self.assertIn("「这个问题我觉得可以用第二种方法解决」", card)

    async def test_leave_limits(self):
        self.make(FakeGateway())
        await self.post("A", "/请假 100 环游世界", command=True)
        self.assertIn("最长 90 天", self.gw.texts(GROUP)[-1])
        await self.post("A", "/请假", command=True)
        self.assertIn("用法", self.gw.texts(GROUP)[-1])
        await self.post("A", "/豁免 @小B", command=True, mentioned_members=[("B", "小B")])
        self.assertIn("只有管理员", self.gw.texts(GROUP)[-1])

    async def test_nothing_happens_without_every_message_access(self):
        """Owner never granted 接收群内全部消息: only @-messages arrive, so everyone merely looks silent."""
        self.make(FakeGateway(), mode="auto")
        for day in range(0, 40):
            self.at(day, 12)
            await self.post("A", "/我的状态", command=True, event_type="GROUP_AT_MESSAGE_CREATE")
            self.at(day, 20)
            await self.mgr.tick()
        self.assertFalse(any("@小A" in t for t in self.gw.texts(GROUP)))
        warnings = [t for t in self.gw.texts(ADMIN_GROUP) if "接收群内全部消息" in t]
        weeks = {(START + timedelta(days=d)).isocalendar()[:2] for d in range(40)}
        self.assertEqual(len(warnings), len(weeks))       # once a week, not every evening
        self.assertIsNone(self.store.group(GROUP)["tracking_start"])

    async def test_access_switched_off_later_pauses_the_rule(self):
        self.make(FakeGateway(), mode="auto")
        await self.run_days(0, 5, lambda day: [("A", f"第{day}天的讨论：新版本的变化挺大的")])
        for day in range(6, 40):                          # from day 6 only @-messages get through
            self.at(day, 12)
            await self.post("B", "/排行", command=True, event_type="GROUP_AT_MESSAGE_CREATE")
            self.at(day, 20)
            await self.mgr.daily_check(GROUP)
        self.assertFalse(any("@小A" in t for t in self.gw.texts(GROUP)))
        self.assertTrue(any("授权被关掉了" in t for t in self.gw.texts(ADMIN_GROUP)))

    async def test_every_admin_command_runs(self):
        self.make(FakeGateway(remove=True), mode="confirm")
        await self.run_days(0, 30, lambda day: [("B", STICKER)] if day == 0 else [("A", f"第{day}天：今天也来讨论一下")])
        for command in ("/管理帮助", "/管理状态", "/管理模式", "/不活跃", "/不活跃 3", "/豁免名单", "/请假名单",
                        "/待移出", "/小报", "/艾特测试", "/立即检查", "/成就", "/排行 月", "/群规"):
            await self.post("ADMIN", command, command=True)
        await self.post("ADMIN", "/管理模式 确认", command=True)
        self.assertIn("已切换为「确认」模式", self.gw.texts(GROUP)[-1])

        c = [("C", "小C")]
        await self.post("ADMIN", "/豁免 @小C 长期出差", command=True, mentioned_members=c)
        self.assertIn("加入豁免名单", self.gw.texts(GROUP)[-1])
        self.assertEqual(self.store.member(GROUP, "C")["exempt_reason"], "长期出差")
        await self.post("ADMIN", "/取消豁免 @小C", command=True, mentioned_members=c)
        await self.post("ADMIN", "/请假 @小C 10 生病", command=True, mentioned_members=c)
        await self.post("ADMIN", "/撤销请假 @小C", command=True, mentioned_members=c)
        self.assertIn("已撤销", self.gw.texts(GROUP)[-1])
        await self.post("ADMIN", "/请假 @小C 10 生病", command=True, mentioned_members=c)
        leave_id = self.store.active_leaves(GROUP, int(self.now.timestamp()))[0]["id"]
        await self.post("ADMIN", f"/撤销请假 {leave_id}", group=ADMIN_GROUP, command=True)
        self.assertIn("已撤销", self.gw.texts(ADMIN_GROUP)[-1])
        await self.post("ADMIN", "/取消移出", group=ADMIN_GROUP, command=True)
        await self.post("ADMIN", "/删除数据 @小C", command=True, mentioned_members=c)
        self.assertIsNone(self.store.member(GROUP, "C"))

        replies = self.gw.texts(GROUP) + self.gw.texts(ADMIN_GROUP)
        self.assertEqual([r for r in replies if "出错了" in r], [])

    async def test_data_is_deleted_when_promised(self):
        self.make(FakeGateway(), mode="confirm")
        await self.post("A", "这个问题我觉得可以用第二种方法解决")
        await self.post("B", "我也遇到过，后来重装依赖就好了")
        self.store.set_status(GROUP, ["B"], "removed", int(self.now.timestamp()))
        self.at(181, 12)
        await self.mgr.tick()
        self.assertIsNone(self.store.member(GROUP, "B"))        # departed 180+ days ago
        self.assertEqual(self.store.counters(GROUP, "B"), {})
        self.assertIsNotNone(self.store.member(GROUP, "A"))
        self.mgr.forget_group(GROUP)                            # bot removed from the group
        self.assertIsNone(self.store.member(GROUP, "A"))
        self.assertIsNone(self.store.group(GROUP))

    async def test_unmanaged_groups_are_ignored(self):
        self.make(FakeGateway())
        await self.post("A", "这个问题我觉得可以用第二种方法解决", group="OTHER")
        self.assertIsNone(self.store.member("OTHER", "A"))
        await self.post("A", "/我的状态", group="OTHER", command=True)
        self.assertIn("还没有开启群管理", self.gw.texts("OTHER")[-1])


class SchedulerTest(ManagerTestCase):
    async def test_daily_check_and_digest_run_once_per_period(self):
        self.make(FakeGateway(), mode="confirm", check_time="20:00",
                  weekly_digest={"enabled": True, "weekday": 7, "time": "21:00"})
        # 2026-11-01 is a Sunday.
        self.at(0, 12)
        await self.post("A", "大家好，这里是第一次讨论新版本的地方")
        self.at(0, 19)
        await self.mgr.tick()
        self.assertIsNone(self.store.get_kv(f"daily:{GROUP}"))
        self.at(0, 21)
        await self.mgr.tick()
        await self.mgr.tick()
        self.assertEqual(self.store.get_kv(f"daily:{GROUP}"), "2026-11-01")
        digests = [t for t in self.gw.texts(GROUP) if "本周群聊小报" in t]
        self.assertEqual(len(digests), 1)
        self.assertIn("有效发言 1 条", digests[0])


if __name__ == "__main__":
    unittest.main()


class GreetingTest(ManagerTestCase):
    """QQ tells official bots nothing when someone joins, so newcomers are welcomed on their first message."""

    def greetings(self):
        return [t for t in self.gw.texts(GROUP) if "第一次见到" in t or "欢迎新朋友" in t]

    async def test_first_message_is_welcomed_once(self):
        self.make(FakeGateway())
        await self.post("A", "大家好")
        self.assertEqual(self.greetings(), ["第一次见到 小A 说话～欢迎欢迎！发 @我 /帮助 看看我能做什么。"])
        await self.post("A", "今天天气不错")
        self.assertEqual(len(self.greetings()), 1)

    async def test_after_a_full_month_strangers_are_newcomers(self):
        self.make(FakeGateway())
        await self.post("A", "大家好")
        self.at(31, 12)
        await self.post("B", "我是新来的")
        self.assertTrue(self.greetings()[-1].startswith("欢迎新朋友 小B～我是小莲"))

    async def test_no_welcome_for_admins_without_full_access_or_when_off(self):
        self.make(FakeGateway())
        await self.post("C", "@机器人 你好", event_type="GROUP_AT_MESSAGE_CREATE", mentions_bot=True)
        self.assertEqual(self.greetings(), [])          # @-messages alone: everyone's first message looks new
        await self.post("ADMIN", "测试一下")
        self.assertEqual(self.greetings(), [])
        self.make(FakeGateway(), greet_newcomers=False)
        await self.post("A", "大家好")
        self.assertEqual(self.greetings(), [])
