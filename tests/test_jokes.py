"""讲笑话: the joke file, which joke comes next, and the two commands."""
import asyncio
import io
import os
import random
import shutil
import tempfile
import unittest
import unittest.mock
from datetime import timedelta
from core import occasions
from core.config import root_path
from core.occasions import Occasion, parse_when
from core.style import STYLES, DEFAULT_STYLE
from modules.fun import jokes
from modules.fun.jokes import Joke, Teller
from modules.help import HelpModule
from modules.fun.social import module as social_module
from tests.test_ai_chat import AIChatTestCase
from tests.test_manager import GROUP, FakeGateway
from tests.test_social import Roll, SocialTestCase

ASKED = [Joke("为什么甲？", "因为乙。"), Joke("为什么丙？", "因为丁。"), Joke("一句话讲完的。")]
TAILS = STYLES.styles[DEFAULT_STYLE].lookup("social.joke.tail")


class JokeFileTest(unittest.TestCase):
    def load(self, text):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder)
        path = os.path.join(folder, "jokes.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path, jokes.load(path)

    def test_the_shipped_jokes_load_and_their_days_exist(self):
        found, problems = jokes.load(root_path("prompts", "jokes.yaml"))
        self.assertEqual((len(found), problems), (14, []))
        self.assertEqual(sum(1 for joke in found if not joke.answer), 3)
        days = {day.name for day in occasions.load(root_path("prompts", "occasions.yaml"))[0]}
        self.assertEqual({joke.day for joke in found if joke.day}, {"春节", "愚人节"})
        self.assertLessEqual({joke.day for joke in found if joke.day}, days)

    def test_a_joke_written_wrong_is_left_out_and_named(self):
        _, (found, problems) = self.load("""jokes:
  - q: 为什么甲？
    a: 因为乙。
  - text: "  一句话，  多余的空格收掉  "
    day: 春节
  - q: 只有问题
  - a: 只有答案
  - q: 都写了
    a: 答案
    text: 还有一句
  - 只是一行字
  - q: 为什么甲？
    a: 换了个答案
  - text: ""
  - text: %s
""" % ("长" * 201))
        self.assertEqual(found, [Joke("为什么甲？", "因为乙。"), Joke("一句话， 多余的空格收掉", "", "春节")])
        for problem, phrase in zip(problems, ["第 3 个笑话要写成 q 和 a", "第 4 个笑话要写成", "第 5 个笑话要写成", "第 6 个笑话要写成",
                                              "第 7 个笑话和前面的重复了", "第 8 个笑话：text 不能是空的", "第 9 个笑话：text 不能是空的，也不能超过 200"]):
            self.assertIn(phrase, problem)
        self.assertEqual(len(problems), 7)

    def test_no_file_or_an_unreadable_one_means_no_jokes(self):
        self.assertEqual(jokes.load("/nonexistent/jokes.yaml"), ([], []))
        self.assertEqual(self.load("")[1], ([], []))
        self.assertEqual(self.load("jokes: 哈哈")[1][1], ["jokes.yaml 的 jokes 要写成列表，每个笑话以「- q:」或「- text:」开头"])
        self.assertEqual(len(self.load("jokes: [")[1][1]), 1)

    def test_the_check_command(self):
        out = io.StringIO()
        self.assertEqual(jokes.main(out, root_path("prompts", "jokes.yaml"), days={"春节", "愚人节"}), 0)
        self.assertIn("为什么数学书总是不开心？ —— 因为它的问题太多了。\n", out.getvalue())
        self.assertIn("【春节】为什么过年的时候饺子最紧张？", out.getvalue())
        self.assertIn("14 jokes ready, 0 problems.", out.getvalue())
        out = io.StringIO()
        self.assertEqual(jokes.main(out, root_path("prompts", "jokes.yaml"), days={"春节"}), 1)      # a day nobody defined
        self.assertIn("的 day「愚人节」不是 occasions.yaml 里的日子", out.getvalue())


class TellerTest(unittest.TestCase):
    def test_every_joke_once_before_any_comes_again_and_never_twice_running(self):
        pool = [Joke(f"笑话{n}") for n in range(6)]
        for seed in range(30):
            teller = Teller(pool, random.Random(seed))
            told = [teller.next("G").question for _ in range(18)]
            for start in (0, 6, 12):
                self.assertEqual(sorted(told[start:start + 6]), sorted(j.question for j in pool), seed)
            self.assertTrue(all(a != b for a, b in zip(told, told[1:])), seed)
        teller = Teller(pool, random.Random(1))
        self.assertNotEqual([teller.next("G").question for _ in range(6)], [teller.next("H").question for _ in range(6)])
        for seed in range(30):                                                  # one group's round isn't another's
            teller = Teller(pool, random.Random(seed))
            [teller.next("G") for _ in range(3)]
            self.assertEqual(sorted(teller.next("H").question for _ in range(6)), sorted(j.question for j in pool), seed)
        self.assertIsNone(Teller([], random.Random(1)).next("G"))
        self.assertEqual(Teller(pool[:1], random.Random(1)).next("G"), Teller(pool[:1], random.Random(1)).next("G"))

    def test_a_days_joke_is_told_only_then_and_first(self):
        pool = [Joke("平常一"), Joke("平常二"), Joke("过年的", day="春节"), Joke("愚人的", day="愚人节")]
        teller = Teller(pool, random.Random(3))
        self.assertEqual(sorted(teller.next("G").question for _ in range(6)), ["平常一", "平常一", "平常一", "平常二", "平常二", "平常二"])
        teller.next("G")                                                        # mid-round,
        self.assertEqual(teller.next("G", ["春节"]).question, "过年的")           # it still comes first
        later = [teller.next("G", ["春节"]).question for _ in range(9)]           # then once a round, like the rest
        self.assertEqual((later.count("过年的"), later.count("愚人的")), (3, 0))
        self.assertTrue(all(a != b for a, b in zip(later, later[1:])))
        self.assertIsNone(Teller(pool[2:], random.Random(1)).next("G"))            # nothing fits an ordinary day


class JokeCommandTest(SocialTestCase):
    def make_with(self, pool=tuple(ASKED), **kwargs):
        self.make(**kwargs)
        self.social.teller = Teller(pool, random.Random(5))

    async def test_she_asks_and_whoever_asks_why_gets_the_answer(self):
        self.make_with(ASKED[:2])
        asked = await self.say("A", "/讲笑话")
        joke = next(j for j in ASKED if asked == f"🎤 听好了：{j.question}")
        self.assertEqual(self.gw.buttons[-1], (GROUP, [[("为什么？", "/为什么")]]))
        answer = (await self.say("B", "/为什么")).split("\n")                      # anyone may ask
        self.assertEqual((answer[0], answer[1] in TAILS, len(answer)), (joke.answer, True, 2))
        self.assertEqual(self.gw.buttons[-1], (GROUP, [[("再来一个", "/讲笑话")]]))
        self.assertEqual(await self.say("A", "/为什么"), "现在没有讲到一半的笑话哦。想听就发 /讲笑话")
        other = await self.say("A", "/笑话")                                      # the other word, the other joke
        self.assertNotEqual(other, asked)
        self.assertIn(next(j for j in ASKED if j.question in other).answer, await self.say("A", "/讲笑话 答案"))

    async def test_a_new_joke_replaces_the_one_left_unanswered_and_an_answer_waits_half_an_hour(self):
        self.make_with(ASKED[:2])
        first = await self.say("A", "/讲笑话")
        second = await self.say("A", "/讲笑话")
        self.assertIn(next(j for j in ASKED if j.question in second).answer, await self.say("A", "/为什么"))
        await self.say("A", "/讲笑话")
        self.now += timedelta(minutes=30)
        self.assertNotEqual(await self.say("A", "/为什么"), "现在没有讲到一半的笑话哦。想听就发 /讲笑话")
        await self.say("A", "/讲笑话")
        self.now += timedelta(minutes=30, seconds=1)
        self.assertEqual(await self.say("A", "/为什么"), "现在没有讲到一半的笑话哦。想听就发 /讲笑话")
        self.assertNotEqual(first, second)

    async def test_a_joke_without_an_answer_is_told_whole(self):
        self.make_with(ASKED[2:] + ASKED[:1])
        told = [await self.say("A", "/讲笑话") for _ in range(2)]
        self.assertIn("🎤 一句话讲完的。", told)
        if told[1] == "🎤 一句话讲完的。":                                          # it left no answer waiting,
            self.assertEqual(self.gw.buttons[-1], (GROUP, [[("再来一个", "/讲笑话")]]))
            self.assertIn("没有讲到一半的笑话", await self.say("A", "/为什么"))      # not even the one before it

    async def test_groups_hear_their_own_and_a_days_joke_comes_on_its_day(self):
        self.make_with(ASKED[:2] + [Joke("过年的", "红包。", day="春节")])
        await self.say("A", "/讲笑话")
        self.assertIn("没有讲到一半的笑话", await self.say("A", "/为什么", group="G2"))
        self.assertNotIn("过年的", "".join([await self.say("A", "/讲笑话") for _ in range(4)]))
        self.social.days = lambda: [Occasion("春节", (parse_when("农历 正月初一"),))]
        self.assertEqual(await self.say("A", "/讲笑话"), "🎤 听好了：过年的")

    async def test_without_jokes_it_says_so(self):
        self.make_with(())
        self.assertEqual(await self.say("A", "/讲笑话"), "现在没有笑话可讲哦。")
        self.make(config={"social": {"joke": False}})
        self.assertEqual((self.social.teller.jokes, await self.say("A", "/讲笑话")), ((), "现在没有笑话可讲哦。"))
        self.make()                                                              # on unless switched off
        self.assertEqual(len(self.social.teller.jokes), 14)

    def test_help_lists_it_only_while_there_are_jokes(self):
        on = HelpModule(None, gateway=FakeGateway(), extras=("joke",))
        self.assertIn("\n/讲笑话 听我讲一个  /为什么 问上一个笑话的答案", on._build_help_text())
        self.assertIn(("讲笑话", "/讲笑话"), on.buttons()[1])
        off = HelpModule(None, gateway=FakeGateway(), extras=())
        self.assertNotIn("讲笑话", off._build_help_text())
        self.assertNotIn(("讲笑话", "/讲笑话"), off.buttons()[1])


class FreshReadingTest(unittest.TestCase):
    def test_what_deepseek_wrote_is_told_only_when_it_is_a_joke(self):
        known = ["为什么甲？"]
        self.assertEqual(jokes.fresh({"q": " 为什么 巨人不坐电梯？ ", "a": "因为他一进去就到顶了。"}, known),
                         Joke("为什么 巨人不坐电梯？", "因为他一进去就到顶了。"))
        self.assertEqual(jokes.fresh({"q": "他怎么回答?", "a": "不答"}, known), Joke("他怎么回答?", "不答"))
        for verdict in ({}, {"q": "为什么乙？"}, {"a": "因为丙。"}, {"q": ["为什么乙？"], "a": "因为丙。"}, {"q": "为什么乙？", "a": 3},
                        {"q": "为什么会是乙", "a": "因为丙。"},                   # not a question
                        {"q": "为什么甲？", "a": "因为丙。"}, {"q": "为什么，甲 ?", "a": "因为丙。"},     # told before, punctuation aside
                        {"q": "为什么乙？", "a": "丙"}, {"q": "乙呢？", "a": "因为丙。"},           # too short
                        {"q": "为什么" + "乙" * 57 + "？", "a": "因为丙。"}, {"q": "为什么乙？", "a": "丙" * 81},
                        {"q": "为什么乙？", "a": "因为丙。\n还有丁。"}, {"q": "为什么@小明 乙？", "a": "因为丙。"}):
            self.assertIsNone(jokes.fresh(verdict, known), verdict)
        self.assertIsNotNone(jokes.fresh({"q": "为什么" + "乙" * 56 + "？", "a": "丙" * 80}, known))      # at the limits


class FreshJokeTest(SocialTestCase):
    """Some of her jokes are made up by AI chat on the spot; the rest, and every fallback, come from the file."""

    POOL = [Joke(f"为什么第{n}个？", f"因为{n}。") for n in range(8)] + [Joke("一句话的。"), Joke("过年的？", "红包。", day="春节")]
    MADE = {"q": "为什么现写的最新鲜？", "a": "因为刚出锅。"}

    def make_with(self, share, roll=0.0, answers=(), pool=None):
        self.make(rng=Roll(roll))
        self.social.teller = Teller(self.POOL if pool is None else pool, random.Random(5))
        self.asked, queue = [], list(answers)

        async def writer(examples, day, told):
            self.asked.append((examples, day, told))
            answer = queue.pop(0) if queue else self.MADE
            if isinstance(answer, Exception):
                raise answer
            return answer

        self.social.joke_writer, self.social.fresh_share = writer, share

    async def test_a_made_up_joke_is_asked_and_answered_like_any_other(self):
        self.make_with(100)
        self.assertEqual(await self.say("A", "/讲笑话"), "🎤 听好了：为什么现写的最新鲜？")
        self.assertEqual(self.gw.buttons[-1], (GROUP, [[("为什么？", "/为什么")]]))
        self.assertEqual((await self.say("B", "/为什么")).split("\n")[0], "因为刚出锅。")
        examples, day, told = self.asked[0]
        self.assertEqual((len(examples), day, told), (6, "", []))
        self.assertLessEqual(set(examples), {(joke.question, joke.answer) for joke in self.POOL[:8]})      # none without an answer, none of another day

    async def test_she_is_told_what_she_made_up_lately_and_a_repeat_falls_back_on_the_file(self):
        self.make_with(100, answers=[self.MADE, self.MADE, {"q": "为什么第二个也新鲜？", "a": "因为也刚出锅。"}])
        await self.say("A", "/讲笑话")
        from_file = await self.say("A", "/讲笑话")                                # the same one again: not told twice
        self.assertIn(from_file.removeprefix("🎤 听好了：").removeprefix("🎤 "), [joke.question for joke in self.POOL])
        self.assertEqual(self.asked[1][2], ["为什么现写的最新鲜？"])
        self.assertEqual(await self.say("A", "/讲笑话"), "🎤 听好了：为什么第二个也新鲜？")
        await self.say("A", "/讲笑话", group="G2")
        self.assertEqual(self.asked[3][2], [])                                   # another group hasn't heard them

    async def test_whatever_goes_wrong_a_joke_from_the_file_is_told(self):
        slow = asyncio.Event()

        async def never(examples, day, told):
            await slow.wait()

        self.make_with(100, answers=[None, {"q": "不是问题", "a": "没用"}, RuntimeError("超时"), {"q": self.POOL[0].question, "a": "抄的。"}])
        with self.assertLogs(social_module._log, "WARNING") as logged:
            told = [await self.say("A", "/讲笑话") for _ in range(4)]
            self.assertNotIn("抄的", await self.say("A", "/为什么"))                # the file's joke isn't hers to retell
            self.social.joke_writer = never
            with unittest.mock.patch.object(social_module, "JOKE_SECONDS", 0.01):
                told.append(await self.say("A", "/讲笑话"))
        known = [joke.question for joke in self.POOL]
        self.assertTrue(all(reply.removeprefix("🎤 听好了：").removeprefix("🎤 ") in known for reply in told), told)
        self.assertEqual(len(set(told)), 5)                                      # and the file's round goes on
        self.assertEqual(len(logged.output), 2)                                  # the failure and the wait, not the unusable ones

    async def test_only_the_share_of_turns_that_gets_one(self):
        self.make_with(30, roll=0.2999)
        self.assertEqual(await self.say("A", "/讲笑话"), "🎤 听好了：为什么现写的最新鲜？")
        self.make_with(30, roll=0.30)
        self.assertNotIn("现写", await self.say("A", "/讲笑话"))
        self.assertEqual(self.asked, [])
        self.make_with(0, roll=0.0)
        self.assertNotIn("现写", await self.say("A", "/讲笑话"))
        self.make_with(100, pool=self.POOL[:2] + self.POOL[8:])                   # two to show: no manner to follow
        self.assertNotIn("现写", await self.say("A", "/讲笑话"))
        self.assertEqual(self.asked, [])
        self.make_with(100, pool=[])                                             # and no jokes, no joking
        self.assertEqual((await self.say("A", "/讲笑话"), self.asked), ("现在没有笑话可讲哦。", []))

    async def test_on_a_special_day_the_days_jokes_show_the_manner_too(self):
        self.make_with(100, pool=self.POOL[:2] + self.POOL[9:])
        self.social.days = lambda: [Occasion("春节", (parse_when("农历 正月初一"),)), Occasion("别的", (parse_when("01-01"),))]
        self.assertEqual(await self.say("A", "/讲笑话"), "🎤 听好了：为什么现写的最新鲜？")
        examples, day, _ = self.asked[0]
        self.assertEqual((day, ("过年的？", "红包。") in examples, len(examples)), ("春节", True, 3))


class JokeWriterTest(AIChatTestCase):
    EXAMPLES = [("为什么甲？", "因为乙。"), ("为什么丙？", "因为丁。")]

    async def test_only_the_examples_the_day_and_her_latest_go_to_deepseek(self):
        self.make()
        self.assertEqual(self.ai.fresh_jokes, 30)                                # on unless config.yaml says otherwise
        self.assertEqual(await self.ai.write_joke(self.EXAMPLES), {"q": "为什么现写的笑话最新鲜？", "a": "因为刚出锅。"})
        request = self.ai_client.requests("joke")[0]
        self.assertEqual((len(request["messages"]), request["temperature"]), (1, 1.3))      # no persona, room to play
        prompt = request["messages"][0]["content"]
        self.assertIn("- 问：为什么甲？ 答：因为乙。\n- 问：为什么丙？ 答：因为丁。", prompt)
        self.assertNotIn("今天是", prompt)
        self.assertNotIn("刚讲过", prompt)
        await self.ai.write_joke(self.EXAMPLES, "春节", ["为什么戊？", "为什么己？"])
        prompt = self.ai_client.requests("joke")[1]["messages"][0]["content"]
        self.assertIn("今天是春节，可以写一个和这一天有关的。\n下面这些你刚讲过，换一个不同的点子：\n- 为什么戊？\n- 为什么己？", prompt)

    async def test_what_cannot_be_used_is_none(self):
        self.make()
        self.ai_client.answers["joke"] += ["我想不出来", RuntimeError("断了"), '{"q": "为什么？", "a": "作为一个AI，我不能。"}']
        self.assertEqual(await self.ai.write_joke(self.EXAMPLES), {})             # not JSON: nothing to read
        with self.assertLogs(level="WARNING"):
            self.assertIsNone(await self.ai.write_joke(self.EXAMPLES))
        with unittest.mock.patch.object(type(self.ai), "_never_say", staticmethod(lambda style: ["作为一个AI"])):
            self.assertIsNone(await self.ai.write_joke(self.EXAMPLES))            # a phrase the style never says
        self.assertIsNone(await self.ai.write_joke([]))
        calls = len(self.ai_client.requests("joke"))
        self.make(fresh_jokes=0)
        self.assertIsNone(await self.ai.write_joke(self.EXAMPLES))
        self.make(fresh_jokes="很多")
        self.assertEqual((self.ai.fresh_jokes, await self.ai.write_joke(self.EXAMPLES)), (0, None))
        self.make(fresh_jokes=250)
        self.assertEqual((self.ai.fresh_jokes, len(self.ai_client.requests("joke")), calls), (100, 0, 3))


if __name__ == "__main__":
    unittest.main()
