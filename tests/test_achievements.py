import os
import tempfile
import unittest
from datetime import datetime
from modules.manager import achievements
from modules.manager.achievements import Badge, counter_increments, load_badges
from modules.manager.classifier import judge


class BadgeFileTest(unittest.TestCase):
    def load(self, text):
        fd, path = tempfile.mkstemp(suffix=".yaml")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        self.addCleanup(os.remove, path)
        return load_badges(path)

    def test_shipped_file_loads(self):
        codes = [b.code for b in achievements.BADGES]
        self.assertEqual(len(codes), 11)
        self.assertEqual(len(set(codes)), 11)
        self.assertFalse(achievements.BY_CODE["first_words"].announce)
        self.assertEqual(achievements.BY_CODE["streak_7"].streak, 7)

    def test_commented_examples_work_once_uncommented(self):
        with open(achievements.BADGES_FILE, encoding="utf-8") as f:
            head, _, examples = f.read().partition("# 下面是可以直接启用的例子")
        uncommented = [line[2:] if line.startswith("# ") else line.lstrip("#")
                       for line in examples.splitlines()[1:]]
        badges = self.load(head + "\n" + "\n".join(uncommented))
        self.assertEqual([b.code for b in badges[-3:]], ["weekend_warrior", "essayist", "regular_100"])

    def test_a_new_badge_needs_only_yaml(self):
        badges = self.load("- {code: weekend_warrior, icon: x, name: 周末战士, desc: 周末 50 次,"
                           " counter: weekend, at_least: 50}")
        self.assertTrue(badges[0].met({"weekend": 50}, streak=0, comeback=False))
        self.assertFalse(badges[0].met({"weekend": 49}, streak=0, comeback=False))

    def test_mistakes_are_caught_at_startup(self):
        mistakes = {
            "缺少": "- {code: a, icon: x, name: n, counter: valid, at_least: 1}",
            "重复": "- {code: a, icon: x, name: n, desc: d, counter: valid, at_least: 1}\n"
                    "- {code: a, icon: x, name: n, desc: d, streak: 3}",
            "没有叫": "- {code: a, icon: x, name: n, desc: d, counter: vallid, at_least: 1}",
            "只能写其中一种": "- {code: a, icon: x, name: n, desc: d, counter: valid, at_least: 1, streak: 3}",
            "正整数": "- {code: a, icon: x, name: n, desc: d, counter: valid}",
        }
        for expected, text in mistakes.items():
            with self.assertRaises(ValueError) as caught:
                self.load(text)
            self.assertIn(expected, str(caught.exception))


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

    def test_streak_and_comeback_badges(self):
        streak = Badge("s", "x", "n", "d", streak=7)
        self.assertTrue(streak.met({}, streak=7, comeback=False))
        back = Badge("c", "x", "n", "d", comeback=True)
        self.assertTrue(back.met({}, streak=0, comeback=True))
        self.assertFalse(back.met({"valid": 999}, streak=99, comeback=False))


if __name__ == "__main__":
    unittest.main()
