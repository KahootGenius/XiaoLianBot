"""今日运势, birthdays, polls, 征集意见 and 翻牌子; buttons, under these and the games; the digest's AI
comment; rolls in the team game."""
import asyncio
import os
import random
import shutil
import tempfile
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock
from core.config import root_path
from core.style import DEFAULT_STYLE, STYLES, StyleBook
from modules.ai.ai_chat import AIChatModule
from modules.ai.store import ChatStore
from modules.fun.blackjack import BlackjackModule
from modules.fun.crazy_talk import CrazyTalkModule
from modules.fun.dice import DiceModule
from modules.fun import soup
from modules.fun.social import SocialModule, fortune, module as social_module
from modules.fun.social.module import parse_birthday, read_flip_special
from modules.fun.social.store import FunStore
from modules.fun.teams.rules import load_rules
from modules.manager.openapi import QQGateway, as_markdown
from tests.test_ai_chat import FakeDeepSeek
from tests.test_manager import GROUP, START, FakeGateway, FakeMessage, ManagerTestCase
from tests.test_teams import TeamGameTestCase, shipped_rules

NAMES = {"A": "小明", "B": "小红", "C": "小刚", "ADMIN": "管理员"}


class Roll(random.Random):
    """A random source that always rolls the same number, choices() included."""

    def __init__(self, roll):
        super().__init__()
        self.roll = roll

    def random(self):
        return self.roll


class SocialTestCase(unittest.IsolatedAsyncioTestCase):
    def make(self, activity=None, config=None, nickname_of=None, rng=None, count=None, has_left=None,
             find_nickname=None, soups=None):
        self.now = START
        self.gw = FakeGateway()
        self.store = FunStore(":memory:")
        self.addCleanup(self.store.db.close)
        self.social = SocialModule(self.gw, store=self.store, clock=lambda: self.now,
                                   is_admin=lambda group, member, role=None: member == "ADMIN",
                                   activity=activity, rng=rng or random.Random(7), config=config,
                                   nickname_of=nickname_of, count=count, has_left=has_left,
                                   find_nickname=find_nickname, soups=soups)
        return self.social

    async def say(self, member, text, mentions=(), group=GROUP):
        """`member` posts `text`; returns everything the bot answered."""
        message = FakeMessage(group, member, NAMES.get(member, member), text, content=text, mentions_bot=True,
                              mentioned_members=[(m, NAMES.get(m, m)) for m in mentions])
        before = len(self.gw.sent)
        await self.social.observe(message)
        await self.social.handle_social(message)
        return "\n".join(content for _, content, _ in self.gw.sent[before:])


class FortuneTest(SocialTestCase):
    async def test_one_fortune_a_day_from_the_style(self):
        self.make()
        first = await self.say("A", "/今日运势")
        self.assertTrue(first.startswith("🔮 小明 的今日运势 · 11月1日\n运势："))
        self.assertEqual(await self.say("A", "/今日运势"), first)            # the same all day
        pools = STYLES.styles[DEFAULT_STYLE].lookup("social.fortune")
        level = first.split("\n")[1].removeprefix("运势：")
        self.assertIn(level, pools["levels"])
        self.assertEqual(self.gw.buttons[-1], (GROUP, [[("我也要测", "/今日运势")]]))
        week = set()
        for day in range(7):
            self.now = START + timedelta(days=day)
            week.add(await self.say("A", "/今日运势"))
        self.assertGreater(len(week), 1)                                    # and a new one tomorrow


class FortuneReadingTest(SocialTestCase):
    """Her comment on the fortune takes in the member's own latest messages, chosen by fortune.pick_recent."""

    def setUp(self):
        patcher = mock.patch.object(fortune, "pick_recent", lambda texts: [text for text in texts if not text.startswith("/")])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.calls = []

    def reading(self, *answers):
        replies = list(answers)

        async def reader(member, level, good, bad, said):
            self.calls.append((member, level, good, bad, said))
            answer = replies.pop(0) if replies else "运气的事，不是你的错"
            if isinstance(answer, Exception):
                raise answer
            if answer == "slow":
                await asyncio.sleep(0.2)
            return answer

        self.social.fortune_reader = reader

    async def test_her_comment_takes_in_their_own_latest_messages(self):
        self.make()
        self.reading()
        await self.say("A", "今天考试考砸了")
        await self.say("B", "我中奖了")
        await self.say("A", "感觉自己好笨")
        first = await self.say("A", "/今日运势")
        self.assertTrue(first.endswith("\n运气的事，不是你的错"))
        [(member, level, good, bad, said)] = self.calls
        self.assertEqual((member, said), ("小明", ["今天考试考砸了", "感觉自己好笨"]))       # not 小红's
        self.assertIn(f"运势：{level}", first)
        self.assertIn("宜：" + "、".join(good), first)
        self.assertEqual(await self.say("A", "/今日运势"), first)       # the same all day, without asking again
        self.assertEqual(len(self.calls), 1)

    async def test_the_style_comment_when_there_is_no_reading(self):
        self.make()
        plain = await self.say("A", "/今日运势")
        self.assertEqual(self.social.recent, {})                        # nothing kept while it's off
        for answer in (None, RuntimeError("余额不足"), "slow"):
            self.make()
            self.reading(answer)
            await self.say("A", "今天好累")
            with mock.patch.object(social_module, "READING_SECONDS", 0.01):
                self.assertEqual(await self.say("A", "/今日运势"), plain, answer)
        self.make()
        self.reading()
        asked = len(self.calls)
        self.assertEqual(await self.say("A", "/今日运势"), plain)       # nothing worth reading: not asked
        self.assertEqual(len(self.calls), asked)

    async def test_what_is_kept_goes_with_the_day_and_with_the_member(self):
        self.make()
        self.reading()
        await self.say("A", "今天考试考砸了")
        await self.say("A", "/今日运势")
        self.assertIn((GROUP, "A"), self.social.recent)
        self.social.forget_member(GROUP, "A")                           # /删除数据
        self.assertEqual((self.social.recent, self.social.readings), ({}, {}))
        await self.say("B", "我中奖了")
        await self.say("B", "/今日运势")
        self.now = START + timedelta(days=3)
        await self.say("C", "/今日运势")                                 # a new day's housekeeping
        self.assertNotIn((GROUP, "B"), self.social.recent)
        self.assertEqual(self.social.readings, {})

    async def test_only_the_last_day_is_kept_or_read(self):
        self.make()
        self.reading()
        await self.say("A", "昨天的事")
        self.now = START + timedelta(days=1, minutes=-20)
        await self.say("B", "/今日运势")                                  # the day's housekeeping: still fresh
        self.now = START + timedelta(days=1, minutes=1)
        await self.say("A", "今天的事")                                   # not yet swept, but not read either
        await self.say("A", "/今日运势")
        self.assertEqual(self.calls[-1][4], ["今天的事"])
        self.now += timedelta(days=1, hours=1)
        await self.say("B", "随便聊聊")                                   # not a command: the hourly sweep
        self.assertNotIn((GROUP, "A"), self.social.recent)

    async def test_a_group_the_bot_leaves_is_forgotten(self):
        self.make()
        self.reading()
        await self.say("A", "今天考砸了")
        await self.say("A", "/今日运势")
        await self.say("B", "好累", group="ELSEWHERE")
        self.social.forget_group(GROUP)
        self.assertEqual(list(self.social.recent), [("ELSEWHERE", "B")])
        self.assertEqual(self.social.readings, {})

    async def test_tags_never_reach_her(self):
        self.make()
        self.reading()
        await self.say("A", '<faceType=1,faceId="5",ext="eyJ0ZXh0Ijoi">今天考砸了 <qqbot-at-user id="B" /> 唉')
        await self.say("A", "/今日运势")
        self.assertEqual(self.calls[-1][4], ["今天考砸了 唉"])


class BirthdayTest(SocialTestCase):
    async def test_people_who_left_arent_listed_or_mentioned(self):
        gone = set()
        self.make(has_left=lambda group, member: member in gone)
        await self.say("A", "/生日 11-1")
        await self.say("B", "/生日 11-20")
        self.assertIn("（2 位）", await self.say("C", "/本月生日"))
        self.assertEqual(self.social.today_for_ai(GROUP)["birthdays"], ["小明"])
        gone.add("A")
        self.assertNotIn("小明", await self.say("C", "/本月生日"))
        self.assertEqual(self.social.today_for_ai(GROUP)["birthdays"], [])
        gone.clear()                                                    # they came back: still registered
        self.assertIn("小明", await self.say("C", "/本月生日"))

    def test_dates_people_type(self):
        for text in ("10-25", "10月25日", "10/25", "1025", "10.25号"):
            self.assertEqual(parse_birthday(text), (10, 25), text)
        self.assertEqual(parse_birthday("2-29"), (2, 29))
        for text in ("13-01", "2-30", "明天", "10"):
            self.assertIsNone(parse_birthday(text), text)

    async def test_set_wish_and_delete(self):
        self.make()
        self.assertIn("还没有登记", await self.say("A", "/生日"))
        self.assertIn("写成「月-日」", await self.say("A", "/生日 13-40"))
        self.assertIn("11月2日", await self.say("A", "/生日 11-2"))
        self.assertEqual(self.gw.buttons[-1], (GROUP, [[("看看本月寿星", "/本月生日")]]))
        self.assertEqual(await self.say("A", "今天上课好累"), "")             # not the day yet
        self.now = START + timedelta(days=1)
        wish = await self.say("A", "早上好")                               # either of the style's two wishes
        self.assertIn("小明", wish)
        self.assertIn("生日快乐", wish)
        self.assertEqual(await self.say("A", "谢谢大家"), "")                 # once a day
        self.assertIn("11 月过生日的群友（1 位）：\n11月2日 小明", await self.say("B", "/本月生日"))
        self.assertIn("删掉了", await self.say("A", "/生日 删除"))
        self.assertIn("还没有人登记", await self.say("B", "/本月生日"))
        self.assertEqual(self.gw.buttons[-1], (GROUP, [[("我要登记生日", "/生日 ")]]))     # typed in, for the date

    async def test_leap_day_birthdays_come_on_the_28th(self):
        self.make()
        await self.say("A", "/生日 2-29")
        self.now = START.replace(year=2027, month=2, day=28)
        self.assertNotEqual(await self.say("A", "早"), "")


class TodayForAITest(SocialTestCase):
    async def test_ai_chat_hears_of_todays_birthdays_and_the_open_poll(self):
        self.make()
        nothing = {"birthdays": [], "poll": None, "opinions": None, "soup": None}
        self.assertEqual(self.social.today_for_ai(GROUP), nothing)
        await self.say("A", "/生日 11-1")                     # today
        await self.say("B", "/生日 11-2")
        await self.say("A", "/投票 周六几点开？ 19点 20点")
        await self.say("B", "/投 A")
        self.assertEqual(self.social.today_for_ai(GROUP),
                         {"birthdays": ["小明"], "poll": ("周六几点开？", [("A", "19点", 1), ("B", "20点", 0)]),
                          "opinions": None, "soup": None})
        self.now += timedelta(days=8)                         # the poll has run out of time
        self.assertEqual(self.social.today_for_ai(GROUP), nothing)

    async def test_a_poll_or_a_call_nobody_comes_back_to_still_goes_on_schedule(self):
        self.make()
        await self.say("A", "/投票 周六几点开？ 19点 20点")
        await self.say("B", "/投 A")
        await self.say("ADMIN", "/征集意见 下个月玩什么？")
        await self.say("A", "/意见 狼人杀")
        count = lambda table: self.store.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        self.now += timedelta(days=36)                        # the poll ran out after 7, plus the 30 it's kept
        await self.say("A", "/今日运势")                       # nobody has used a poll command since
        self.assertEqual((count("polls"), count("votes")), (1, 1))
        self.now += timedelta(days=2)
        await self.say("A", "/今日运势")
        self.assertEqual((count("polls"), count("votes")), (0, 0))
        self.assertEqual((count("opinion_calls"), count("opinions")), (1, 1))      # 30 open, then 30 kept
        self.now += timedelta(days=23)
        await self.say("A", "/今日运势")
        self.assertEqual((count("opinion_calls"), count("opinions"), count("joined")), (0, 0, 0))

    async def test_and_of_the_open_call_for_opinions(self):
        self.make()
        await self.say("ADMIN", "/征集意见 下个月玩什么？")
        await self.say("A", "/意见 狼人杀")
        self.assertEqual(self.social.today_for_ai(GROUP)["opinions"], ("下个月玩什么？", 1))
        self.now += timedelta(days=30)                        # nobody ended it: it closes by itself
        self.assertIsNone(self.social.today_for_ai(GROUP)["opinions"])
        self.assertIn("没有在征集意见", await self.say("A", "/征集意见"))


class Host:
    """Stands in for AI chat as 海龟汤's host: its verdicts come off a list, in order."""

    def __init__(self):
        self.heard, self.verdicts, self.hook = [], [], None

    async def __call__(self, surface, truth, points, said, guess):
        self.heard.append((said, guess))
        self.puzzle = (surface, truth, points)
        if self.hook:
            self.hook()
        verdict = self.verdicts.pop(0) if self.verdicts else (
            '{"solved": false, "close": false}' if guess else '{"answer": "无关", "key": false}')
        if isinstance(verdict, Exception):
            raise verdict
        return verdict


BALD = soup.Puzzle("bald", "淋不湿的头发", "阿强淋了雨，头发却没湿。为什么？", "阿强是光头。", ("阿强没有头发",),
                   ("和雨没关系。", "想想头发本身。"))
BUS = soup.Puzzle("bus", "终点站", "车上还剩一个人，没人觉得不对。为什么？", "他是司机。", ("剩下的人是司机",))
FISH = soup.Puzzle("fish", "三条鱼", "四个人钓了三条鱼。为什么？", "只有三个人。", ("只有三个人", "祖孙三代"))
YES, NO = '{"answer": "是", "key": false}', '{"answer": "不是", "key": false}'
RIGHT = '{"solved": true, "close": false}'
UNRELATED = r"\n(无关。|这个和真相没有关系。)$"       # what she says when the host's verdict is 无关, which is Host's default


class SoupTest(SocialTestCase):
    """/海龟汤: she hosts a puzzle. Members ask yes-or-no questions and guess; what she answers comes
    from a verdict the group never sees."""

    def cook(self, *puzzles, **kwargs):
        self.make(soups={puzzle.code: puzzle for puzzle in puzzles or (BALD,)}, **kwargs)
        self.host = self.social.soup_host = Host()
        return self.social

    async def test_she_opens_one_and_shows_it_again(self):
        self.cook()
        self.assertEqual(await self.say("A", "/海龟汤"),
                         "🍲 海龟汤开锅：淋不湿的头发（小明 点的）\n阿强淋了雨，头发却没湿。为什么？\n"
                         "问是非题：@我 /问 你的问题，我只回答「是」「不是」「无关」。\n觉得知道真相了：@我 /猜 你的答案")
        buttons = (GROUP, [[("我要提问", "/问 "), ("我来猜", "/猜 "), ("要个提示", "/海龟汤 提示")]])
        self.assertEqual(self.gw.buttons[-1], buttons)
        self.assertEqual(self.social.today_for_ai(GROUP)["soup"], "阿强淋了雨，头发却没湿。为什么？")
        self.assertIsNone(self.social.today_for_ai("ELSEWHERE")["soup"])
        self.host.verdicts = [YES, '{"answer": "无关", "key": false}', NO]
        for question in ("他淋到雨了吗", "那天是周末吗", "他戴了帽子吗"):
            await self.say("B", f"/问 {question}")
        self.assertEqual(await self.say("B", "/海龟汤"),                    # one open at a time: this shows it
                         "🍲 正在喝的海龟汤：淋不湿的头发\n阿强淋了雨，头发却没湿。为什么？\n已经问了 3 个问题，给过 0 条提示。\n"
                         "最近问出来的：\n· 他淋到雨了吗 → 是\n· 他戴了帽子吗 → 不是")
        self.assertEqual(self.gw.buttons[-1], buttons)
        self.assertNotIn("光头", "".join(self.gw.texts(GROUP)))              # the truth stays with the host

    async def test_she_answers_yes_or_no_from_the_verdict(self):
        self.cook()
        await self.say("A", "/海龟汤")
        self.host.verdicts = ['{"answer": "是", "key": true}', '好的：{"answer": "「不是」", "key": true}',
                              '{"answer": "无关"}', '{"answer": "是也不是", "key": false}', YES]
        self.assertRegex(await self.say("B", "/问 他 是\n光头吗"), r"^🥄 小红 问：他 是 光头吗\n(是。|是的。|对。)（问到点子上了！）$")
        self.assertEqual(self.host.puzzle, ("阿强淋了雨，头发却没湿。为什么？", "阿强是光头。", ["阿强没有头发"]))
        self.assertRegex(await self.say("B", "/问他打伞了吗"), r"^🥄 小红 问：他打伞了吗\n(不是。|不对。)$")     # no space; a 不是 isn't key
        self.assertRegex(await self.say("B", "/提问：今天星期几"), r"^🥄 小红 问：今天星期几\n(无关。|这个和真相没有关系。)$")
        self.assertRegex(await self.say("A", "/问: 他的衣服和头发都湿了吗"), r"\n(是也不是。|只说对了一部分。)$")
        self.assertRegex(await self.say("A", "/问 他出门了吗"), r"\n(是。|是的。|对。)$")
        self.assertEqual(self.host.heard, [("他 是 光头吗", False), ("他打伞了吗", False), ("今天星期几", False),
                                           ("他的衣服和头发都湿了吗", False), ("他出门了吗", False)])
        turns = self.store.soup_turns(1)
        self.assertEqual([(turn["member_openid"], turn["answer"]) for turn in turns],
                         [("B", "positive"), ("B", "negative"), ("B", "unrelated"), ("A", "partly"), ("A", "positive")])

    async def test_what_she_cant_answer_isnt_kept(self):
        self.cook()
        await self.say("A", "/海龟汤")
        self.host.verdicts = ['{"answer": "不能回答", "key": false}', "我觉得是吧", '{"answer": "可能"}', RuntimeError("超时")]
        self.assertEqual(await self.say("B", "/问 为什么他的头发没湿"),
                         "这个我答不了。问一个能用「是」或「不是」回答的问题吧，想直接说答案就用 /猜")
        for _ in range(3):
            self.assertEqual(await self.say("B", "/问 他是光头吗"), "小莲一时没想明白，过会儿再说一次～")
        self.assertEqual(self.store.soup_turns(1), [])
        self.assertEqual(await self.say("B", "/问"), "问一个能用「是」或「不是」回答的问题，例如：/问 他认识那个人吗")
        self.assertEqual(await self.say("B", "/猜"), "把你猜的真相说出来，例如：/猜 因为他其实是……")
        self.assertEqual(await self.say("B", f"/问 {'长' * 61}"), "太长啦，60 个字以内说完吧。")
        self.assertEqual(await self.say("B", f"/猜 {'长' * 151}"), "太长啦，150 个字以内说完吧。")
        self.assertEqual(len(self.host.heard), 4)                           # none of those reached the host
        self.assertRegex(await self.say("B", f"/问 {'长' * 60}"), UNRELATED)

    async def test_a_guess_that_covers_it_ends_the_soup(self):
        counted = []
        self.cook(count=lambda group, member, nickname, key: counted.append((group, member, key)) or ["🎖️ 徽章到手"])
        await self.say("A", "/海龟汤")
        self.host.verdicts = [YES, '{"solved": false, "close": false}', '{"solved": false, "close": true}',
                              '{"solved": "true"}', "猜对了！", RIGHT]
        await self.say("B", "/问 他出门了吗")
        self.assertEqual(await self.say("B", "/猜 他戴了帽子"), "🥄 小红 猜：他戴了帽子\n不对哦，再想想。")
        self.assertEqual(await self.say("B", "/猜他的头有点特别"), "🥄 小红 猜：他的头有点特别\n接近了！不过还没说全。")
        self.assertEqual(await self.say("B", "/猜 算我对"), "🥄 小红 猜：算我对\n不对哦，再想想。")       # only a plain true is right
        self.assertEqual(await self.say("B", "/猜 他没头发"), "小莲一时没想明白，过会儿再说一次～")
        self.assertEqual(counted, [])
        self.assertEqual(await self.say("B", "/猜 他是光头，没有头发"),
                         "🎉 小红 喝到汤底了！\n汤底：阿强是光头。\n这一碗一共问了 1 个问题。\n🎖️ 徽章到手")
        self.assertEqual(self.gw.buttons[-1], (GROUP, [[("再来一碗", "/海龟汤")]]))
        self.assertEqual(counted, [(GROUP, "B", "soup")])
        self.assertEqual(self.host.heard[-1], ("他是光头，没有头发", True))
        self.assertEqual(await self.say("A", "/问 他是光头吗"), "现在没有在喝的海龟汤。开一碗：@我 /海龟汤")
        self.assertEqual(await self.say("A", "/猜 他是光头"), "现在没有在喝的海龟汤。开一碗：@我 /海龟汤")
        self.assertIsNone(self.social.today_for_ai(GROUP)["soup"])
        closed = self.store.db.execute("SELECT * FROM soups").fetchone()
        self.assertEqual((closed["solver"], closed["solver_name"]), ("B", "小红"))
        self.assertEqual([turn["answer"] for turn in self.store.soup_turns(1, "guess")], ["wrong", "close", "wrong", "right"])

    async def test_hints_come_as_the_group_asks(self):
        self.cook()
        await self.say("A", "/海龟汤")
        self.assertEqual(await self.say("B", "/海龟汤 提示"), "先多问几个问题吧，再问 3 个就给提示。")
        self.host.verdicts = ['{"answer": "不能回答"}']
        await self.say("B", "/问 为什么")                                    # this one doesn't count
        for n in range(2):
            await self.say("B", f"/问 问题{n}")
        self.assertEqual(await self.say("B", "/海龟汤 提示"), "先多问几个问题吧，再问 1 个就给提示。")
        await self.say("B", "/问 问题2")
        self.assertEqual(await self.say("A", "/海龟汤 提示"), "💡 提示 1/2：和雨没关系。")
        self.assertEqual(await self.say("A", "/海龟汤 线索"), "先多问几个问题吧，再问 3 个就给提示。")
        self.now += timedelta(minutes=2)                                    # or B would be asking too fast
        for n in range(3, 6):
            await self.say("B", f"/问 问题{n}")
        self.assertEqual(await self.say("A", "/海龟汤 提示"), "💡 提示 2/2：想想头发本身。")
        self.assertEqual(await self.say("A", "/海龟汤 提示"), "提示已经给完啦，剩下的靠你们了。")
        self.assertIn("已经问了 6 个问题，给过 2 条提示。", await self.say("A", "/海龟汤"))
        self.cook(BUS)
        await self.say("A", "/海龟汤")
        self.assertEqual(await self.say("A", "/海龟汤 提示"), "这一碗没有提示，靠你们自己了。")

    async def test_whoever_opened_it_or_an_admin_gives_up(self):
        self.cook()
        nothing = "现在没有在喝的海龟汤。开一碗：@我 /海龟汤"
        self.assertEqual(await self.say("A", "/海龟汤 结束"), nothing)
        self.assertEqual(await self.say("A", "/海龟汤 提示"), nothing)
        await self.say("A", "/海龟汤")
        self.assertEqual(await self.say("B", "/海龟汤 结束"), "只有点这碗汤的人或管理员能结束哦。")
        self.assertEqual(await self.say("A", "/海龟汤 放弃"), "🍲 「淋不湿的头发」不喝了。汤底是：阿强是光头。")
        self.assertEqual(self.gw.buttons[-1], (GROUP, [[("再来一碗", "/海龟汤")]]))
        self.assertIsNone(self.store.open_soup(GROUP))
        await self.say("A", "/海龟汤")
        self.assertIn("汤底是：阿强是光头。", await self.say("ADMIN", "/海龟汤 揭晓"))
        await self.say("A", "/海龟汤")
        self.social.forget_member(GROUP, "A")                                # whoever opened it is gone:
        self.assertIn("汤底是：阿强是光头。", await self.say("B", "/海龟汤 结束"))  # anyone may

    async def test_one_left_a_day_goes_cold(self):
        self.cook()
        await self.say("A", "/海龟汤")
        self.now += timedelta(hours=23, minutes=59)
        self.assertRegex(await self.say("B", "/问 他出门了吗"), UNRELATED)
        self.now += timedelta(minutes=1)
        self.assertIsNone(self.social.today_for_ai(GROUP)["soup"])
        cold = "上一碗「淋不湿的头发」放了一天没人喝完，凉了。汤底是：阿强是光头。\n"
        self.assertEqual(await self.say("B", "/问 他是光头吗"), cold + "现在没有在喝的海龟汤。开一碗：@我 /海龟汤")
        self.assertEqual(await self.say("B", "/问 他是光头吗"), "现在没有在喝的海龟汤。开一碗：@我 /海龟汤")     # said once
        self.assertEqual(len(self.host.heard), 1)
        await self.say("A", "/海龟汤")
        self.now += timedelta(days=1)
        self.assertTrue((await self.say("B", "/海龟汤")).startswith(cold + "🍲 海龟汤开锅：淋不湿的头发（小红 点的）"))
        self.social.soups = {}                                              # its puzzle left the file
        self.social.soups = {BUS.code: BUS}
        self.assertTrue((await self.say("A", "/海龟汤")).startswith("🍲 海龟汤开锅：终点站"))

    async def test_only_so_many_questions_and_not_too_fast(self):
        self.cook()
        await self.say("A", "/海龟汤")
        for n in range(6):
            self.assertRegex(await self.say("B", f"/问 问题{n}"), UNRELATED)
        self.assertEqual(await self.say("B", "/问 问题6"), "问得太快啦，让小莲喘口气，过一会儿再来～")
        self.assertEqual(await self.say("B", "/猜 他是光头"), "")              # told once, then nothing
        self.assertRegex(await self.say("A", "/问 问题7"), UNRELATED)          # each member's own pace
        self.assertEqual(len(self.host.heard), 7)
        self.now += timedelta(seconds=61)
        self.assertRegex(await self.say("B", "/问 问题8"), UNRELATED)
        with mock.patch.object(social_module, "SOUP_TURNS", 8):
            self.assertEqual(await self.say("A", "/问 问题9"),
                             "这一碗已经问了 8 个问题啦，直接 /猜 吧。实在猜不出，点汤的人或管理员可以 /海龟汤 结束")
            self.host.verdicts = [RIGHT]
            self.assertIn("喝到汤底了", await self.say("A", "/猜 他是光头"))     # guessing still goes

    async def test_a_group_gets_every_puzzle_before_one_comes_round_again(self):
        self.cook(BALD, BUS, FISH)
        seen = []
        for _ in range(7):
            opened = await self.say("A", "/海龟汤")
            seen.append(self.store.open_soup(GROUP)["puzzle"])
            self.assertIn("海龟汤开锅", opened)
            await self.say("A", "/海龟汤 结束")
        self.assertEqual(sorted(seen[:3]), ["bald", "bus", "fish"])
        self.assertEqual(sorted(seen[3:6]), ["bald", "bus", "fish"])
        self.assertTrue(all(first != second for first, second in zip(seen, seen[1:])))     # never the same twice running
        await self.say("A", "/海龟汤", group="G2")                            # each group's own round
        self.assertEqual(len(self.store.soup_played("G2")), 1)

    async def test_it_ends_under_her_while_she_thinks(self):
        self.cook()
        await self.say("A", "/海龟汤")
        nothing = "现在没有在喝的海龟汤。开一碗：@我 /海龟汤"
        self.host.hook = lambda: self.store.close_soup(1, 5, "A", "小明")      # someone else just solved it
        self.host.verdicts = [RIGHT]
        self.assertEqual(await self.say("B", "/猜 他是光头"), nothing)
        self.assertEqual(self.store.soup_turns(1, "guess"), [])
        self.assertFalse(self.store.close_soup(1, 9, "B", "小红"))             # closing it again changes nothing
        self.assertEqual(self.store.db.execute("SELECT solver FROM soups").fetchone()["solver"], "A")
        await self.say("A", "/海龟汤")
        self.host.hook = lambda: self.store.close_soup(2, 5)
        self.host.verdicts = [YES]
        self.assertEqual(await self.say("B", "/问 他是光头吗"), nothing)
        self.assertEqual(self.store.soup_turns(2), [])

    async def test_no_host_or_no_puzzles_no_game(self):
        self.make(soups={BALD.code: BALD})
        for command in ("/海龟汤", "/海龟汤 提示"):
            self.assertEqual(await self.say("A", command), "海龟汤要开了 AI 聊天才能玩哦。")
        self.assertEqual(await self.say("A", "/问 他是光头吗"), "现在没有在喝的海龟汤。开一碗：@我 /海龟汤")
        self.cook()
        self.social.soups = {}
        self.assertEqual(await self.say("A", "/海龟汤"), "海龟汤没有开启哦。")
        self.make(config={"social": {"soup": False}})
        self.assertEqual(self.social.soups, {})
        self.make()                                                         # the shipped puzzles
        self.assertEqual(len(self.social.soups), 13)

    async def test_what_is_kept_and_for_how_long(self):
        self.cook()
        await self.say("A", "/海龟汤")
        self.host.verdicts = [YES, YES, RIGHT]
        await self.say("A", "/问 他出门了吗")
        await self.say("B", "/问 他淋雨了吗")
        await self.say("B", "/猜 他是光头")
        self.social.forget_member(GROUP, "B")                                # /删除数据
        self.assertEqual([turn["member_openid"] for turn in self.store.soup_turns(1)], ["A"])
        self.assertEqual(self.store.soup_turns(1, "guess"), [])
        closed = self.store.db.execute("SELECT * FROM soups").fetchone()
        self.assertEqual((closed["starter"], closed["solver"], closed["solver_name"]), ("A", "", None))
        self.assertNotIn((GROUP, "B"), self.social.soup_pace)
        self.now += timedelta(days=6, hours=23)
        await self.say("A", "/今日运势")                                     # the day's first command tidies up
        self.assertEqual(len(self.store.soup_turns(1)), 1)
        self.now += timedelta(days=1)
        await self.say("A", "/今日运势")
        self.assertEqual(self.store.soup_turns(1), [])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM soups").fetchone()[0], 0)
        self.assertEqual(self.store.soup_played(GROUP), ["bald"])           # which puzzles it has had stays
        await self.say("A", "/海龟汤")                                        # one nobody ever comes back to
        await self.say("A", "/问 他出门了吗")
        self.now += timedelta(days=1, hours=23)
        await self.say("A", "/今日运势")
        self.assertIsNotNone(self.store.open_soup(GROUP))                   # for a day more, a question still finds it
        self.now += timedelta(days=1)
        await self.say("A", "/今日运势")
        self.assertIsNone(self.store.open_soup(GROUP))                      # then it closes as of when its day was up,
        closed = self.store.db.execute("SELECT * FROM soups").fetchone()
        self.assertEqual(closed["closed_at"] - closed["created_at"], 86400)
        self.now += timedelta(days=5)
        await self.say("A", "/今日运势")
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM soup_turns").fetchone()[0], 1)
        self.now += timedelta(days=2)
        await self.say("A", "/今日运势")
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM soup_turns").fetchone()[0], 0)     # and goes on schedule
        await self.say("A", "/海龟汤")
        await self.say("A", "/问 他出门了吗")
        self.social.forget_group(GROUP)
        self.assertEqual((self.store.open_soup(GROUP), self.store.soup_played(GROUP), self.social.soup_pace), (None, [], {}))
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM soup_turns").fetchone()[0], 0)


class SoupPuzzlesTest(unittest.TestCase):
    def write(self, text):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder)
        path = os.path.join(folder, "soups.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_the_shipped_puzzles(self):
        puzzles, problems = soup.load(root_path("prompts", "soups.yaml"))
        self.assertEqual(problems, [])
        self.assertEqual(len(puzzles), 13)
        for puzzle in puzzles.values():
            self.assertTrue(1 <= len(puzzle.points) <= 2 and puzzle.hints, puzzle.code)
            self.assertTrue(puzzle.surface.endswith("为什么？"), puzzle.code)
            self.assertNotIn("\n", puzzle.surface + puzzle.truth)

    def test_a_broken_puzzle_is_named_and_left_out(self):
        good = "- {code: ok, title: 好题, surface: '汤  面\n第二行', truth: 汤底, points: [' 关键 点 '], hints: [提示]}\n"
        puzzles, problems = soup.load(self.write(good + """
- {code: ok, title: 重复, surface: 面, truth: 底, points: [点]}
- {code: 中文, title: 编号, surface: 面, truth: 底, points: [点]}
- {code: nopoints, title: 没有关键点, surface: 面, truth: 底}
- {code: many, title: 关键点太多, surface: 面, truth: 底, points: [一, 二, 三, 四, 五]}
- {code: hints, title: 提示不是列表, surface: 面, truth: 底, points: [点], hints: 一句}
- {code: notruth, title: 没有汤底, surface: 面, points: [点]}
- 只有一句话
"""))
        self.assertEqual(puzzles, {"ok": soup.Puzzle("ok", "好题", "汤 面 第二行", "汤底", ("关键 点",), ("提示",))})
        text = "\n".join(problems)
        for phrase in ("第 2 题（ok）：code 和前面的重复了", "第 3 题（中文）：code 只能用英文", "第 4 题（nopoints）：points 要写 1 到 4 条",
                       "第 5 题（many）：points 要写", "第 6 题（hints）：hints 要写成列表", "第 7 题：code、title、surface、truth 都要写",
                       "第 8 题：code、title"):
            self.assertIn(phrase, text)
        self.assertEqual(len(problems), 7)
        self.assertEqual(soup.load(self.write("code: 不是列表")), ({}, ["soups.yaml 要写成列表，每道题以「- code:」开头"]))
        self.assertEqual(soup.load(self.write("- [")), ({}, mock.ANY))
        self.assertEqual(soup.load(os.path.join(tempfile.gettempdir(), "no-such-soups.yaml")), ({}, []))
        long = "- {code: long, title: 太长, surface: %s, truth: 底, points: [点]}" % ("长" * 401)
        self.assertIn("surface 最多 400 个字", soup.load(self.write(long))[1][0])

    def test_reading_the_hosts_verdicts(self):
        self.assertEqual(soup.parse_answer('{"answer": "是", "key": true}'), ("positive", True))
        self.assertEqual(soup.parse_answer('{"answer": "是", "key": "true"}'), ("positive", False))
        self.assertEqual(soup.parse_answer('我想想：{"answer": " 不是。", "key": true}'), ("negative", False))
        self.assertEqual(soup.parse_answer('{"answer": "无关"}'), ("unrelated", False))
        self.assertEqual(soup.parse_answer('{"answer": "是也不是", "key": true}'), ("partly", False))
        self.assertEqual(soup.parse_answer('{"answer": "不能回答"}'), (soup.INVALID, False))
        for raw in ("", "是", '{"answer": "汤底是他是光头"}', '{"key": true}', '{"answer": null}'):
            self.assertIsNone(soup.parse_answer(raw), raw)
        self.assertEqual(soup.parse_guess(RIGHT), soup.RIGHT)
        self.assertEqual(soup.parse_guess('{"solved": false, "close": true}'), soup.CLOSE)
        self.assertEqual(soup.parse_guess('{"solved": false}'), soup.WRONG)
        self.assertEqual(soup.parse_guess('{"solved": "true", "close": "true"}'), soup.WRONG)
        self.assertEqual(soup.parse_guess('{"solved": 1, "close": false}'), soup.WRONG)
        for raw in ("", "猜对了", '{"close": true}'):
            self.assertIsNone(soup.parse_guess(raw), raw)

    def test_which_puzzle_comes_next(self):
        puzzles = {puzzle.code: puzzle for puzzle in (BALD, BUS, FISH)}
        for _ in range(20):
            self.assertIs(soup.pick(puzzles, ["bald", "fish"], random.Random()), BUS)
            self.assertIsNot(soup.pick(puzzles, ["bald", "bus", "fish"], random.Random()), FISH)     # not the last one again
        self.assertIs(soup.pick({"bald": BALD}, ["bald"], random.Random()), BALD)                    # unless it's all there is
        self.assertIn(soup.pick(puzzles, [], random.Random()), puzzles.values())

    def test_the_check_command(self):
        import io
        out = io.StringIO()
        self.assertEqual(soup.main(out), 0)
        self.assertIn("13 puzzles ready, 0 problems.", out.getvalue())


class SoupHostTest(unittest.IsolatedAsyncioTestCase):
    """AI chat as the host: one call that has the truth, and her chat, which never does."""

    def setUp(self):
        self.client, self.store = FakeDeepSeek(), ChatStore(":memory:")
        self.addCleanup(self.store.db.close)
        self.ai = AIChatModule(FakeGateway(), {}, store=self.store, client=self.client)

    async def test_the_host_gets_the_puzzle_and_what_was_said_and_nothing_else(self):
        self.client.answers["soup"] = [YES, RIGHT]
        self.assertEqual(await self.ai.host_soup(BALD.surface, BALD.truth, ["阿强没有头发", "另一点"], "他是光头吗"), YES)
        self.assertEqual(await self.ai.host_soup(BALD.surface, BALD.truth, ["阿强没有头发"], "他是光头", guess=True), RIGHT)
        question, guess = self.client.requests("soup")
        for request in (question, guess):
            self.assertEqual((len(request["messages"]), request["model"], request["temperature"]), (1, "deepseek-flash", 0))
            self.assertEqual(request["extra_body"], {"thinking": {"type": "disabled"}})
            self.assertNotIn("小莲", request["messages"][0]["content"])       # no persona
        text = question["messages"][0]["content"]
        self.assertIn("汤面（玩家看得到）：阿强淋了雨，头发却没湿。为什么？\n汤底（只有你知道）：阿强是光头。\n"
                      "关键点：\n- 阿强没有头发\n- 另一点\n", text)
        self.assertIn("「他是光头吗」", text)
        self.assertIn('只回 JSON：{"answer": "是", "key": false}', text)
        self.assertIn("玩家在猜汤底", guess["messages"][0]["content"])
        self.assertIn('只回 JSON：{"solved": false, "close": false}', guess["messages"][0]["content"])
        self.client.answers["soup"] = [RuntimeError("余额不足")]
        with self.assertRaises(RuntimeError):
            await self.ai.host_soup(BALD.surface, BALD.truth, ["阿强没有头发"], "他是光头吗")

    async def test_in_chat_she_knows_the_soup_but_not_its_truth(self):
        social = SocialModule(FakeGateway(), store=FunStore(":memory:"), soups={BALD.code: BALD})
        self.addCleanup(social.store.db.close)
        social.soup_host = self.ai.host_soup
        self.ai.social_today = social.today_for_ai
        opening = FakeMessage(GROUP, "A", "小明", "/海龟汤", content="/海龟汤", mentions_bot=True)
        await social.handle_social(opening)
        chatting = FakeMessage(GROUP, "A", "小明", "汤底是什么", content="汤底是什么", mentions_bot=True)
        await self.ai.handle_ai_chat(chatting)
        await self.ai.settle()
        request = "\n".join(str(message["content"]) for message in self.client.requests("chat")[-1]["messages"])
        self.assertIn("群里正在玩海龟汤，汤面是：阿强淋了雨，头发却没湿。为什么？\n聊天时的你不知道汤底", request)
        self.assertNotIn("光头", request)


class PollTest(SocialTestCase):
    async def test_a_poll_from_start_to_end(self):
        self.make()
        self.assertIn("没有进行中的投票", await self.say("A", "/投票"))
        self.assertIn("用法", await self.say("A", "/投票 只有一个选项"))
        started = await self.say("A", "/投票 周六几点开？ 19点 20点 21点")
        self.assertIn("📊 周六几点开？（发起：小明", started)
        self.assertIn("C. 21点 · 0 票", started)
        self.assertEqual(self.gw.buttons[-1][1], [[("A. 19点", "/投 A"), ("B. 20点", "/投 B"), ("C. 21点", "/投 C")]])
        self.assertIn("已经有一个投票", await self.say("B", "/投票 另一个 是 否"))
        self.assertIn("小明 投给了 B. 20点（现在共 1 票）", await self.say("A", "/投 B"))
        await self.say("B", "/投B")
        await self.say("C", "/投 1")
        await self.say("A", "/投 c")                                         # changes their vote
        self.assertIn("没有这个选项", await self.say("C", "/投 Z"))
        self.assertIn("B. 20点 · 1 票：小红", await self.say("C", "/投票"))
        self.assertIn("只有发起人或管理员", await self.say("C", "/结束投票"))
        result = await self.say("ADMIN", "/结束投票")
        self.assertIn("📊 投票结束：周六几点开？（共 3 票）", result)
        self.assertIn("A. 19点 · 1 票：小刚", result)
        self.assertIn("C. 21点 · 1 票：小明", result)
        self.assertIn("没有进行中的投票", await self.say("A", "/投 A"))

    async def test_polls_close_themselves_after_a_week(self):
        self.make()
        await self.say("A", "/投票 去哪 甲 乙")
        self.now = START + timedelta(days=7)
        self.assertIn("没有进行中的投票", await self.say("B", "/投 A"))
        self.assertIn("📊 再来", await self.say("B", "/投票 再来 甲 乙"))


class BadgeCountTest(SocialTestCase):
    """Polls, 征集 and birthdays count toward the group manager's badges, each only once."""

    def make_counting(self):
        self.counted = []

        def count(group, member, nickname, key):
            self.counted.append((member, key))
            return [f"🎉 {nickname} +1 {key}"]
        return self.make(count=count)

    async def test_each_poll_counts_once(self):
        self.make_counting()
        await self.say("A", "/投票 周六几点开？ 19点 20点")
        self.assertIn("投给了", (voted := await self.say("A", "/投 A")))
        self.assertIn("\n🎉 小明 +1 vote", voted)                          # in the same reply
        self.assertNotIn("🎉", await self.say("A", "/投 B"))              # changing the vote
        await self.say("B", "/投 A")
        self.assertEqual(self.counted, [("A", "vote"), ("B", "vote")])

    async def test_each_call_counts_once_even_after_taking_it_back(self):
        self.make_counting()
        await self.say("ADMIN", "/征集意见 下个月玩什么？")
        self.assertIn("🎉 小明 +1 opinion", await self.say("A", "/意见 想玩狼人杀"))
        await self.say("A", "/意见 撤回")
        self.assertNotIn("🎉", await self.say("A", "/意见 还是想玩剧本杀"))
        await self.say("A", "/意见 周末晚上比较好")
        self.assertEqual(self.counted, [("A", "opinion")])
        self.store.forget_member(GROUP, "A")                    # /删除数据: who joined goes too
        self.assertTrue(self.store.join_call(self.store.open_call(GROUP)["id"], "A"))

    async def test_a_birthday_counts_with_the_wish(self):
        self.make_counting()
        await self.say("A", "/生日 11-2")
        self.now = START + timedelta(days=1)
        self.assertIn("🎉 小明 +1 birthday", await self.say("A", "早上好"))
        await self.say("A", "今天好开心")
        self.assertEqual(self.counted, [("A", "birthday")])


class OpinionTest(SocialTestCase):
    async def test_a_call_for_opinions_from_start_to_end(self):
        self.make()
        self.assertIn("没有在征集意见", await self.say("A", "/征集意见"))
        self.assertIn("只有管理员", await self.say("A", "/征集意见 下个月玩什么？"))
        started = await self.say("ADMIN", "/征集意见 下个月玩什么？")
        self.assertIn("📮 管理员 发起了意见征集：下个月玩什么？", started)
        self.assertIn("每人最多 3 条", started)
        self.assertEqual(self.gw.buttons[-1], (GROUP, [[("我要提意见", "/意见 ")]]))     # typed in, for the opinion
        self.assertIn("正在征集「下个月玩什么？」", await self.say("ADMIN", "/征集意见 另一个"))
        self.assertIn("收到 小明 的意见～（现在 1 人提了 1 条）", await self.say("A", "/意见 想玩狼人杀"))
        await self.say("A", "/意见：周末晚上比较好")              # with a colon
        await self.say("B", "/意见想去KTV")                       # without a space
        self.assertIn("用法", await self.say("C", "/意见"))
        self.assertIn("最多 200 个字", await self.say("C", "/意见 " + "长" * 201))
        await self.say("A", "/意见 第三条")
        self.assertIn("每人最多提 3 条", await self.say("A", "/意见 第四条"))
        self.assertIn("收回了：第三条", await self.say("A", "/意见 撤回"))
        self.assertIn("还没有提过意见", await self.say("C", "/意见 撤回"))
        status = await self.say("C", "/意见征集")
        self.assertIn("📮 正在征集意见：下个月玩什么？（发起：管理员）\n已有 2 人提了 3 条", status)
        self.assertIn("只有发起人或管理员", await self.say("C", "/结束征集"))
        ended = await self.say("ADMIN", "/结束征集")
        self.assertEqual(ended, "📮 意见征集结束：下个月玩什么？（2 人，共 3 条）\n"
                                "1. 小明：想玩狼人杀\n2. 小明：周末晚上比较好\n3. 小红：想去KTV")
        self.assertIn("没有在征集意见", await self.say("A", "/意见 晚了"))
        await self.say("ADMIN", "/征集意见 空的\n第二行")
        self.assertIn("意见征集结束：空的 第二行\n这次没有人提意见", await self.say("ADMIN", "/结束征集意见"))

    async def test_her_summary_follows_the_list(self):
        self.make()
        heard = []

        async def summarizer(topic, opinions):
            heard.append((topic, opinions))
            return "好几个人都想玩狼人杀～"
        self.social.opinion_summarizer = summarizer
        await self.say("ADMIN", "/征集意见 玩什么")
        await self.say("A", "/意见 狼人杀")
        await self.say("B", "/意见 也是狼人杀")
        ended = await self.say("ADMIN", "/结束征集")
        self.assertTrue(ended.endswith("2. 小红：也是狼人杀\n💬 小莲看完大家的意见：\n好几个人都想玩狼人杀～"))
        self.assertEqual(heard, [("玩什么", ["狼人杀", "也是狼人杀"])])       # not who wrote them

    async def test_a_long_list_goes_out_in_parts(self):
        self.make()
        await self.say("ADMIN", "/征集意见 玩什么")
        for member in "ABCDEFGH":
            for i in range(3):
                await self.say(member, f"/意见 {member}{i}" + "想" * 100)
        before = len(self.gw.sent)
        await self.say("ADMIN", "/结束征集")
        parts = [content for _, content, _ in self.gw.sent[before:]]
        self.assertGreater(len(parts), 2)
        self.assertTrue(all(len(part) <= 900 for part in parts))
        self.assertIn("24. H：H2", parts[-1])

    async def test_forgetting_someone_and_clearing_old_calls(self):
        self.make()
        await self.say("ADMIN", "/征集意见 玩什么")
        await self.say("A", "/意见 狼人杀")
        await self.say("B", "/意见 KTV")
        self.social.forget_member(GROUP, "A")
        self.assertIn("已有 1 人提了 1 条", await self.say("C", "/征集意见"))
        await self.say("ADMIN", "/结束征集")
        self.now += timedelta(days=31)
        await self.say("C", "/征集意见")                      # the day's housekeeping runs first
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM opinions").fetchone()[0], 0)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM opinion_calls").fetchone()[0], 0)


class OpinionSummaryTest(unittest.IsolatedAsyncioTestCase):
    async def test_ai_sums_them_up_in_character(self):
        client, store = FakeDeepSeek(), ChatStore(":memory:")
        self.addCleanup(store.db.close)
        ai = AIChatModule(FakeGateway(), {}, store=store, client=client)
        self.assertIsNone(await ai.summarize_opinions("玩什么", ["狼人杀", "KTV"]))     # two need no summary
        self.assertEqual(client.requests("chat"), [])
        client.answers["chat"].append("好几个人提到狼人杀[表情:开心]")
        self.assertEqual(await ai.summarize_opinions("玩什么", ["狼人杀", "也是狼人杀", "KTV"]), "好几个人提到狼人杀")
        request = client.requests("chat")[-1]["messages"]
        self.assertIn("你是「小莲」", request[0]["content"])
        self.assertIn("主题是「玩什么」", request[1]["content"])
        self.assertIn("1. 狼人杀\n2. 也是狼人杀\n3. KTV", request[1]["content"])
        client.answers["chat"].append(RuntimeError("余额不足"))
        self.assertIsNone(await ai.summarize_opinions("玩什么", ["一", "二", "三"]))   # the list goes out without it
        off = AIChatModule(FakeGateway(), {"ai_chat": {"opinion_summary": False}}, store=store, client=client)
        self.assertIsNone(await off.summarize_opinions("玩什么", ["一", "二", "三"]))


class PickRecentTest(unittest.TestCase):
    def test_what_goes_with_the_fortune(self):
        said = ["/今日运势", "／翻牌子", "哈哈哈", "嗯嗯", "1", "？？", "😂😂", "好累", "考砸了", "ok", "打卡", "打卡"]
        self.assertEqual(fortune.pick_recent(said), ["好累", "考砸了", "ok", "打卡"])
        self.assertEqual(fortune.pick_recent(["打卡", "好累", "打卡"]), ["好累", "打卡"])     # where it was said last
        many = [f"第{n}件事" for n in range(15)]
        self.assertEqual(fortune.pick_recent(many), many[-fortune.RECENT_SHOWN:])
        self.assertEqual(fortune.pick_recent([]), [])


class FortuneCommentTest(unittest.IsolatedAsyncioTestCase):
    async def test_ai_reads_the_fortune_in_character(self):
        client, store = FakeDeepSeek(), ChatStore(":memory:")
        self.addCleanup(store.db.close)
        off = AIChatModule(FakeGateway(), {}, store=store, client=client)
        self.assertIsNone(await off.read_fortune("小明", "凶", ["早睡早起"], ["熬夜"], ["考砸了"]))     # off by default
        ai = AIChatModule(FakeGateway(), {"ai_chat": {"fortune": True}}, store=store, client=client)
        self.assertIsNone(await ai.read_fortune("小明", "凶", ["早睡早起"], ["熬夜"], []))
        self.assertEqual(client.requests("chat"), [])
        client.answers["chat"].append("只是运气差了点，不是你的问题[表情:摸头]")
        self.assertEqual(await ai.read_fortune("小明", "凶", ["早睡早起", "喝一杯奶茶"], ["熬夜"], ["考砸了", "好笨"]),
                         "只是运气差了点，不是你的问题")
        request = client.requests("chat")[-1]["messages"]
        self.assertIn("你是「小莲」", request[0]["content"])
        self.assertIn("小明 刚抽了今日运势：凶。宜：早睡早起、喝一杯奶茶；忌：熬夜。", request[1]["content"])
        self.assertIn("- 考砸了\n- 好笨", request[1]["content"])
        client.answers["chat"].append(RuntimeError("余额不足"))
        self.assertIsNone(await ai.read_fortune("小明", "凶", ["早睡早起"], ["熬夜"], ["考砸了"]))

    async def test_she_knows_who_is_asking(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder)
        shutil.copy(root_path("styles", f"{DEFAULT_STYLE}.yaml"), folder)
        with open(os.path.join(folder, "桃子.yaml"), "w", encoding="utf-8") as f:
            f.write("name: 桃子\nsystem_prompt: 你是桃子。\ntalking_to@vip: 现在和你说话的是贵宾。\n")
        book = StyleBook(directory=folder)
        book.configure({"style": "桃子", "audiences": {"vip": ["A"]}})
        client, store = FakeDeepSeek(), ChatStore(":memory:")
        self.addCleanup(store.db.close)
        ai = AIChatModule(FakeGateway(), {"ai_chat": {"fortune": True}}, store=store, client=client)
        with book.scope(GROUP, audiences=["vip"]):
            await ai.read_fortune("小明", "凶", ["早睡早起"], ["熬夜"], ["考砸了"])
        self.assertEqual(client.requests("chat")[-1]["messages"][0]["content"], "你是桃子。\n\n现在和你说话的是贵宾。")


class FlipTest(SocialTestCase):
    async def test_this_weeks_active_members_weighted(self):
        rows = [{"member_openid": "B", "nickname": "小红", "n": 1000}, {"member_openid": "C", "nickname": "小刚", "n": 1},
                {"member_openid": "A", "nickname": "小明", "n": 5000}]
        self.make(activity=lambda group, since: rows)
        self.assertIn("小红", await self.say("A", "/翻牌子"))                 # 1000 messages against 1; never themself
        self.assertEqual(self.gw.buttons[-1], (GROUP, [[("我也要翻", "/翻牌子")]]))
        self.assertIn("今天已经翻过牌子啦，翻到的是 小红", await self.say("A", "/翻牌子"))
        self.now = START + timedelta(days=1)
        self.assertNotIn("已经翻过", await self.say("A", "/翻牌子"))

    async def test_admins_flip_as_often_as_they_like(self):
        rows = [{"member_openid": "B", "nickname": "小红", "n": 3}, {"member_openid": "C", "nickname": "小刚", "n": 3}]
        self.make(activity=lambda group, since: rows)
        for _ in range(4):
            flipped = await self.say("ADMIN", "/翻牌子")
            self.assertIn("📜", flipped)
            self.assertNotIn("已经翻过", flipped)
        await self.say("A", "/翻牌子")
        self.assertIn("今天已经翻过牌子啦", await self.say("A", "/翻牌子"))   # everyone else, once a day

    async def test_mentions_or_an_unmanaged_group(self):
        self.make()                                         # no activity data: not a managed group
        self.assertIn("@ 几个人", await self.say("A", "/翻牌子"))
        picked = await self.say("A", "/翻牌子", mentions=["C"])
        self.assertIn("小刚", picked)


class SpecialFlipTest(SocialTestCase):
    ROWS = [{"member_openid": "B", "nickname": "小红", "n": 1000}, {"member_openid": "C", "nickname": "小刚", "n": 1},
            {"member_openid": "A", "nickname": "小明", "n": 5000}]

    @staticmethod
    def special(table):
        return {"social": {"flip": {"special": table}}}

    async def test_their_chances_come_first_and_the_rest_is_the_usual_draw(self):
        config = self.special({"A": {"C": 60}})
        self.make(activity=lambda group, since: self.ROWS, config=config, rng=Roll(0.59))
        self.assertIn("小刚", await self.say("A", "/翻牌子"))       # within A's 60%, though 小刚 posted once
        self.make(activity=lambda group, since: self.ROWS, config=config, rng=Roll(0.61))
        self.assertIn("小红", await self.say("A", "/翻牌子"))       # the other 40%: by posts, without 小刚
        self.assertIn("小明", await self.say("B", "/翻牌子"))       # everyone else flips as before

    async def test_a_hundred_percent_always_comes_up(self):
        self.make(activity=lambda group, since: self.ROWS, config=self.special({"A": {"C": "100%"}}))
        for day in range(7):
            self.now = START + timedelta(days=day)
            self.assertIn("小刚", await self.say("A", "/翻牌子"))

    async def test_quiet_this_week_still_counts_but_not_after_leaving(self):
        rows = [{"member_openid": "B", "nickname": "小红", "n": 3}]
        self.make(activity=lambda group, since: rows, config=self.special({"A": {"D": 100, "E": 100}}),
                  nickname_of=lambda group, member: "小丁" if member == "D" else None)     # E has left
        for day in range(5):
            self.now = START + timedelta(days=day)
            self.assertIn("小丁", await self.say("A", "/翻牌子"))

    async def test_with_mentions_only_those_mentioned(self):
        self.make(config=self.special({"A": {"C": 100}}))      # an unmanaged group
        self.assertIn("小红", await self.say("A", "/翻牌子", mentions=["B"]))
        self.now += timedelta(days=1)
        self.assertIn("小刚", await self.say("A", "/翻牌子", mentions=["B", "C"]))

    def test_the_log_puts_a_nickname_to_every_id(self):
        names = {"A": "小明", "B": "小红"}
        with mock.patch.object(social_module, "_log") as log:
            self.make(config=self.special({"A": {"B": 60, "ZZZZZZZZZZ": 20}}), find_nickname=names.get)
        logged = "\n".join(str(call.args[0]) for call in log.info.call_args_list)
        self.assertIn("小明（A…） 翻到 小红（B…） 60%、？（ZZZZZZZZ…） 20%", logged)
        self.assertIn("1 个 ID 在管理的群里找不到", str(log.warning.call_args_list))
        with mock.patch.object(social_module, "_log") as log:
            self.make(config=self.special({"A": {"B": 60}}), find_nickname=names.get)
        log.warning.assert_not_called()

    def test_reading_the_tables(self):
        tables = read_flip_special({"A": {"B": 60, "C": "25%", "D": 0, "E": "很多", "A": 50, "F": -3, "G": ".nan"},
                                    "H": "不是名单", "I": {"B": 0}})
        self.assertEqual(tables, {"A": {"B": 60.0, "C": 25.0}})
        self.assertEqual(read_flip_special(None), {})
        self.assertEqual(read_flip_special(["A"]), {})


class ButtonsTest(unittest.IsolatedAsyncioTestCase):
    def make_api(self, refuse, refuse_inline=False):
        """QQ refusing every markdown message (`refuse`), or only a reply with buttons inside it."""
        calls = []

        async def post_group_message(**kwargs):
            calls.append(kwargs)
            if kwargs.get("msg_type") == 2 and (refuse or refuse_inline and kwargs["markdown"]["content"] != "点一下直接发送 👇"):
                raise RuntimeError("custom keyboard not enabled")
        return SimpleNamespace(post_group_message=post_group_message), calls

    async def test_buttons_go_in_the_reply(self):
        api, calls = self.make_api(refuse=False)
        await QQGateway(api).send("G", "运势", reply_to="m1", buttons=[[("我也抽", "/今日运势"), ("抓人", "/抓 ")]])
        self.assertEqual([(c["msg_type"], c["msg_seq"], c["markdown"]["content"]) for c in calls], [(2, 1, "运势")])
        buttons = calls[0]["keyboard"]["content"]["rows"][0]["buttons"]
        self.assertEqual([(b["action"]["data"], b["action"]["enter"]) for b in buttons],
                         [("/今日运势", True), ("/抓", False)])            # "/抓 " is only typed in

    def test_the_reply_reads_as_written(self):
        self.assertEqual(as_markdown("📊 周六几点开？\n1. 19点 · 2 票：小_明\n- 备注 <地址>\n\n# 3 *票*\n"),
                         "📊 周六几点开？  \n\u200b1. 19点 · 2 票：小＿明  \n\u200b- 备注 ＜地址＞  \n\u200b  \n"
                         "\u200b# 3 ＊票＊")

    async def test_if_qq_wont_take_them_inside_they_go_under_it_for_an_hour(self):
        api, calls = self.make_api(refuse=False, refuse_inline=True)
        gateway = QQGateway(api)
        await gateway.send("G", "运势", reply_to="m1", buttons=[[("我也抽", "/今日运势")]])
        await gateway.send("G", "运势", reply_to="m2", buttons=[[("我也抽", "/今日运势")]])
        self.assertEqual([(c["msg_type"], c.get("msg_id")) for c in calls], [(2, "m1"), (0, "m1"), (2, "m1"),
                                                                             (0, "m2"), (2, "m2")])
        gateway.inline_off_until = 0                        # the hour is up: it tries inside the reply again
        calls.clear()
        await gateway.send("G", "运势", reply_to="m3", buttons=[[("我也抽", "/今日运势")]])
        self.assertEqual((calls[0]["msg_type"], calls[0]["markdown"]["content"]), (2, "运势"))
        calls.clear()
        await QQGateway(api, buttons_inline=False).send("G", "运势", reply_to="m4", buttons=[[("我也抽", "/今日运势")]])
        self.assertEqual([c["msg_type"] for c in calls], [0, 2])          # config.yaml: buttons_inline: false
        calls.clear()
        await QQGateway(api).send("G", '<qqbot-at-user id="X" /> 到你了', reply_to="m5", buttons=[[("好", "/好")]])
        self.assertEqual([c["msg_type"] for c in calls], [0, 2])          # an @ stays in a text message

    def test_a_button_that_changes_things_asks_first(self):
        rows = QQGateway.keyboard([[("确认移出 #3", "/确认移出 3", "确定把名单 #3 的 2 人移出群吗？"),
                                    ("取消 #3", "/取消移出 3")]])["content"]["rows"]
        confirm, cancel = rows[0]["buttons"]
        self.assertEqual((confirm["action"]["modal"], confirm["render_data"]["style"]),
                         ({"content": "确定把名单 #3 的 2 人移出群吗？"}, 3))
        self.assertEqual(("modal" in cancel["action"], cancel["render_data"]["style"]), (False, 1))

    async def test_the_games_offer_the_next_move(self):
        gw = FakeGateway()

        async def play(module, handle, text):
            await getattr(module, handle)(FakeMessage(GROUP, "A", "小明", text, content=text))
            return gw.buttons[-1][1]

        blackjack = BlackjackModule(None, gateway=gw)
        self.assertEqual(await play(blackjack, "handle_blackjack", "/21点"), [[("抽卡", "/抽卡"), ("停止", "/停止")]])
        self.assertEqual(await play(blackjack, "handle_blackjack", "/停止"), [[("再来一局", "/21点")]])
        self.assertEqual(await play(blackjack, "handle_blackjack", "/抽卡"), [[("再来一局", "/21点")]])   # no game
        self.assertEqual(await play(DiceModule(None, gateway=gw), "handle_dice", "/摇骰子"), [[("我也要摇", "/摇骰子")]])
        self.assertEqual(await play(CrazyTalkModule(None, gateway=gw), "handle_crazy_talk", "/发癫 小红"),
                         [[("再来一条", "/发癫 小红")]])

    async def test_refused_buttons_fall_back_to_text_for_a_while(self):
        api, calls = self.make_api(refuse=True)
        gateway = QQGateway(api)
        await gateway.send("G", "运势", reply_to="m1", buttons=[[("我也抽", "/今日运势")]])
        await gateway.send("G", "运势", reply_to="m2", buttons=[[("我也抽", "/今日运势")]])
        self.assertEqual([c["msg_type"] for c in calls], [2, 0, 2, 0])      # tried both ways once, then text only
        api, calls = self.make_api(refuse=False)
        await QQGateway(api, buttons=False).send("G", "运势", reply_to="m1", buttons=[[("我也抽", "/今日运势")]])
        self.assertEqual([c["msg_type"] for c in calls], [0])               # config.yaml: buttons: false


class UndeliverableReplyTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_refused_reply_is_followed_by_a_short_notice(self):
        calls = []

        async def post_group_message(**kwargs):
            calls.append(kwargs)
            if kwargs["content"] == "被拦下的话":
                raise RuntimeError("40054017 消息内容不合规")
        gateway = QQGateway(SimpleNamespace(post_group_message=post_group_message))
        with self.assertRaises(RuntimeError):                  # the caller still hears about it
            await gateway.send("G", "被拦下的话", reply_to="m1")
        self.assertEqual([(c["content"], c.get("msg_seq")) for c in calls],
                         [("被拦下的话", 1), ("被拦下的话", None), ("刚才那条回复没发出去，可能被 QQ 拦下了，换个说法再试试～", 2)])
        calls.clear()
        with self.assertRaises(RuntimeError):                  # no message to reply to: no notice
            await gateway.send("G", "被拦下的话")
        self.assertEqual(len(calls), 1)


class DigestCommentTest(ManagerTestCase):
    async def test_the_digest_ends_with_a_comment(self):
        self.make(FakeGateway())
        await self.post("A", "大家晚上好，今天聊点什么")

        async def commenter(digest):
            self.assertIn("本周群聊小报", digest)
            return "这周大家都很能聊！"
        self.assertNotIn("💭", await self.mgr.digest_with_comment(GROUP))
        self.mgr.digest_commenter = commenter
        self.assertTrue((await self.mgr.digest_with_comment(GROUP)).endswith("\n\n💭 小莲说：这周大家都很能聊！"))

    async def test_ai_writes_it_in_character(self):
        client, store = FakeDeepSeek(), ChatStore(":memory:")
        self.addCleanup(store.db.close)
        ai = AIChatModule(FakeGateway(), {}, store=store, client=client)
        client.answers["chat"].append("“下周继续热闹呀～”")
        self.assertEqual(await ai.comment_on_digest("📮 本周群聊小报\n💬 有效发言 12 条"), "下周继续热闹呀～")
        request = client.requests("chat")[-1]["messages"]
        self.assertIn("你是「小莲」", request[0]["content"])
        self.assertIn("有效发言 12 条", request[1]["content"])
        client.answers["chat"].append(RuntimeError("余额不足"))
        self.assertIsNone(await ai.comment_on_digest("小报"))               # the digest goes out without it


class RollTest(TeamGameTestCase):
    ROLL = """
  - name: 掷骰
    effect: roll
    from: [存活, 死亡]
    only: [存活]
"""

    def rules_file(self, text):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder)
        path = os.path.join(folder, "teams.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    async def test_a_captain_rolls_each_members_fate(self):
        self.make(rules_path=self.rules_file(shipped_rules() + self.ROLL))
        self.teams.rng = random.Random(3)
        await self.say("CA", "/抓 @M1 @M2")
        reply = await self.say("CA", "/掷骰 @M1 @M2")
        lines = reply.split("\n")
        self.assertEqual(len(lines), 2)                                     # one line per member
        for line, member in zip(lines, ("M1", "M2")):
            status = self.team("CA")[member]["生死"]
            self.assertEqual(line, f"🎲 阿队 为 {'小一' if member == 'M1' else '小二'} 掷出了「{status}」！")
        self.assertIn("不在你的队伍里", await self.say("CB", "/掷骰 @M1"))

    def test_roll_needs_a_from_list(self):
        with self.assertRaises(ValueError) as caught:
            load_rules(self.rules_file(shipped_rules() + self.ROLL.replace("    from: [存活, 死亡]\n", "")))
        self.assertIn("from", str(caught.exception))
        with self.assertRaises(ValueError):
            load_rules(self.rules_file(shipped_rules() + self.ROLL.replace("from: [存活, 死亡]", "from: [飞走]")))


class StyleLinesTest(unittest.TestCase):
    def test_every_social_line_and_pool_exists(self):
        base = STYLES.styles[DEFAULT_STYLE]
        for key in ("poll.title", "poll.closed_title", "poll.option", "poll.option_empty", "fortune.button",
                    "birthday.wish", "flip.result"):
            self.assertIsNotNone(base.own(f"social.{key}"), key)
        for pool in ("levels", "good", "bad", "comments"):
            self.assertGreaterEqual(len(base.own(f"social.fortune.{pool}")), 2, pool)
        self.assertIsNotNone(base.own("manager.digest.comment"))
        self.assertIsNotNone(base.own("games.teams.rolled"))


if __name__ == "__main__":
    unittest.main()
