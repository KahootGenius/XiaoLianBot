"""海龟汤 puzzles in a set of their own (`set:` in the puzzle file), opened with /海龟汤 and the set's name."""
import io
import os
import random
import shutil
import tempfile
import unittest
import unittest.mock
from datetime import timedelta
from modules.fun import soup
from modules.help import HelpModule
from tests.test_manager import GROUP, FakeGateway
from tests.test_social import BALD, BUS, FISH, RIGHT, Host, SocialTestCase

DARK = [soup.Puzzle(f"dark{n}", f"悬疑{n}", f"悬疑汤面{n}。为什么？", f"悬疑汤底{n}。", ("关键点",), (), "悬疑") for n in (1, 2)]
SWEET = soup.Puzzle("sweet", "甜的", "甜的汤面。为什么？", "甜的汤底。", ("关键点",), (), "甜口")
ENTRY = "- code: {code}\n  title: 题目\n  surface: 汤面\n  truth: 汤底\n  points: [关键点]\n"


class SetFileTest(unittest.TestCase):
    def load(self, text):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder)
        path = os.path.join(folder, "soups.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return soup.load(path)

    def test_a_puzzle_can_belong_to_a_set(self):
        puzzles, problems = self.load(ENTRY.format(code="plain") + ENTRY.format(code="a") + "  set: 悬疑\n"
                                      + ENTRY.format(code="b") + "  set: Dark2\n" + ENTRY.format(code="c") + "  set: 悬疑\n")
        self.assertEqual(([p.set for p in puzzles.values()], problems), (["", "悬疑", "Dark2", "悬疑"], []))
        self.assertEqual(soup.sets(puzzles), ["悬疑", "Dark2"])                   # in the file's order, each once
        self.assertEqual((list(soup.of_set(puzzles)), list(soup.of_set(puzzles, "悬疑")), soup.of_set(puzzles, "没有")),
                         (["plain"], ["a", "c"], {}))
        only_sets = {code: puzzle for code, puzzle in puzzles.items() if puzzle.set}
        self.assertEqual(list(soup.of_set(only_sets)), ["a", "b", "c"])           # nothing unnamed: /海龟汤 opens any
        self.assertEqual(soup.sets(soup.load()[0]), [])                          # the shipped puzzles are all /海龟汤's

    def test_a_set_name_that_cannot_follow_the_command_is_refused(self):
        for name in ("悬 疑", "悬疑/2", "悬疑@2", "一二三四五六七八九十十一二", "提示", "结束", '""', "~"):
            puzzles, problems = self.load(ENTRY.format(code="a") + f"  set: {name}\n" + ENTRY.format(code="ok"))
            self.assertEqual(list(puzzles), ["ok"], name)
            self.assertIn("第 1 题（a）：set 是接在 /海龟汤 后面的名字", problems[0])
        self.assertEqual(self.load(ENTRY.format(code="a") + "  set: 一二三四五六七八九十十一\n")[1], [])       # twelve is the most

    def test_the_check_command_shows_each_set(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder)
        path = os.path.join(folder, "soups.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(ENTRY.format(code="plain") + ENTRY.format(code="a") + "  set: 悬疑\n")
        out = io.StringIO()
        with unittest.mock.patch.object(soup, "puzzles_file", lambda: path):
            self.assertEqual(soup.main(out), 0)
        self.assertEqual(out.getvalue(), "plain  题目  关键点 1 · 提示 0\na  题目  关键点 1 · 提示 0  /海龟汤悬疑\n2 puzzles ready, 0 problems.\n")


class SetCommandTest(SocialTestCase):
    def cook(self, *puzzles):
        self.make(soups={puzzle.code: puzzle for puzzle in puzzles}, rng=random.Random(2))
        self.host = self.social.soup_host = Host()

    async def open_and_solve(self, command):
        self.now += timedelta(minutes=2)                    # unhurried: guesses have a speed limit
        opened = await self.say("A", command)
        self.host.verdicts = [RIGHT]
        await self.say("A", "/猜 就是这样")
        return opened

    async def test_each_command_opens_its_own_puzzles(self):
        self.cook(BALD, BUS, *DARK, SWEET)
        self.assertEqual(self.social.soup_sets, ["悬疑", "甜口"])
        for _ in range(4):                                                      # /海龟汤 never strays into a set
            self.assertRegex(await self.open_and_solve("/海龟汤"), "淋不湿的头发|终点站")
            self.assertEqual(self.gw.buttons[-1], (GROUP, [[("再来一碗", "/海龟汤")]]))
        opened = [await self.open_and_solve(command) for command in ("/海龟汤悬疑", "/海龟汤 悬疑", "/海龟汤悬疑", "/海龟汤 悬疑")]
        self.assertTrue(all("悬疑汤面" in text for text in opened))
        self.assertEqual(self.gw.buttons[-1], (GROUP, [[("再来一碗", "/海龟汤悬疑")]]))     # another of its kind
        self.assertNotEqual(opened[0], opened[1])                               # both of the set before one comes again,
        self.assertNotEqual(opened[2], opened[3])
        self.assertNotEqual(opened[1], opened[2])                               # and not the same one twice running
        self.assertIn("甜的汤面", await self.say("A", "/海龟汤甜口"))
        await self.say("A", "/海龟汤 结束")                                       # given up: the button offers the same kind
        self.assertEqual(self.gw.buttons[-1], (GROUP, [[("再来一碗", "/海龟汤甜口")]]))

    async def test_a_sets_round_is_its_own(self):
        self.cook(BALD, BUS, *DARK)
        await self.open_and_solve("/海龟汤")
        await self.open_and_solve("/海龟汤悬疑")
        await self.open_and_solve("/海龟汤悬疑")
        self.assertEqual(len(self.store.soup_played(GROUP)), 3)
        await self.open_and_solve("/海龟汤悬疑")                                  # the set starts over: its two are forgotten,
        played = self.store.soup_played(GROUP)
        self.assertEqual((len(played), sum(1 for code in played if code.startswith("dark"))), (2, 1))      # the other isn't
        first = next(code for code in played if not code.startswith("dark"))
        opened = await self.say("A", "/海龟汤")
        self.assertIn({"bald": "终点站", "bus": "淋不湿的头发"}[first], opened)       # so /海龟汤 still knows what it has had

    async def test_while_one_is_open_a_sets_command_shows_it_and_other_words_are_not_hers(self):
        self.cook(BALD, *DARK)
        await self.say("A", "/海龟汤")
        self.assertIn("淋不湿的头发", await self.say("B", "/海龟汤悬疑"))           # one puzzle at a time, whatever its set
        self.assertEqual(await self.say("B", "/海龟汤别的"), "")                   # not a set: not this module's command
        self.assertEqual(await self.say("B", "/海龟汤提示"), "")
        self.host.verdicts = [RIGHT]
        await self.say("A", "/猜 光头")
        self.assertIn("悬疑汤面", await self.say("B", "/海龟汤悬疑 随便写点什么"))
        self.assertIn("要个提示", [label for label, _ in self.gw.buttons[-1][1][0]])

    def test_help_names_the_sets_while_the_game_is_on(self):
        on = HelpModule(None, gateway=FakeGateway(), extras=("soup",), soup_sets=("/海龟汤悬疑", "/海龟汤甜口"))
        self.assertIn("/海龟汤 开一碗推理题  /问 是非题  /猜 你的答案\n/海龟汤悬疑  /海龟汤甜口 换一种口味的汤\n", on._build_help_text() + "\n")
        self.assertNotIn("换一种口味", HelpModule(None, extras=("soup",))._build_help_text())
        self.assertNotIn("换一种口味", HelpModule(None, extras=(), soup_sets=("/海龟汤悬疑",))._build_help_text())


if __name__ == "__main__":
    unittest.main()
