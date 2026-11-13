import os
import tempfile
import unittest
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest import mock
from core.style import ADMIN, StyleBook
from modules.fun.sleep import SleepModule
from modules.manager import achievements
from modules.manager.achievements import Badge, counter_increments, load_badges, next_goal
from modules.manager.classifier import judge
from modules.manager.module import ManagerModule
from modules.manager.store import Store
from tests.test_manager import GROUP, TZ, FakeGateway, ManagerTestCase

HIDDEN = {"birthday", "new_year", "spring_festival", "mid_autumn"}


class BadgeFileTest(unittest.TestCase):
    def load(self, text):
        fd, path = tempfile.mkstemp(suffix=".yaml")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        self.addCleanup(os.remove, path)
        return load_badges(path)

    def test_shipped_file_loads(self):
        codes = [b.code for b in achievements.BADGES]
        self.assertEqual(len(codes), 35)
        self.assertEqual(len(set(codes)), 35)
        self.assertFalse(achievements.BY_CODE["first_words"].announce)
        self.assertEqual(achievements.BY_CODE["streak_7"].streak, 7)
        self.assertEqual({b.code for b in achievements.BADGES if b.hidden}, HIDDEN)
        festival, autumn = achievements.BY_CODE["spring_festival"], achievements.BY_CODE["mid_autumn"]
        for day, met in ((date(2027, 2, 5), True), (date(2027, 2, 6), True), (date(2027, 2, 7), False),
                         (date(2030, 2, 2), True), (date(2030, 2, 3), True), (date(2035, 2, 8), True)):
            self.assertEqual(festival.met({}, 0, False, today=day), met, day)           # 除夕、正月初一, every year
        self.assertTrue(autumn.met({}, 0, False, today=date(2027, 9, 15)))                # 八月十五
        self.assertFalse(autumn.met({}, 0, False, today=date(2027, 9, 16)))

    def test_commented_examples_work_once_uncommented(self):
        with open(achievements.BADGES_FILE, encoding="utf-8") as f:
            head, _, examples = f.read().partition("# 下面是可以直接启用的例子")
        uncommented = [line[2:] if line.startswith("# ") else line.lstrip("#")
                       for line in examples.splitlines()[1:]]
        badges = self.load(head + "\n" + "\n".join(uncommented))
        self.assertEqual([b.code for b in badges[-3:]], ["chatter_10000", "streak_365", "dragon_boat"])

    def test_a_new_badge_needs_only_yaml(self):
        badges = self.load("- {code: weekend_warrior, icon: x, name: 周末战士, desc: 周末 50 次,"
                           " counter: weekend, at_least: 50}")
        self.assertTrue(badges[0].met({"weekend": 50}, streak=0, comeback=False))
        self.assertFalse(badges[0].met({"weekend": 49}, streak=0, comeback=False))
        self.assertFalse(badges[0].hidden)

    def test_days_of_the_year(self):
        badge, secret = self.load('- {code: a, icon: x, name: n, desc: d, dates: ["01-01", 2027-02-06]}\n'
                                  '- {code: b, icon: x, name: n, desc: d, hidden: true, dates: "02-29"}')
        self.assertEqual(badge.dates, ("01-01", "2027-02-06"))       # YAML's own date reads the same
        self.assertTrue(badge.met({}, 0, False, today=date(2031, 1, 1)))
        self.assertTrue(badge.met({}, 0, False, today=date(2027, 2, 6)))
        self.assertFalse(badge.met({}, 0, False, today=date(2028, 2, 6)))
        self.assertFalse(badge.met({"valid": 999}, 99, True))                # not a valid message on the day
        self.assertTrue(secret.hidden)
        self.assertTrue(secret.met({}, 0, False, today=date(2028, 2, 29)))

    def test_mistakes_are_caught_at_startup(self):
        mistakes = {
            "缺少": "- {code: a, icon: x, name: n, counter: valid, at_least: 1}",
            "重复": "- {code: a, icon: x, name: n, desc: d, counter: valid, at_least: 1}\n"
                    "- {code: a, icon: x, name: n, desc: d, streak: 3}",
            "没有叫": "- {code: a, icon: x, name: n, desc: d, counter: vallid, at_least: 1}",
            "只能写其中一种": "- {code: a, icon: x, name: n, desc: d, counter: valid, at_least: 1, streak: 3}",
            "正整数": "- {code: a, icon: x, name: n, desc: d, counter: valid}",
            "「02-30」不是日期": "- {code: a, icon: x, name: n, desc: d, dates: [01-01, 02-30]}",
            "「1-1」不是日期": "- {code: a, icon: x, name: n, desc: d, dates: [1-1]}",
            "其中一种": "- {code: a, icon: x, name: n, desc: d, dates: [01-01], comeback: true}",
            "icon@captains 用不了": "- {code: a, icon: x, icon@captains: y, name: n, desc: d, streak: 3}",
            "name@ 用不了": "- {code: a, icon: x, name: n, 'name@': m, desc: d, streak: 3}",
            "name@captains 是空的": "- {code: a, icon: x, name: n, name@captains: '', desc: d, streak: 3}",
            "name@a.b 用不了": "- {code: a, icon: x, name: n, name@a.b: m, desc: d, streak: 3}",
            "name@a b 用不了": "- {code: a, icon: x, name: n, name@a b: m, desc: d, streak: 3}",
        }
        for expected, text in mistakes.items():
            with self.assertRaises(ValueError) as caught:
                self.load(text)
            self.assertIn(expected, str(caught.exception))

    def test_a_badge_can_read_differently_for_particular_people(self):
        badge, = self.load("- {code: a, icon: x, name: 新人, name@captains: 新队长, desc: 说明, desc@vip: 贵宾的说明,"
                           " counter: valid, at_least: 1}")
        twice, = self.load("- {code: b, icon: x, name: 新人, name@captains: 新队长, name@vip: 新贵宾, desc: d, streak: 3}")
        self.assertEqual(twice.as_for(["vip", "captains"]).name, "新贵宾")      # most particular first
        self.assertEqual(twice.as_for(["captains", "vip"]).name, "新队长")
        self.assertEqual((badge.name, badge.desc), ("新人", "说明"))
        self.assertIs(badge.as_for([]), badge)
        self.assertIs(badge.as_for([ADMIN]), badge)
        captain = badge.as_for(["captains"])
        self.assertEqual((captain.name, captain.desc), ("新队长", "说明"))
        both = badge.as_for(["vip", "captains"])
        self.assertEqual((both.name, both.desc), ("新队长", "贵宾的说明"))
        self.assertTrue(both.met({"valid": 1}, streak=0, comeback=False))

    def test_versions_for_nobody_are_named(self):
        badges = self.load("- {code: a, icon: x, name: n, name@captains: m, desc@typo: t, desc: d, streak: 3}")
        with mock.patch.object(achievements, "BADGES", badges):
            problems = achievements.audience_problems({ADMIN, "captains"})
        self.assertEqual(len(problems), 1)
        self.assertIn("desc@typo", problems[0])


class CounterTest(unittest.TestCase):
    def test_new_counters(self):
        saturday_night = datetime(2026, 10, 24, 2, 30)
        long_text = judge("这一段是很长的分享，" * 6)
        bumps = counter_increments(long_text, saturday_night, first_of_day=True)
        self.assertEqual((bumps["weekend"], bumps["long"], bumps["days"], bumps["night"]), (1, 1, 1, 1))
        monday_noon = datetime(2026, 10, 26, 12, 0)
        short_text = judge("这个问题我觉得可以用第二种方法解决")
        bumps = counter_increments(short_text, monday_noon, first_of_day=False)
        self.assertEqual((bumps["weekend"], bumps["long"], bumps["days"], bumps["night"]), (0, 0, 0, 0))
        self.assertEqual(bumps["opener"], 0)
        self.assertEqual(counter_increments(short_text, monday_noon, False, opener=True)["opener"], 1)

    def test_streak_and_comeback_badges(self):
        streak = Badge("s", "x", "n", "d", streak=7)
        self.assertTrue(streak.met({}, streak=7, comeback=False))
        back = Badge("c", "x", "n", "d", comeback=True)
        self.assertTrue(back.met({}, streak=0, comeback=True))
        self.assertFalse(back.met({"valid": 999}, streak=99, comeback=False))

    def test_the_next_goal_is_the_closest_one(self):
        badge, progress = next_goal({"first_words"}, {"valid": 1, "days": 1}, streak=1)
        self.assertEqual((badge.code, progress), ("streak_7", (1, 7)))
        badge, progress = next_goal({"first_words"}, {"valid": 95, "days": 3}, streak=1)
        self.assertEqual((badge.code, progress), ("chatter_100", (95, 100)))
        counted = {b.code for b in achievements.BADGES if b.counter or b.streak} - HIDDEN
        self.assertIsNone(next_goal(counted, {}, streak=0))       # left: hidden ones, a comeback, holidays


class BadgesInTheGroupTest(ManagerTestCase):
    async def badges(self, member, command="/成就"):
        await self.post(member, command, command=True)
        return self.gw.texts(GROUP)[-1]

    async def test_the_first_valid_message_after_five_opens_the_day(self):
        self.make(FakeGateway())
        for day in range(10):
            self.at(day, 3)
            await self.post("A", f"第{day}天还没睡，在看比赛的回放")     # before five: doesn't open the day
            self.at(day, 7)
            await self.post("C", "早")                                    # not a valid message
            await self.post("B", f"第{day}天早上好，今天的天气看起来不错")
            await self.post("A", f"第{day}天我也醒了，昨晚的比赛太精彩了")
        self.assertEqual(self.store.counters(GROUP, "B")["opener"], 10)
        self.assertNotIn("opener", self.store.counters(GROUP, "A"))
        self.assertTrue(any("小B" in text and "开门红" in text for text in self.gw.texts(GROUP)))

    async def test_the_reply_is_short_with_everything_a_tap_away(self):
        self.make(FakeGateway())
        await self.post("A", "大家好，我是新来的，请多关照")
        await self.post("B", "欢迎欢迎，有什么不懂的可以问我们")
        self.assertEqual((await self.badges("A")).split("\n"), [
            "🎖️ 小A 的成就（1/35）",
            "已解锁：🌱初次见面",
            "最新：🌱 初次见面 —— 第一次有效发言（11月1日 · 全群 2 人）",
            "还有 34 个没解锁（隐藏 4 个），/成就 全部 看完整列表",
            "下一个目标：🔥七日之约 1/7"])
        self.assertEqual(self.gw.buttons[-1][1][0][0], ("全部成就", "/成就 全部"))

        text = await self.badges("A", "/成就 全部")
        self.assertIn("🌱 初次见面 —— 第一次有效发言（11月1日 · 全群 2 人）\n还没解锁：话匣子、", text)
        self.assertIn("还有 4 个隐藏成就", text)
        self.assertNotIn("元旦快乐", text)
        self.assertIn("下一个目标：🔥七日之约 1/7", text)
        self.assertNotIn("全部成就", [label for label, *_ in self.gw.buttons[-1][1][0]])

        text = await self.badges("C")                                     # no badges yet
        self.assertEqual(text.split("\n")[1], "还有 35 个没解锁（隐藏 4 个），/成就 全部 看完整列表")

    async def test_it_stays_in_one_message_with_every_badge(self):
        self.make(FakeGateway())
        await self.post("A", "大家好，我是新来的，请多关照")
        for badge in achievements.BADGES:
            self.store.award(GROUP, "A", badge.code, int(self.now.timestamp()))
        before = len(self.gw.sent)
        text = await self.badges("A")
        self.assertEqual(len(self.gw.sent), before + 1)
        self.assertIn("🎖️ 小A 的成就（35/35）", text)
        self.assertIn("全部解锁啦", text)
        self.assertNotIn("个没解锁", text)
        await self.badges("A", "/成就 全部")
        self.assertGreater(len(self.gw.sent), before + 2)                 # the full list runs to two

    async def test_holiday_badges_come_on_their_day(self):
        self.make(FakeGateway())
        self.now = TZ.localize(datetime(2026, 12, 31, 23, 50))
        await self.post("A", "还有十分钟就到新的一年了，好期待")
        self.now += timedelta(minutes=20)
        await self.post("B", "新年快乐！祝大家新的一年一切顺利")
        texts = self.gw.texts(GROUP)
        self.assertFalse(any("小A" in text and "元旦快乐" in text for text in texts))
        self.assertTrue(any("小B" in text and "🎆元旦快乐" in text for text in texts))
        text = await self.badges("B")
        self.assertIn("最新：🎆 元旦快乐 —— 元旦当天来群里聊天（1月1日 · 全群 1 人）", text)
        self.assertIn("隐藏 3 个", text)

    async def test_other_features_count_toward_badges(self):
        self.make(FakeGateway())
        await self.post("A", "大家好，我是新来的，请多关照")
        for _ in range(4):
            self.assertEqual(self.mgr.count(GROUP, "A", "小A", "vote"), [])
        congratulations = self.mgr.count(GROUP, "A", "小A", "vote")
        self.assertEqual(len(congratulations), 1)
        self.assertIn("🗳️积极投票", congratulations[0])
        self.assertEqual(self.mgr.count("ELSEWHERE", "A", "小A", "vote"), [])    # a group it doesn't manage
        self.assertNotIn("vote", self.store.counters("ELSEWHERE", "A"))
        self.assertIn("🍲喝到汤底", self.mgr.count(GROUP, "A", "小A", "soup")[0])  # the first 海龟汤 they solve
        for _ in range(3):
            self.assertEqual(self.mgr.count(GROUP, "A", "小A", "soup"), [])
        self.assertIn("🕵️名侦探", self.mgr.count(GROUP, "A", "小A", "soup")[0])


class BadgesForParticularPeopleTest(ManagerTestCase):
    """A badge reads the way its holder's versions say, whoever is reading."""

    def setUp(self):
        self.make(FakeGateway())
        book = StyleBook()
        book.configure({"audiences": {"captains": ["A"]}})
        self.mgr.styles = book
        badges = (Badge("first", "🌱", "新人", "第一次有效发言", counter="valid", at_least=1,
                        versions={"name@captains": "新队长", "desc@captains": "队长的第一句"}),
                  Badge("more", "💬", "话匣子", "5 条有效发言", counter="valid", at_least=5,
                        versions={"name@captains": "话多队长"}),
                  Badge("quiet", "🤫", "悄悄话", "不祝贺的成就", counter="valid", at_least=1, announce=False,
                        versions={"name@captains": "悄悄队长"}))
        for name, value in (("BADGES", badges), ("BY_CODE", {b.code: b for b in badges})):
            patcher = mock.patch.object(achievements, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def badges(self, member):
        await self.post(member, "/成就", command=True)
        return self.gw.texts(GROUP)[-1]

    async def test_the_holder_decides(self):
        self.assertEqual(self.mgr.audiences_of(GROUP, "A"), ["captains"])
        self.assertEqual(self.mgr.audiences_of(GROUP, "ADMIN"), [ADMIN])
        await self.post("A", "大家好，我是新来的队长，请多关照")
        await self.post("B", "欢迎欢迎，有什么不懂的可以问我们")
        texts = self.gw.texts(GROUP)
        self.assertTrue(any("🌱新队长" in text and "队长的第一句" in text for text in texts))
        self.assertTrue(any("🌱新人" in text and "第一次有效发言" in text for text in texts))
        self.assertFalse(any("小B" in text and "新队长" in text for text in texts))

        mine = await self.badges("A")
        self.assertIn("新队长", mine)
        self.assertIn("下一个目标：💬话多队长 1/5", mine)
        self.assertNotIn("新人", mine)
        theirs = await self.badges("B")
        self.assertIn("新人", theirs)
        self.assertIn("下一个目标：💬话匣子 1/5", theirs)

        digest = self.mgr.build_digest(GROUP)       # one post for everyone: each badge as its holder has it
        self.assertIn("新队长", digest)
        self.assertIn("新人", digest)
        self.assertNotIn("悄悄", digest)             # not congratulated, so not in the digest either

    def test_a_version_for_nobody_is_named_at_startup(self):
        store = Store(":memory:")
        self.addCleanup(store.db.close)
        book = StyleBook()
        book.configure({})
        with mock.patch("modules.manager.module._log") as log:
            ManagerModule(FakeGateway(), {}, store=store, styles=book)
        self.assertIn("name@captains", str(log.warning.call_args_list))
        with mock.patch("modules.manager.module._log") as log:
            ManagerModule(FakeGateway(), {}, store=store, styles=StyleBook())     # not configured: no false alarm
        log.warning.assert_not_called()


class SleepBadgeTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_good_night_counts(self):
        counted, replies = [], []
        now = [TZ.localize(datetime(2026, 11, 1, 23, 0))]
        sleep = SleepModule(None, clock=lambda: now[0],
                            count=lambda group, member, nickname, key: counted.append((member, key)) or ["🎉"])

        async def reply(content):
            replies.append(content)

        async def say(text, hours_later):
            now[0] += timedelta(hours=hours_later)
            await sleep.handle_sleep(SimpleNamespace(content=text, group_openid=GROUP, nickname="小A",
                                                     author=SimpleNamespace(member_openid="A"), reply=reply))

        await say("/晚安", 0)
        await say("/早安", 7.5)
        self.assertEqual(counted, [("A", "sleep")])
        self.assertTrue(replies[-1].endswith("\n🎉"))
        await say("/晚安", 15)
        await say("/早安", 5)                              # too short
        await say("/晚安", 10)
        await say("/早安", 9.5)                            # too long
        self.assertEqual(counted, [("A", "sleep")])


if __name__ == "__main__":
    unittest.main()
