"""The team game (/抓): rules file checks and simulated game nights with a fake QQ."""
import os
import re
import shutil
import tempfile
import unittest
from datetime import timedelta
from core.config import root_path
from modules.fun.teams import TeamsModule
from modules.fun.teams.rules import load_rules
from tests.test_manager import GROUP, START, FakeGateway, FakeMessage

OTHER_GROUP = "G2"
CAPTAINS = ["CA", "CB", "CC"]
NAMES = {"CA": "阿队", "CB": "贝队", "CC": "西队", "ADMIN": "管理员", "M1": "小一", "M2": "小二", "M3": "小三",
         "M4": "小四", "M5": "小五"}


def shipped_rules():
    with open(root_path("prompts", "teams.yaml"), encoding="utf-8") as f:
        return f.read()


class TeamGameTestCase(unittest.IsolatedAsyncioTestCase):
    def make(self, rules_path=None, data_path=None, **teams_config):
        self.now = START.replace(hour=20)
        self.gw = FakeGateway()
        config = {"teams": {"captains": CAPTAINS, **teams_config}}
        self.teams = TeamsModule(self.gw, config, is_admin=lambda group, member, role=None: member == "ADMIN",
                                 rules_path=rules_path or root_path("prompts", "teams.yaml"),
                                 data_path=data_path, clock=lambda: self.now)
        return self.teams

    async def say(self, member, text, group=GROUP):
        """`member` @s the bot with `text`; "@M1" in the text @s M1 too. Returns the bot's reply."""
        mentions = [(openid, NAMES.get(openid)) for openid in re.findall(r"@([A-Z0-9]+)", text)]
        content = re.sub(r"@([A-Z0-9]+)", lambda m: "@" + NAMES.get(m.group(1), m.group(1)), text)
        message = FakeMessage(group, member, NAMES.get(member, member), text, content=content,
                              mentions_bot=True, mentioned_members=mentions)
        before = len(self.gw.sent)
        handled = await self.teams.handle_teams(message)
        replies = [content for _, content, _ in self.gw.sent[before:]]
        return "\n".join(replies) if handled else None

    def team(self, captain, group=GROUP):
        teams = self.teams.roster.today(group, self.teams._day())
        team = teams.teams.get(captain)
        return {openid: m.status for openid, m in team.members.items()} if team else None


class GrabTest(TeamGameTestCase):
    async def test_captains_grab_free_members(self):
        self.make()
        self.assertEqual(await self.say("CA", "/抓 @M1 @M2"), "阿队 抓到了 小一、小二！")
        self.assertEqual(set(self.team("CA")), {"M1", "M2"})
        reply = await self.say("CB", "/抓 @M2 @M3")              # M2 is taken: only M3 joins
        self.assertIn("贝队 抓到了 小三！", reply)
        self.assertIn("小二 已经在 阿队 的队伍里了。", reply)
        self.assertIn("小一 已经在你的队伍里啦。", await self.say("CA", "/抓@M1"))
        self.assertIn("不能抓自己", await self.say("CA", "/抓 @CA"))
        self.assertIn("贝队 今天是队长", await self.say("CA", "/抓 @CB"))

    async def test_only_captains_grab_and_admins_cannot_either(self):
        self.make()
        self.assertEqual(await self.say("M1", "/抓 @M2"), "只有队长才能用 /抓 哦～")
        self.assertEqual(await self.say("ADMIN", "/抓 @M2"), "只有队长才能用 /抓 哦～")
        self.assertIsNone(self.team("ADMIN"))
        self.assertIn("要 @ 对象", await self.say("CA", "/抓"))

    async def test_a_captain_without_a_team_can_be_grabbed_and_then_cannot_lead(self):
        self.make()
        self.assertIn("抓到了 西队", await self.say("CA", "/抓 @CC"))
        self.assertEqual(await self.say("CC", "/抓 @M1"), "你现在是 阿队 队伍里的队员，不能另外组队哦。")

    async def test_team_size_limit(self):
        rules = self.rules_file(shipped_rules().replace("max_members: 0", "max_members: 2"))
        self.make(rules_path=rules)
        reply = await self.say("CA", "/抓 @M1 @M2 @M3")
        self.assertIn("抓到了 小一、小二", reply)
        self.assertIn("队伍满员了（最多 2 人），小三 没能加入。", reply)

    def rules_file(self, text):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder)
        path = os.path.join(folder, "teams.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path


class CaptainActionTest(TeamGameTestCase):
    async def asyncSetUp(self):
        self.make()
        await self.say("CA", "/抓 @M1 @M2 @M3")
        await self.say("CB", "/抓 @M4")

    async def test_statuses_and_the_team_list(self):
        self.assertIn("任命 小一 为小队长", await self.say("CA", "/设为小队长 @M1"))
        self.assertIn("小二、小三 阵亡了", await self.say("CA", "/阵亡 @M2 @M3"))
        self.assertIn("小二 已经是「死亡」了。", await self.say("CA", "/死亡 @M2"))
        self.assertIn("小三 不是「存活」，不能用 /设为小队长。", await self.say("CA", "/设为小队长 @M3"))
        self.assertIn("复活了", await self.say("CA", "/复活 @M3"))
        self.assertEqual(self.team("CA")["M1"], {"职位": "小队长", "生死": "存活"})
        listing = await self.say("M5", "/队伍")                   # anyone can look
        self.assertEqual(listing, "🏳️ 今日队伍 · 11月1日\n【阿队】3 人：⭐小一、💀小二、小三\n【贝队】1 人：小四\n"
                                  "图例：⭐小队长 💀死亡")

    async def test_vice_captains_may_only_do_what_by_allows(self):
        await self.say("CA", "/设为小队长 @M1")
        self.assertIn("小二 阵亡了", await self.say("M1", "/死亡 @M2"))      # 死亡: by [队长, 小队长]
        self.assertIn("小四 不在你的队伍里。", await self.say("M1", "/死亡 @M4"))
        self.assertEqual(await self.say("M1", "/踢出 @M3"), "只有队长、管理员才能用 /踢出 哦～")
        self.assertEqual(await self.say("M3", "/死亡 @M2"), "只有队长、小队长、管理员才能用 /死亡 哦～")

    async def test_members_cannot_leave_and_captains_rule_only_their_own_team(self):
        self.assertEqual(await self.say("M2", "/踢出 @M2"), "只有队长、管理员才能用 /踢出 哦～")
        self.assertIn("小四 不在你的队伍里。", await self.say("CA", "/踢出 @M4"))
        self.assertIn("小五 不在你的队伍里。", await self.say("CA", "/死亡 @M5"))
        self.assertIn("贝队 今天是队长", await self.say("CA", "/死亡 @CB"))
        self.assertIn("西队 不在你的队伍里", await self.say("CC", "/踢出 @CC"))   # a captain with no team today

    async def test_kicked_members_are_free_again(self):
        self.assertIn("小二 被 阿队 请出了队伍", await self.say("CA", "/放人 @M2"))
        self.assertIn("贝队 抓到了 小二", await self.say("CB", "/抓 @M2"))
        await self.say("CB", "/踢出 @M2 @M4")
        self.assertIsNone(self.team("CB"))                      # an empty team disappears

    async def test_admins_referee_every_team(self):
        self.assertIn("小四 阵亡了", await self.say("ADMIN", "/死亡 @M4"))
        self.assertIn("小一 被 管理员 请出了队伍", await self.say("ADMIN", "/踢出 @M1"))
        self.assertIn("小五 现在不在任何队伍里。", await self.say("ADMIN", "/踢出 @M5"))

    async def test_disband_and_clear(self):
        self.assertEqual(await self.say("M1", "/解散队伍"), "只有队长能解散自己的队伍；管理员可以用 /解散队伍 @队长。")
        self.assertEqual(await self.say("CC", "/解散队伍"), "你今天还没有队伍哦。")
        self.assertIn("要管理员来", await self.say("CA", "/解散队伍 @CB"))
        self.assertEqual(await self.say("CA", "/解散队伍"), "阿队 的队伍解散了，3 人恢复自由。")
        self.assertIn("贝队 抓到了 小一", await self.say("CB", "/抓 @M1"))
        self.assertEqual(await self.say("ADMIN", "/解散队伍"), "要解散哪支队伍？请 @ 那位队长：/解散队伍 @队长")
        self.assertEqual(await self.say("ADMIN", "/解散队伍 @CA @CB"),
                         "阿队 今天没有带队伍。\n贝队 的队伍解散了，2 人恢复自由。")
        await self.say("CA", "/抓 @M1")
        self.assertEqual(await self.say("CA", "/清空队伍"), "这个指令只有管理员能用哦～")
        self.assertEqual(await self.say("ADMIN", "/清空队伍"), "已清空本群的 1 支队伍，大家都恢复自由啦。")
        self.assertIn("今天还没有队伍", await self.say("M1", "/队伍"))

    async def test_groups_are_separate(self):
        self.assertIn("西队 抓到了 小一", await self.say("CC", "/抓 @M1", group=OTHER_GROUP))
        self.assertNotIn("西队", await self.say("M1", "/队伍"))


class DayAndStorageTest(TeamGameTestCase):
    async def test_teams_clear_at_the_daily_reset(self):
        self.make()
        await self.say("CA", "/抓 @M1")
        self.now = START.replace(hour=4, minute=59) + timedelta(days=1)    # 04:59 next morning: same game night
        self.assertIn("阿队", await self.say("M1", "/队伍"))
        self.now += timedelta(minutes=1)                                    # 05:00: a new day
        self.assertIn("今天还没有队伍", await self.say("M1", "/队伍"))

    async def test_scheduled_reset_clears_quiet_groups(self):
        self.make()
        await self.say("CA", "/抓 @M1")
        await self.say("CB", "/抓 @M2", group=OTHER_GROUP)
        self.assertEqual(self.teams.roster.reset_stale(self.teams._day()), 0)
        self.now += timedelta(days=1)
        self.assertEqual(self.teams.roster.reset_stale(self.teams._day()), 2)
        self.assertEqual(self.teams.roster.groups, {})

    async def test_teams_survive_a_restart_and_rule_changes(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder)
        data = os.path.join(folder, "teams.json")
        self.make(data_path=data)
        await self.say("CA", "/抓 @M1 @M2")
        await self.say("CA", "/死亡 @M1")
        await self.say("CA", "/设为小队长 @M2")
        # Mid-game, 小队长 is renamed 副队长 and the bot restarts.
        text = shipped_rules().replace("职位: [队员, 小队长]", "职位: [队员, 副队长]").replace("set: 小队长", "set: 副队长")
        text = text.replace("  小队长: ⭐\n", "").replace("by: [队长, 小队长]", "by: [队长, 副队长]")
        rules = os.path.join(folder, "teams.yaml")
        with open(rules, "w", encoding="utf-8") as f:
            f.write(text)
        self.make(rules_path=rules, data_path=data)
        self.assertEqual(self.team("CA"), {"M1": {"职位": "队员", "生死": "死亡"},
                                           "M2": {"职位": "队员", "生死": "存活"}})

    async def test_forgetting(self):
        self.make()
        await self.say("CA", "/抓 @M1 @M2")
        await self.say("CB", "/抓 @M3")
        self.teams.forget_member(GROUP, "M1")
        self.teams.forget_member(GROUP, "CB")                   # leads a team: the team goes
        self.assertEqual(set(self.team("CA")), {"M2"})
        self.assertIsNone(self.team("CB"))
        self.teams.forget_group(GROUP)
        self.assertNotIn(GROUP, self.teams.roster.groups)


class SetupTest(TeamGameTestCase):
    async def test_off_without_captains(self):
        teams = TeamsModule(FakeGateway(), {})
        self.assertFalse(teams.enabled)
        message = FakeMessage(GROUP, "CA", "阿队", "/抓", content="/抓", mentioned_members=[("M1", "小一")])
        self.assertFalse(await teams.handle_teams(message))

    async def test_only_listed_groups_play(self):
        self.make(groups=[OTHER_GROUP])
        self.assertEqual(await self.say("CA", "/抓 @M1"), "这个群没有开启组队游戏哦～")
        self.assertIn("抓到了", await self.say("CA", "/抓 @M1", group=OTHER_GROUP))

    async def test_other_commands_pass_through(self):
        self.make()
        for text in ("/我的状态", "/抓人游戏好玩吗", "随便聊聊 /抓", "/摇骰子"):
            self.assertIsNone(await self.say("CA", text), text)

    async def test_names_fall_back_when_mentions_have_none(self):
        self.make()
        self.teams.nickname_of = lambda group, member: {"M1": "老一"}.get(member)
        message = FakeMessage(GROUP, "CA", "阿队", "/抓", content="/抓 @某人 @某人",
                              mentioned_members=[("M1", None), ("ABCDEF1234", None)])
        await self.teams.handle_teams(message)
        self.assertEqual(self.gw.sent[-1][1], "阿队 抓到了 老一、群友#1234！")

    async def test_help_lists_the_actions(self):
        self.make()
        block = self.teams.help_block()
        self.assertIn("/抓 @某人", block)
        self.assertIn("/踢出 /设为小队长 /撤销小队长 /死亡 /复活", block)
        self.assertIn("05:00", block)


class AIViewTest(TeamGameTestCase):
    async def test_ai_chat_sees_todays_teams_but_not_hidden_statuses(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder)
        path = os.path.join(folder, "teams.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(shipped_rules().replace("hide_from_ai: []", "hide_from_ai: [死亡]"))
        self.make(rules_path=path)
        self.assertEqual(self.teams.today_for_ai(GROUP), [])
        await self.say("CA", "/抓 @M1 @M2")
        await self.say("CA", "/设为小队长 @M1")
        await self.say("CA", "/死亡 @M2")
        self.assertEqual(self.teams.today_for_ai(GROUP), [("阿队", [("小一", ["小队长", "存活"]), ("小二", ["队员"])])])
        self.assertEqual(self.teams.today_for_ai(OTHER_GROUP), [])
        self.now += timedelta(days=1)                         # yesterday's teams are over
        self.assertEqual(self.teams.today_for_ai(GROUP), [])
        self.assertEqual(TeamsModule(self.gw, {}).today_for_ai(GROUP), [])     # game off


class RenamedCommandsTest(TeamGameTestCase):
    async def test_built_in_commands_can_be_renamed(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder)
        rules = os.path.join(folder, "teams.yaml")
        with open(rules, "w", encoding="utf-8") as f:
            f.write(shipped_rules().replace("teams: 队伍", "teams: 看队").replace("disband: 解散队伍", "disband: 散队")
                    .replace("clear: 清空队伍", "clear: 清场"))
        self.make(rules_path=rules)
        await self.say("CA", "/抓 @M1")
        self.assertIn("【阿队】1 人：小一", await self.say("M1", "/看队"))
        self.assertIsNone(await self.say("M1", "/队伍"))                      # the old word passes through
        self.assertIn("/散队 @队长", await self.say("ADMIN", "/散队"))
        self.assertEqual(await self.say("ADMIN", "/清场"), "已清空本群的 1 支队伍，大家都恢复自由啦。")
        self.assertIn("/看队 查看今天的队伍", self.teams.help_block())


class RulesFileTest(unittest.TestCase):
    """Mistakes in teams.yaml stop the bot at startup with a message its editor can act on."""

    GOOD = shipped_rules()

    def check(self, text, reserved=()):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder)
        path = os.path.join(folder, "teams.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return load_rules(path, reserved)

    def assertRefused(self, text, *phrases, reserved=()):
        with self.assertRaises(ValueError) as caught:
            self.check(text, reserved)
        for phrase in phrases:
            self.assertIn(phrase, str(caught.exception))

    def test_the_shipped_rules_load(self):
        rules = self.check(self.GOOD)
        self.assertEqual(rules.action("阵亡").status, "死亡")
        self.assertEqual(rules.starting(), {"职位": "队员", "生死": "存活"})

    def test_a_new_status_and_action_need_only_yaml(self):
        rules = self.check(self.GOOD.replace("生死: [存活, 死亡]", "生死: [存活, 受伤, 死亡]") + """
  - name: 受伤
    set: 受伤
    only: [存活]
""")
        self.assertEqual((rules.action("受伤").column, rules.action("受伤").only), ("生死", ("存活",)))

    def test_mistakes(self):
        self.assertRefused(self.GOOD.replace("生死: [存活, 死亡]", "生死: [存活, 小队长]"), "「小队长」", "重复")
        self.assertRefused(self.GOOD.replace("生死: [存活, 死亡]", "生死: [存活]"), "至少要有两个状态")
        self.assertRefused(self.GOOD.replace("  死亡: 💀", "  牺牲: 💀"), "icons", "「牺牲」")
        self.assertRefused(self.GOOD.replace("set: 死亡", "set: 牺牲"), "第 5 个操作（死亡）", "「牺牲」")
        self.assertRefused(self.GOOD.replace("effect: kick", "effect: banish"), "banish", "grab")
        self.assertRefused(self.GOOD.replace("    effect: kick\n", ""), "effect 和 set")
        self.assertRefused(self.GOOD.replace("only: [存活]", "only: [存活, 死亡]"), "不可能同时满足")
        self.assertRefused(self.GOOD.replace("by: [队长, 小队长]", "by: [队长, 军师]"), "「军师」")
        self.assertRefused(self.GOOD.replace("aliases: [阵亡]", "aliases: [复活]"), "/复活", "重复")
        self.assertRefused(self.GOOD.replace("aliases: [阵亡]", "aliases: [队伍]"), "内置指令")
        self.assertRefused(self.GOOD.replace("clear: 清空队伍", "clear: 阵亡"), "/阵亡", "内置指令")
        self.assertRefused(self.GOOD.replace("clear: 清空队伍", "clear: 队伍"), "同一个名字")
        self.assertRefused(self.GOOD.replace("clear: 清空队伍", "wipe: 清空队伍"), "「wipe」")
        self.assertRefused(self.GOOD.replace("{actor} 抓到了", "{who} 抓到了"), "{who}")
        self.assertRefused(self.GOOD.replace("effect: grab", "set: 小队长"), "effect: grab")
        self.assertRefused(self.GOOD.replace('reset_time: "05:00"', "reset_time: 早上五点"), "reset_time")
        self.assertRefused(self.GOOD.replace("hide_from_ai: []", "hide_from_ai: [失踪]"), "hide_from_ai", "「失踪」")
        self.assertRefused(self.GOOD.replace("aliases: [放人]", "aliases: [成员踢出]"), "/成员",
                           reserved=("/成员", "/帮助"))

    def test_unquoted_reset_time(self):
        rules = self.check(self.GOOD.replace('reset_time: "05:00"', "reset_time: 06:30"))
        self.assertEqual(rules.reset_time.strftime("%H:%M"), "06:30")


if __name__ == "__main__":
    unittest.main()
