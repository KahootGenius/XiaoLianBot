"""Special days (prompts/occasions.yaml): the lunar calendar, the file, and what each feature does on one."""
import io
import os
import shutil
import tempfile
import unittest
from datetime import date, timedelta
from core import lunar, occasions
from core.config import root_path
from core.occasions import Calendar, Occasion, parse_when
from core.style import STYLES, StyleBook, t
from modules.fun.teams.history import History
from tests.test_ai_chat import AIChatTestCase, G
from tests.test_manager import GROUP, START, FakeMessage
from tests.test_social import NAMES, SocialTestCase
from tests.test_teams import Dice, TeamGameTestCase

NEW_YEAR = Occasion("元旦", (parse_when("01-01"),), greet=("🎆 {member} 是今年第 {n} 个，{day}快乐！",), first=2, die=1,
                    chat="新的一年开始了。")


class LunarTest(unittest.TestCase):
    def test_festivals_fall_where_the_published_calendar_puts_them(self):
        for day, expected in {"2026-02-17": (1, 1), "2027-02-06": (1, 1), "2030-02-03": (1, 1), "2033-01-31": (1, 1),
                              "2026-03-03": (1, 15), "2026-06-19": (5, 5), "2026-08-19": (7, 7), "2026-09-25": (8, 15),
                              "2027-09-15": (8, 15), "2028-10-03": (8, 15), "2027-02-05": (12, 29),
                              "2026-02-16": (12, 29), "2028-01-25": (12, 29), "2025-01-29": (1, 1)}.items():
            self.assertEqual(lunar.lunar(date.fromisoformat(day)), (*expected, False), day)
        self.assertEqual(lunar.lunar(date(2025, 7, 25)), (6, 1, True))          # 闰六月
        self.assertEqual(lunar.lunar(date(2033, 12, 22)), (11, 1, True))        # 闰冬月, the one tables get wrong
        self.assertTrue(lunar.is_eve(date(2027, 2, 5)))
        self.assertFalse(lunar.is_eve(date(2027, 2, 6)))

    def test_the_table_is_whole_and_ends_where_it_says(self):
        months = lunar._MONTHS
        for (start, month, leap), (following, next_month, next_leap) in zip(months, months[1:]):
            self.assertIn((following - start).days, (29, 30), start)
            self.assertTrue((next_leap and next_month == month and not leap)
                            or (not next_leap and next_month == month % 12 + 1), start)
        self.assertEqual(sum(1 for _, _, leap in months if leap), 6)
        self.assertEqual((lunar.FIRST, lunar.LAST), (date(2025, 1, 29), date(2042, 1, 20)))
        self.assertIsNone(lunar.lunar(lunar.FIRST - timedelta(days=1)))
        self.assertIsNone(lunar.lunar(lunar.LAST + timedelta(days=1)))
        self.assertEqual(lunar.lunar(lunar.LAST), (12, 29, False))


class WhenTest(unittest.TestCase):
    def test_the_ways_to_say_which_day(self):
        on = lambda text, day: parse_when(text).falls_on(date.fromisoformat(day))
        self.assertTrue(on("01-01", "2031-01-01") and not on("01-01", "2031-01-02"))
        self.assertTrue(on("02-29", "2028-02-29") and not on("02-29", "2027-02-28"))
        self.assertTrue(on("2027-02-06", "2027-02-06") and not on("2027-02-06", "2028-02-06"))
        self.assertTrue(parse_when(date(2027, 2, 6)).falls_on(date(2027, 2, 6)))        # as YAML reads it unquoted
        for text in ("农历 正月初一", "农历正月初一", "农历 一月初一", "农历 1-1"):
            self.assertTrue(on(text, "2027-02-06") and on(text, "2030-02-03") and not on(text, "2027-02-07"), text)
        self.assertTrue(on("农历 八月十五", "2026-09-25") and on("农历 腊月廿三", "2027-01-30"))
        self.assertTrue(on("农历 十二月二十三", "2027-01-30") and on("农历 冬月初一", "2026-12-09"))
        self.assertTrue(on("农历 除夕", "2027-02-05") and on("农历 除夕", "2026-02-16") and not on("农历 除夕", "2027-02-06"))
        self.assertTrue(on("农历 六月初一", "2025-06-25") and not on("农历 六月初一", "2025-07-25"))     # not in the leap month
        self.assertFalse(on("农历 正月初一", "2050-01-23"))                                           # past the table
        for text in ("1-1", "13-01", "02-30", "2027-13-01", "农历 十三月初一", "农历 正月三十一", "农历 0-1", "明天", ""):
            with self.assertRaises(ValueError, msg=text) as caught:
                parse_when(text)
            self.assertIn("不是日期", str(caught.exception))

    def test_a_day_can_last_and_knows_when_it_comes_next(self):
        festival = Occasion("春节", (parse_when("农历 正月初一"),), lasts=3)
        self.assertEqual([festival.on(date(2027, 2, day)) for day in (5, 6, 7, 8, 9)], [False, True, True, True, False])
        self.assertEqual(festival.next_from(date(2026, 11, 13)), date(2027, 2, 6))
        self.assertEqual(festival.next_from(date(2027, 2, 7)), date(2027, 2, 7))
        self.assertIsNone(Occasion("一次", (parse_when("2020-01-01"),)).next_from(date(2026, 1, 1)))
        both = Occasion("圣诞", (parse_when("12-24"), parse_when("12-25")))
        self.assertTrue(both.on(date(2026, 12, 24)) and both.on(date(2026, 12, 25)) and not both.on(date(2026, 12, 26)))


class FileTest(unittest.TestCase):
    def load(self, text):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder)
        path = os.path.join(folder, "occasions.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path, occasions.load(path)

    def test_the_shipped_days_load(self):
        days, problems = occasions.load(root_path("prompts", "occasions.yaml"))
        self.assertEqual(problems, [])
        by_name = {day.name: day for day in days}
        self.assertEqual(list(by_name), ["元旦", "除夕", "春节", "元宵", "愚人节", "端午", "七夕", "中秋", "万圣夜", "圣诞", "跨年夜"])
        self.assertEqual((by_name["春节"].lasts, by_name["春节"].first, by_name["春节"].die, by_name["七夕"].greet), (3, 8, 1, ()))
        self.assertEqual((by_name["除夕"].first, by_name["除夕"].lasts, by_name["除夕"].die), (10, 1, 0))     # what a day gets unasked
        self.assertEqual(by_name["除夕"].next_from(date(2026, 11, 13)), date(2027, 2, 5))
        # ...and every day the shipped style has a version for is one of them
        named = {key.rpartition("@")[2] for key, _ in __import__("core.style", fromlist=["_leaves"])._leaves(
            STYLES.shared_base.data) if "@" in key.rpartition(".")[2]}
        self.assertLessEqual(named - {"admin"}, set(by_name))

    def test_a_day_written_wrong_is_left_out_and_named(self):
        _, (days, problems) = self.load("""days:
  - name: 好日子
    when: [06-01, 农历 五月初五]
    greet: ["{member} 第 {n} 个", "{day}好"]
    first: 0
    lasts: 2
  - when: 01-01
  - name: 没写哪天
  - name: 写成了on
    on: 01-01
  - name: 不是日期
    when: 一月一号
  - name: 好日子
    when: 01-02
  - name: 带@的
    when: 01-03
  - name: admin
    when: 01-03
  - name: 花括号
    when: 01-04
    greet: "{who} 你好"
  - name: 骰子
    when: 01-05
    die: 6
  - name: 太久
    when: 01-06
    lasts: 0
""")
        self.assertEqual([(d.name, len(d.when), d.first, d.lasts, len(d.greet)) for d in days], [("好日子", 2, 0, 2, 2)])
        for problem, phrase in zip(problems, ["第 2 个日子缺少 name", "（没写哪天）：when 要写是哪一天", "（写成了on）：when 要写是哪一天",
                                              "（不是日期）：when 里的「一月一号」不是日期", "（好日子）：名字和前面的重复了",
                                              "（带@的）：名字里不能有", "（admin）：名字里不能有", "greet 里的 {who}",
                                              "（骰子）：die 要写 0 到 5", "（太久）：lasts 要写 1 到 31"]):
            self.assertIn(phrase, problem)
        self.assertEqual(len(problems), 10)

    def test_no_file_or_an_unreadable_one_means_no_special_days(self):
        self.assertEqual(occasions.load("/nonexistent/occasions.yaml"), ([], []))
        self.assertEqual(self.load("")[1], ([], []))
        self.assertEqual(self.load("days: 元旦")[1][1], ["occasions.yaml 的 days 要写成列表，每个日子以「- name:」开头"])
        days, problems = self.load("days: [")[1]
        self.assertEqual((days, len(problems)), ([], 1))

    def test_the_command_lists_when_each_comes_next(self):
        out = io.StringIO()
        self.assertEqual(occasions.main(root_path("prompts", "occasions.yaml"), out, today=date(2026, 12, 31)), 0)
        said = out.getvalue()
        self.assertIn("跨年夜  下一次：就是今天  聊天\n", said)
        self.assertIn("春节  下一次：2027-02-06，连续 3 天  问候 · 聊天 · 骰子+1\n", said)
        self.assertIn("11 个日子，0 个问题。", said)
        path, _ = self.load("days:\n  - name: 坏的\n    when: 13-13\n")
        out = io.StringIO()
        self.assertEqual(occasions.main(path, out), 1)
        self.assertIn("!! occasions.yaml 第 1 个日子（坏的）", out.getvalue())


class CalendarTest(unittest.TestCase):
    def setUp(self):
        self.now = START.replace(month=12, day=31, hour=23, minute=59)
        self.calendar = Calendar([NEW_YEAR, Occasion("跨年夜", (parse_when("12-31"),)),
                                  Occasion("春节", (parse_when("农历 正月初一"),))], clock=lambda: self.now)

    def test_today_follows_the_beijing_date(self):
        self.assertEqual(self.calendar.names(), ("跨年夜",))
        self.now += timedelta(minutes=1)
        self.assertEqual((self.calendar.names(), self.calendar.today()[0].die), (("元旦",), 1))
        self.now += timedelta(days=1)
        self.assertEqual(self.calendar.today(), ())

    def test_the_log_says_when_the_lunar_table_is_running_out(self):
        self.assertIsNone(self.calendar.running_out())
        self.now = self.now.replace(year=2041)
        self.assertIn("2042-01-20", self.calendar.running_out())
        self.assertIsNone(Calendar([NEW_YEAR], clock=lambda: self.now).running_out())       # nothing lunar to miss

    def test_a_style_speaks_the_days_version_of_a_line(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder)
        with open(os.path.join(folder, "小莲.yaml"), "w", encoding="utf-8") as f:
            f.write("hello: 你好\nhello@元旦: 新年好\nhello@admin: 管理员好\nbye: 再见\nbye@admin: 管理员再见\n"
                    "bye@生日: 没有这个日子\nlevels: [吉, 凶]\nlevels@元旦: [大吉]\n")
        book = StyleBook(directory=folder, local_directory=folder)
        self.calendar.install(book)
        book.configured = True
        self.assertEqual(book._audience_problems("小莲"), [book._audience_problems("小莲")[0]])      # only 生日 is unknown
        self.assertIn("bye@生日：没有叫「生日」的群友类别或特别的日子", book._audience_problems("小莲")[0])
        with book.scope(GROUP, audiences=["admin"]):                # an ordinary day
            self.assertEqual((t("hello"), book.default().pick("levels")), ("管理员好", ["吉", "凶"]))
        self.now += timedelta(minutes=1)                            # 元旦: the day's version first, then the person's
        with book.scope(GROUP, audiences=["admin"]):
            self.assertEqual((t("hello"), t("bye"), book.default().pick("levels")), ("新年好", "管理员再见", ["大吉"]))
        with book.scope(GROUP):
            self.assertEqual((t("hello"), t("bye")), ("新年好", "再见"))


class FortuneOnTheDayTest(SocialTestCase):
    async def test_everyone_draws_the_same_on_new_years_day_and_at_spring_festival(self):
        self.make()
        usual = {(await self.say(member, "/今日运势")).split("\n")[1] for member in "ABCDEFGHIJKLMNOP"}
        self.assertGreater(len(usual), 2)
        for day, title, comment in (("元旦", "🎆 小明 的新年第一签 · 11月1日", "新的一年，小明 开门就是大吉！小莲也沾沾喜气～"),
                                    ("春节", "🧧 小明 的新春运势 · 11月1日", "过年啦，小明 抽到的是大吉，新的一年顺顺利利～")):
            with STYLES.scope(GROUP, audiences=[day]):
                drawn = [await self.say(member, "/今日运势") for member in "ABCDEFGHIJKLMNOP"]
            self.assertEqual({reply.split("\n")[1] for reply in drawn}, {"运势：大吉"})
            self.assertEqual(drawn[0].split("\n")[0], title)
            self.assertEqual(drawn[0].split("\n")[-1], comment)      # the day's own comment


class GreetingTest(SocialTestCase):
    def make_on(self, *days):
        self.make()
        self.social.days = lambda: list(days)

    async def chat(self, member, text="早", **kwargs):
        message = FakeMessage(GROUP, member, NAMES.get(member, member), text, content=text, **kwargs)
        before = len(self.gw.sent)
        await self.social.observe(message)
        self.assertTrue(all(replied == message.id for _, _, replied in self.gw.sent[before:]))
        return "\n".join(content for _, content, _ in self.gw.sent[before:])

    async def test_the_first_few_to_speak_get_the_days_line_once(self):
        self.make_on(NEW_YEAR)
        self.assertEqual(await self.chat("A"), "🎆 小明 是今年第 1 个，元旦快乐！")
        self.assertEqual(await self.chat("A"), "")                              # once each
        self.assertEqual(await self.chat("B"), "🎆 小红 是今年第 2 个，元旦快乐！")
        self.assertEqual(await self.chat("C"), "")                              # the day's `first` is 2
        self.assertEqual(self.store.daily(GROUP, "C", "day:元旦"), (None, None))  # and nothing is kept of the rest
        self.assertEqual(await self.chat("BOT", is_bot=True), "")
        self.now += timedelta(days=1)                                           # a day that lasts greets again
        self.assertEqual(await self.chat("C"), "🎆 小刚 是今年第 1 个，元旦快乐！")
        self.social.days = lambda: []
        self.assertEqual(await self.chat("B"), "")                              # an ordinary day

    async def test_first_zero_greets_everyone_and_a_day_without_a_line_nobody(self):
        everyone = Occasion("中秋", (parse_when("农历 八月十五"),), greet=("{member} 中秋快乐",), first=0)
        self.make_on(Occasion("除夕", (parse_when("农历 除夕"),)), everyone)
        self.assertEqual([await self.chat(member) for member in "ABC"], ["小明 中秋快乐", "小红 中秋快乐", "小刚 中秋快乐"])
        self.assertEqual(self.store.count_daily(GROUP, "day:中秋", self.now.date().isoformat()), 3)
        self.assertEqual(self.store.count_daily(GROUP, "day:除夕", self.now.date().isoformat()), 0)


class DieOnTheDayTest(TeamGameTestCase):
    async def test_the_die_is_kinder_by_what_the_day_adds(self):
        dice = Dice(die=(3, 3, 6))
        self.make(rng=dice)
        await self.say("CA", "/抓 @M1 @M2")
        await self.say("CB", "/抓 @M3")
        self.assertIn("掷出了 3 点，差一点", await self.say("CB", "/抢人 @M1"))      # an ordinary day
        self.teams.days = lambda: [NEW_YEAR, Occasion("别的", (parse_when("01-01"),), die=1)]      # the most of them, not the sum
        self.assertIn("掷出了 4 点，把 小一", await self.say("CB", "/抢人 @M1"))
        self.assertIn("掷出了 6 点，把 小二", await self.say("CB", "/抢人 @M2"))     # never past its six


class ChatOnTheDayTest(AIChatTestCase):
    async def test_she_is_told_what_day_it_is(self):
        self.make()
        self.assertNotIn("今天是", self.ai._today(G, []))
        self.ai.days_today = lambda: [NEW_YEAR, Occasion("七夕", (parse_when("农历 七月初七"),))]
        lines = self.ai._today(G, []).split("\n")
        self.assertEqual(lines[1:3], ["今天是元旦。新的一年开始了。", "今天是七夕。"])
        self.assertTrue(lines[0].startswith("现在是北京时间"))


if __name__ == "__main__":
    unittest.main()
