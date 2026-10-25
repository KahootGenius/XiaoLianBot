import unittest
from datetime import datetime, timedelta
import pytz
from modules.manager.rules import MemberSnapshot, Policy, evaluate

TZ = pytz.timezone('Asia/Shanghai')
START = TZ.localize(datetime(2026, 11, 1, 12, 0))
DAY = 86400


def at(days, hour=20):
    return START.replace(hour=hour) + timedelta(days=days)


def ts(days, hour=20):
    return int(at(days, hour).timestamp())


class RulesTest(unittest.TestCase):
    def setUp(self):
        self.policy = Policy()
        self.tracking = ts(0, 12)

    def verdict(self, member, day):
        return evaluate(member, at(day), self.policy, self.tracking, TZ)

    def test_reminder_on_day_25_then_removal_on_day_30(self):
        m = MemberSnapshot("m", "小明", first_seen=self.tracking)
        self.assertEqual(self.verdict(m, 24).action, "ok")
        reminder = self.verdict(m, 25)
        self.assertEqual((reminder.action, reminder.days, reminder.days_left), ("remind", 25, 5))
        m.reminded_at = ts(25)
        self.assertEqual(self.verdict(m, 29).action, "ok")
        self.assertTrue(self.verdict(m, 29).reminded)
        self.assertEqual(self.verdict(m, 30).action, "remove")

    def test_late_reminder_still_gets_five_days_notice(self):
        m = MemberSnapshot("m", "小明", first_seen=self.tracking)
        late = self.verdict(m, 28)          # bot was offline on day 25
        self.assertEqual((late.action, late.days_left), ("remind", 5))
        m.reminded_at = ts(28)
        self.assertEqual(self.verdict(m, 32).action, "ok")
        self.assertEqual(self.verdict(m, 33).action, "remove")

    def test_valid_message_resets_the_clock_and_voids_the_reminder(self):
        m = MemberSnapshot("m", "小明", first_seen=self.tracking, reminded_at=ts(25), last_valid_at=ts(27))
        v = self.verdict(m, 30)
        self.assertEqual((v.action, v.days, v.reminded), ("ok", 3, False))

    def test_leave_pauses_and_restarts_the_clock(self):
        m = MemberSnapshot("m", "小明", first_seen=self.tracking, leave_start=ts(10), leave_end=ts(50))
        self.assertEqual(self.verdict(m, 40).action, "leave")
        after = self.verdict(m, 60)
        self.assertEqual((after.action, after.days), ("ok", 10))

    def test_newcomer_counts_from_first_sight_without_member_list(self):
        m = MemberSnapshot("m", "新人", first_seen=ts(20))
        self.assertEqual(self.verdict(m, 30).days, 10)

    def test_exempt_admins_and_bots_are_never_flagged(self):
        for m, expected in ((MemberSnapshot("a", "x", first_seen=self.tracking, exempt=True), "exempt"),
                            (MemberSnapshot("b", "x", first_seen=self.tracking, is_admin=True), "admin"),
                            (MemberSnapshot("c", "x", first_seen=self.tracking, is_bot=True), "bot"),
                            (MemberSnapshot("d", "x", first_seen=self.tracking, status="removed"), "gone")):
            self.assertEqual(self.verdict(m, 40).action, expected)


if __name__ == "__main__":
    unittest.main()
