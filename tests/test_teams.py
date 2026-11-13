"""The team game (/抓): rules file checks and simulated game nights with a fake QQ."""
import asyncio
import contextlib
import io
import os
import re
import shutil
import tempfile
import unittest
from datetime import timedelta
from unittest import mock
from core.config import root_path
from core.style import STYLES
from modules.fun.teams import TeamsModule, check
from modules.fun.teams.history import History
from modules.fun.teams.module import _log as teams_log
from modules.fun.teams.rules import load_rules
from tests.test_manager import GROUP, START, FakeGateway, FakeMessage

OTHER_GROUP = "G2"
CAPTAINS = ["CA", "CB", "CC"]
NAMES = {"CA": "阿队", "CB": "贝队", "CC": "西队", "ADMIN": "管理员", "M1": "小一", "M2": "小二", "M3": "小三",
         "M4": "小四", "M5": "小五"}


def shipped_rules():
    with open(root_path("prompts", "teams.yaml"), encoding="utf-8") as f:
        return f.read()


def rules_with(events=None, hide=None, **replaced):
    """The shipped rules with their events (YAML for the list's entries) or hiding settings swapped."""
    text = shipped_rules()
    if events is not None:
        text, done = re.subn(r"(?ms)^events:\n.*?(?=^# -{20,} 档案)", f"events:\n{events}\n", text)
        assert done == 1
    if hide is not None:
        text, done = re.subn(r"(?ms)^hide:\n.*?(?=^# -{20,} 突发事件)", f"hide:\n{hide}\n", text)
        assert done == 1
    for old, new in replaced.items():
        assert old in text, old
        text = text.replace(old, new)
    return text


class Dice:
    """A random source the test sets: random() gives `roll`, from 0 to 1, choice() the item at `pick`,
    and randint() the next of `die`: the points a thrown die shows, one throw after another."""

    def __init__(self, roll=0.0, pick=0, die=()):
        self.roll, self.pick, self.rolls = roll, pick, 0
        self.die, self.throws, self.faces = list(die), 0, None

    def randint(self, low, high):
        self.throws, self.faces = self.throws + 1, (low, high)
        return self.die.pop(0)

    def random(self):
        self.rolls += 1
        return self.roll

    def choice(self, items):
        return list(items)[self.pick % len(items)]


class TeamGameTestCase(unittest.IsolatedAsyncioTestCase):
    def make(self, rules_path=None, data_path=None, rng=None, count=None, history=None, **teams_config):
        self.now = START.replace(hour=20)
        self.gw = FakeGateway()
        config = {"teams": {"captains": CAPTAINS, **teams_config}}
        if history is None:
            history = History(":memory:")
            self.addCleanup(history.db.close)
        self.teams = TeamsModule(self.gw, config, is_admin=lambda group, member, role=None: member == "ADMIN",
                                 rules_path=rules_path or root_path("prompts", "teams.yaml"),
                                 data_path=data_path, clock=lambda: self.now, rng=rng, count=count, history=history)
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

    def rules_file(self, text):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder)
        path = os.path.join(folder, "teams.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    async def chat(self, member, text="今天好累啊", group=GROUP, **kwargs):
        """`member` says something in the group that isn't a command; returns what the bot said to it."""
        message = FakeMessage(group, member, NAMES.get(member, member), text, content=text, **kwargs)
        before = len(self.gw.sent)
        await self.teams.observe(message)
        self.assertTrue(all(replied == message.id for _, _, replied in self.gw.sent[before:]))
        return "\n".join(content for _, content, _ in self.gw.sent[before:])

    def hiding(self, group=GROUP):
        return {openid: hider.spot for openid, hider in self.teams.roster.today(group, self.teams._day()).hiding.items()}


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
        with mock.patch.dict(STYLES.default().data["games"]["teams"], {"captain": "船长"}):    # a style's own word
            self.assertEqual(await self.say("M1", "/抓 @M2"), "只有船长才能用 /抓 哦～")
            self.assertEqual(await self.say("M1", "/死亡 @M2"), "只有船长、小队长、管理员才能用 /死亡 哦～")

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


class HideAndSeekTest(TeamGameTestCase):
    """/躲: someone who isn't on a team hides, where only the bot knows. A captain can't grab them:
    they look in one place at a time (/找), a few times a day."""
    SPOTS = "柜子里、窗帘后、床底下、书架后、阳台上、桌子底下"

    async def test_someone_hides_and_only_the_bot_knows_where(self):
        self.make(rng=Dice(pick=1))
        self.assertEqual(await self.say("M1", "/躲"),
                         "🙈 小一 躲起来了！藏在哪儿只有小莲知道（一共 6 个地方）。队长要用 /找 地点 才找得到。")
        self.assertEqual(self.hiding(), {"M1": "窗帘后"})
        self.assertEqual(await self.say("CA", "/抓 @M1 @M2"),
                         "阿队 抓到了 小二！\n小一 躲起来了，抓不到。用 /找 地点 去翻翻看吧。")
        self.assertEqual(set(self.team("CA")), {"M2"})
        self.assertEqual(await self.say("M1", "/躲"), "你已经躲好啦，别出声～想出来就发 /躲 出来")
        listed = await self.say("M3", "/队伍")
        self.assertIn("【阿队】1 人：小二\n🙈 躲起来的（1 人）：小一", listed)
        self.assertEqual(await self.say("M2", "/躲"), "你已经在 阿队 的队伍里了，躲不掉啦。")
        self.assertEqual(await self.say("CA", "/躲"), "你今天带着队伍呢，队长不能躲。")
        self.assertNotIn("窗帘后", "\n".join(self.gw.texts(GROUP)))          # nobody was told where
        await self.say("ADMIN", "/解散队伍 @CA")
        self.assertEqual(await self.say("M3", "/队伍"),                     # with no teams, still who's hiding
                         "今天还没有队伍哦～队长 @我 /抓 @某人 就能开始组队。\n🙈 躲起来的（1 人）：小一")

    async def test_a_captain_looks_in_one_place_at_a_time(self):
        self.make(rng=Dice(pick=1))
        await self.say("M1", "/躲")
        await self.say("M2", "/躲")
        self.assertEqual(await self.say("CA", "/找"),
                         f"要翻哪儿？/找 地点。现在有 2 人躲着，你今天还能找 3 次。\n可以翻的地方：{self.SPOTS}")
        self.assertEqual(self.gw.buttons[-1], (GROUP, [
            [("柜子里", "/找 柜子里"), ("窗帘后", "/找 窗帘后"), ("床底下", "/找 床底下"), ("书架后", "/找 书架后"),
             ("阳台上", "/找 阳台上")], [("桌子底下", "/找 桌子底下")]]))
        self.assertEqual(await self.say("CA", "/找 柜子里"), "🔦 阿队 翻了翻柜子里……什么也没有。（今天还能找 2 次）")
        self.assertEqual(self.gw.buttons[-1][1][1], [("桌子底下", "/找 桌子底下")])       # the places again, to try another
        self.assertIsNone(self.team("CA"))                                  # an empty search starts no team
        self.assertEqual(await self.say("CA", "/找窗帘后"),                    # everyone hiding there, at once
                         "🔦 阿队 在窗帘后找到了 小一、小二！抓进队伍。（今天还能找 1 次）")
        self.assertEqual(self.team("CA"), {"M1": {"职位": "队员", "生死": "存活"}, "M2": {"职位": "队员", "生死": "存活"}})
        self.assertEqual(self.hiding(), {})
        self.assertEqual(await self.say("CA", "/找 天花板"), f"没有「天花板」这个地方。可以翻的地方：{self.SPOTS}")
        self.assertEqual(await self.say("CA", "/找 床底下"), "现在没有人躲着，不用找啦。")
        self.assertEqual(self.teams.roster.today(GROUP, self.teams._day()).searches, {"CA": 2})     # that one cost nothing

    async def test_who_may_look_and_how_often(self):
        self.make(rng=Dice(pick=5))
        await self.say("M1", "/躲")
        self.assertEqual(await self.say("M2", "/找 柜子里"), "只有队长才能用 /找 找人哦～")
        self.assertEqual(await self.say("ADMIN", "/找 柜子里"), "只有队长才能用 /找 找人哦～")       # referees don't play
        await self.say("CB", "/抓 @CC")
        self.assertEqual(await self.say("CC", "/找 柜子里"), "你现在是 贝队 队伍里的队员，不能另外组队哦。")
        for left in (2, 1, 0):
            self.assertIn(f"什么也没有。（今天还能找 {left} 次）", await self.say("CA", "/找 柜子里"))
        self.assertEqual(self.gw.buttons[-1][0], GROUP)
        self.assertEqual(await self.say("CA", "/找 桌子底下"), "你今天已经找了 3 次，找不动了。")
        self.assertEqual(self.hiding(), {"M1": "桌子底下"})
        self.assertIn("找到了 小一", await self.say("CB", "/找 桌子底下"))       # each captain has their own three
        self.assertEqual(set(self.team("CB")), {"CC", "M1"})

    async def test_hiding_again_and_coming_out(self):
        self.make(rng=Dice(pick=0))
        self.assertEqual(await self.say("M1", "/躲 出来"), "你现在没有躲着哦。")
        for _ in range(3):
            self.assertIn("躲起来了", await self.say("M1", "/躲"))
            self.assertEqual(await self.say("M1", "/躲 出来"), "小一 自己走出来了。")
        self.assertEqual(await self.say("M1", "/躲"), "今天你已经躲了 3 次，不能再躲了。")
        self.assertEqual(self.hiding(), {})
        await self.say("CC", "/躲")                                         # a captain with no team may hide,
        self.assertIn("抓到了 小二", await self.say("CC", "/抓 @M2"))          # and shows themself by grabbing
        self.assertEqual(self.hiding(), {})
        await self.say("CB", "/躲")
        await self.say("M3", "/躲")
        self.assertIn("找到了 小三", await self.say("CB", "/找 柜子里"))        # or by looking: they don't find themself
        self.assertEqual((self.hiding(), set(self.team("CB"))), ({}, {"M3"}))

    async def test_a_full_team_leaves_the_rest_unseen(self):
        self.make(rules_path=self.rules_file(rules_with(**{"max_members: 0": "max_members: 1"})), rng=Dice(pick=2))
        await self.say("M1", "/躲")
        await self.say("M2", "/躲")
        self.assertEqual(await self.say("CA", "/找 床底下"), "🔦 阿队 在床底下找到了 小一！抓进队伍。（今天还能找 2 次）")
        self.assertEqual(self.hiding(), {"M2": "床底下"})
        self.assertEqual(await self.say("CA", "/找 床底下"), "你的队伍满员了（最多 1 人），找到了也装不下。")
        self.assertEqual(self.teams.roster.today(GROUP, self.teams._day()).searches, {"CA": 1})

    async def test_staying_hidden_all_day_goes_in_the_record(self):
        counted = []
        self.make(rng=Dice(pick=0), count=lambda group, member, name, key: counted.append((member, key)) or [])
        await self.say("M1", "/躲")
        await self.say("M2", "/躲")
        await self.say("M3", "/躲", group=OTHER_GROUP)
        await self.say("CA", "/找 柜子里")                                   # both found: neither slipped through
        await self.say("CA", "/踢出 @M1")
        await self.say("M1", "/躲")
        self.assertEqual([entry for entry in counted if entry[1] == "slipped"], [])
        self.now += timedelta(days=1)
        self.assertEqual(self.teams._close_days(), 2)                       # the scheduler's minute
        self.assertEqual([entry for entry in counted if entry[1] == "slipped"], [("M1", "slipped"), ("M3", "slipped")])
        self.assertEqual(self.teams.history.totals(GROUP, "M1"), {"hid": 2, "found": 1, "caught": 1, "slipped": 1})
        self.assertEqual(self.teams.roster.groups, {})
        await self.say("M1", "/躲")                                          # a new day: a new three of everything
        self.assertIn("今天还能找 2 次", await self.say("CA", "/找 窗帘后"))
        self.now += timedelta(days=1)
        self.assertIn("今天还没有队伍", await self.say("M2", "/队伍"))           # or the day's first command puts it away
        self.assertEqual(self.teams.history.totals(GROUP, "M1")["slipped"], 2)

    async def test_hiding_survives_a_restart_but_not_a_place_that_is_gone(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder)
        data = os.path.join(folder, "teams.json")
        self.make(data_path=data, rng=Dice(pick=0))
        await self.say("M1", "/躲")
        self.teams.rng.pick = 1
        await self.say("M2", "/躲")
        await self.say("CA", "/找 床底下")
        self.make(data_path=data)
        today = self.teams.roster.today(GROUP, self.teams._day())
        self.assertEqual((self.hiding(), today.hides, today.searches), ({"M1": "柜子里", "M2": "窗帘后"}, {"M1": 1, "M2": 1}, {"CA": 1}))
        fewer = self.rules_file(rules_with(hide="  spots: [柜子里, 床底下, 书架后]"))
        self.make(rules_path=fewer, data_path=data)
        self.assertEqual(self.hiding(), {"M1": "柜子里"})                     # 窗帘后 is no longer a place

    async def test_without_places_there_is_no_hiding(self):
        self.make(rules_path=self.rules_file(rules_with(hide="  spots: []")))
        for command in ("/躲", "/躲 出来", "/找", "/找 柜子里"):
            self.assertEqual(await self.say("CA", command), "这个游戏没有设置躲猫猫的地方哦。")
        self.assertNotIn("/躲", self.teams.help_block())
        self.make(rules_path=self.rules_file(re.sub(r"(?ms)^hide:\n.*?(?=^# -{20,} 突发事件)", "", shipped_rules())))
        self.assertEqual(self.teams.rules.spots, ())                        # the whole section may be left out

    async def test_referees_and_deletion_bring_people_out(self):
        self.make(rng=Dice(pick=0))
        await self.say("M1", "/躲")
        await self.say("M2", "/躲")
        self.teams.forget_member(GROUP, "M1")                               # they left the group, say
        self.assertEqual(self.hiding(), {"M2": "柜子里"})
        self.assertEqual(await self.say("ADMIN", "/清空队伍"), "已清空本群的 0 支队伍，大家都恢复自由啦。")
        self.assertEqual(self.hiding(), {})
        self.assertEqual(await self.say("ADMIN", "/清空队伍"), "本群现在没有队伍。")


class EventTest(TeamGameTestCase):
    """Events from teams.yaml befall team members while the group chats."""
    SLIP = """  - name: 溜走
    chance: 2
    effect: kick
    say: "{members} 从 {captain} 那儿溜走了！"
"""
    TRIP = """  - name: 意外
    only: [存活]
    chance: 3
    set: 死亡
    say: "{members} 摔了一跤，现在是「{status}」。"
"""
    CALL = """  - name: 点名
    speaker: 队长
    chance: 50
    say: "{captain} 点到了 {members}。"
"""

    def play(self, events, roll=0.0, pick=0, **kwargs):
        self.make(rules_path=self.rules_file(rules_with(events=events, **kwargs)), rng=Dice(roll, pick))
        return self.teams

    async def test_something_befalls_a_member_who_speaks(self):
        self.play(self.SLIP)
        await self.say("CA", "/抓 @M1 @M2")
        self.assertEqual(await self.chat("M1"), "小一 从 阿队 那儿溜走了！")
        self.assertEqual(set(self.team("CA")), {"M2"})
        self.assertEqual(self.teams.history.totals(GROUP, "M1"), {"caught": 1, "event": 1})
        self.assertEqual(self.teams.history.most(GROUP, "M1", "event"), ("溜走", 1, ""))
        self.assertEqual(await self.chat("M2"), "")                         # the group has just had one
        self.now += timedelta(minutes=9, seconds=59)
        self.assertEqual(await self.chat("M2"), "")
        self.now += timedelta(seconds=1)
        self.assertEqual(await self.chat("M2"), "小二 从 阿队 那儿溜走了！")
        self.assertIsNone(self.team("CA"))                                  # its last member gone, so is the team

    async def test_each_event_has_its_own_chance(self):
        self.play(self.SLIP + self.TRIP, roll=0.05)                         # 5 in 100: past both, 2 and 3
        await self.say("CA", "/抓 @M1")
        self.assertEqual(await self.chat("M1"), "")
        self.teams.rng.roll = 0.0499                                        # just inside the second
        self.assertEqual(await self.chat("M1"), "小一 摔了一跤，现在是「死亡」。")
        self.assertEqual(self.team("CA"), {"M1": {"职位": "队员", "生死": "死亡"}})
        self.assertEqual(self.teams.history.totals(GROUP, "M1"), {"caught": 1, "event": 1, "=死亡": 1})
        self.now += timedelta(minutes=10)
        self.assertEqual(await self.chat("M1"), "")                         # already dead: 意外 can't happen, and 4.99 is past 溜走
        self.teams.rng.roll = 0.0199
        self.assertEqual(await self.chat("M1"), "小一 从 阿队 那儿溜走了！")

    async def test_who_an_event_can_befall(self):
        self.play("""  - name: 招魂
    only: [死亡]
    chance: 50
    effect: kick
    say: "{members} 的魂被招走了。"
  - name: 意外
    chance: 50
    set: 死亡
    say: "{members} 现在是「{status}」。"
""", roll=0.0, **{"event_gap: 10": "event_gap: 0", "event_daily: 2": "event_daily: 0"})
        await self.say("CA", "/抓 @M1")
        self.assertEqual(await self.chat("M1"), "小一 现在是「死亡」。")       # alive: 招魂 isn't theirs to meet, so the roll lands on 意外
        self.teams.rng.roll = 0.99                                          # past 招魂's 50, and 意外 would change nothing now
        self.assertEqual(await self.chat("M1"), "")
        self.assertEqual(self.team("CA"), {"M1": {"职位": "队员", "生死": "死亡"}})
        self.teams.rng.roll = 0.0
        self.assertEqual(await self.chat("M1"), "小一 的魂被招走了。")

    async def test_a_captains_words_fall_on_one_of_their_members(self):
        self.play(self.SLIP + self.CALL, pick=1)
        await self.say("CA", "/抓 @M1 @M2")
        self.assertEqual(await self.chat("CA"), "阿队 点到了 小二。")           # not 溜走: that's a member's own
        self.assertEqual(set(self.team("CA")), {"M1", "M2"})                # and this one only says something
        self.assertEqual(self.teams.history.totals(GROUP, "M2"), {"caught": 1, "event": 1})
        self.assertEqual(self.teams.history.totals(GROUP, "CA"), {"grabbed": 2})
        self.now += timedelta(minutes=10)
        self.assertEqual(await self.chat("CB"), "")                         # a captain with no team
        self.assertEqual(await self.chat("M3"), "")                         # and someone who isn't playing
        self.assertEqual(await self.chat("M1"), "小一 从 阿队 那儿溜走了！")

    async def test_a_roll_and_a_days_worth(self):
        self.play("""  - name: 换岗
    chance: 100
    effect: roll
    from: [队员, 小队长]
    say: "{members} 现在是「{status}」。"
""", pick=1, **{"event_gap: 10": "event_gap: 0"})
        await self.say("CA", "/抓 @M1 @M2")
        self.assertEqual(await self.chat("M1"), "小一 现在是「小队长」。")
        self.assertEqual(self.team("CA")["M1"]["职位"], "小队长")
        self.assertEqual(await self.chat("M1"), "小一 现在是「小队长」。")       # no gap in this game
        self.assertEqual(await self.chat("M1"), "")                         # two a day each
        self.assertEqual(await self.chat("M2"), "小二 现在是「小队长」。")
        self.assertEqual(self.teams.history.totals(GROUP, "M1"), {"caught": 1, "event": 2, "=小队长": 2})
        self.now += timedelta(days=1)                                       # a new game day: nobody is on a team
        self.assertEqual(await self.chat("M1"), "")
        await self.say("CA", "/抓 @M1")
        self.assertEqual(await self.chat("M1"), "小一 现在是「小队长」。")

    async def test_commands_bots_other_groups_and_the_same_message_twice(self):
        self.play(self.SLIP)
        await self.say("CA", "/抓 @M1")
        for text in ("/今日运势", "／帮助", " /队伍"):
            self.assertEqual(await self.chat("M1", text), "")
        self.assertEqual(await self.chat("M1", is_bot=True), "")
        self.assertEqual(await self.chat("M1", group=OTHER_GROUP), "")      # no game there today
        self.assertEqual(self.teams.rng.rolls, 0)
        self.teams.rng.roll = 0.5
        message = FakeMessage(GROUP, "M1", "小一", "在吗", content="在吗")
        await self.teams.observe(message)
        await self.teams.observe(message)                                   # an @ arrives as two events
        self.assertEqual(self.teams.rng.rolls, 1)
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder)
        data, rules = os.path.join(folder, "teams.json"), self.rules_file(rules_with(events=self.SLIP))
        self.make(rules_path=rules, data_path=data, rng=Dice())
        await self.say("CA", "/抓 @M1")
        self.make(rules_path=rules, data_path=data, rng=Dice(), groups=[OTHER_GROUP])      # the group stops playing mid-game
        self.assertEqual(set(self.team("CA")), {"M1"})
        self.assertEqual(await self.chat("M1"), "")
        self.assertEqual(self.teams.rng.rolls, 0)

    async def test_no_events_no_rolls(self):
        self.play("")
        self.assertEqual(self.teams.rules.events, ())
        await self.say("CA", "/抓 @M1")
        self.assertEqual(await self.chat("M1"), "")
        self.assertEqual(self.teams.rng.rolls, 0)
        self.assertNotIn("突发事件", self.teams.help_block())
        off = TeamsModule(self.gw, {})                                      # and with the game off
        await off.observe(FakeMessage(GROUP, "M1", "小一", "在吗", content="在吗"))


class StealTest(TeamGameTestCase):
    """An action with effect: steal, the shipped /抢人: a die thrown for another captain's member."""

    async def start(self, *die, rules=None, **kwargs):
        """CA has M1 and M2, CB has M3. The die will show `die`, one throw after another."""
        self.dice = Dice(die=die)
        self.make(rules_path=self.rules_file(rules) if rules else None, rng=self.dice, **kwargs)
        await self.say("CA", "/抓 @M1 @M2")
        await self.say("CB", "/抓 @M3")

    def throws(self, group=GROUP):
        return self.teams.roster.today(group, self.teams._day()).throws

    async def test_at_four_or_more_they_change_teams(self):
        await self.start(4, 6)
        await self.say("CA", "/设为小队长 @M1")
        self.assertEqual(await self.say("CB", "/抢人 @M1"),
                         "🎲 贝队 掷出了 4 点，把 小一 从 阿队 的队伍里抢了过来！（今天还能掷 2 次）")
        fresh = {"职位": "队员", "生死": "存活"}
        self.assertEqual(self.team("CB"), {"M3": fresh, "M1": fresh})          # they start over, like anyone grabbed
        self.assertEqual(set(self.team("CA")), {"M2"})
        self.assertEqual(self.gw.buttons[-1], (GROUP, [[("抢人", "/抢人 ")]]))    # the die again; they add whom
        self.assertIn("把 小二 从 阿队 的队伍里抢了过来！（今天还能掷 1 次）", await self.say("CB", "/抢人@M2"))
        self.assertIsNone(self.team("CA"))                                      # a team with nobody left disappears
        self.assertEqual(list(self.team("CB")), ["M3", "M1", "M2"])
        self.assertEqual((self.throws(), self.dice.faces), ({"抢人": {"CB": 2}}, (1, 6)))
        self.assertIn("\n掷骰子抢人：抢到 2 次，失手 0 次", await self.say("CB", "/档案"))

    async def test_below_four_nothing_moves_and_the_throws_run_out(self):
        await self.start(3, 1, 2, 5, 6)
        self.assertEqual(await self.say("CB", "/抢人 @M1"),
                         "🎲 贝队 掷出了 3 点，差一点：小一 还在 阿队 的队伍里。（今天还能掷 2 次）")
        self.assertEqual((set(self.team("CA")), set(self.team("CB"))), ({"M1", "M2"}, {"M3"}))
        await self.say("CB", "/抢人 @M1")
        buttons = len(self.gw.buttons)
        self.assertIn("（今天还能掷 0 次）", await self.say("CB", "/抢人 @M1"))
        self.assertEqual(len(self.gw.buttons), buttons)                         # no throw left, no button for one
        self.assertEqual(await self.say("CB", "/抢人 @M1"), "你今天已经掷了 3 次 /抢人，明天再来吧。")
        self.assertEqual(self.dice.throws, 3)                                   # the die stayed where it was
        self.assertIn("阿队 掷出了 5 点，把 小三 从 贝队", await self.say("CA", "/抢人 @M3"))     # each has their own throws
        self.now += timedelta(days=1)                                           # a new game day brings new ones
        await self.say("CA", "/抓 @M1")
        self.assertIn("贝队 掷出了 6 点，把 小一 从 阿队 的队伍里抢了过来！（今天还能掷 2 次）", await self.say("CB", "/抢人 @M1"))

    async def test_whoever_cannot_be_taken_costs_no_throw(self):
        rules = shipped_rules().replace("max_members: 0", "max_members: 2")
        await self.start(5, rules=rules.replace("    need: 4\n", "    need: 4\n    only: [存活]\n"))
        await self.say("CA", "/死亡 @M2")
        self.assertEqual(await self.say("CB", "/抢人 @M3"), "小三 已经在你的队伍里啦。")
        self.assertEqual(await self.say("CB", "/抢人 @CA"), "阿队 今天是队长，不能这样做哦。")
        self.assertEqual(await self.say("CB", "/抢人 @M5"), "小五 现在不在任何队伍里。")
        self.assertEqual(await self.say("CC", "/抢人 @CC"), "不能抓自己哦～")
        self.assertEqual(await self.say("CB", "/抢人 @M2"), "小二 不是「存活」，不能用 /抢人。")
        self.assertEqual(await self.say("CA", "/抢人 @M3"), "队伍满员了（最多 2 人），小三 没能加入。")
        self.assertIn("要 @ 对象", await self.say("CB", "/抢人"))
        self.assertEqual((self.dice.throws, self.throws(), self.gw.buttons), (0, {}, []))
        self.assertEqual(await self.say("CB", "/抢人 @M3 @M1"),                # one who can't be and one who can: one throw
                         "🎲 贝队 掷出了 5 点，把 小一 从 阿队 的队伍里抢了过来！（今天还能掷 2 次）\n小三 已经在你的队伍里啦。")
        self.assertEqual(self.dice.throws, 1)

    async def test_only_captains_throw_it(self):
        await self.start()
        self.assertEqual(await self.say("M1", "/抢人 @M3"), "只有队长才能用 /抢人 哦～")
        self.assertEqual(await self.say("ADMIN", "/抢人 @M3"), "只有队长才能用 /抢人 哦～")       # a referee leads no team
        await self.say("CA", "/抓 @CC")
        self.assertEqual(await self.say("CC", "/抢人 @M3"), "你现在是 阿队 队伍里的队员，不能另外组队哦。")
        self.assertEqual(self.dice.throws, 0)

    async def test_a_captain_with_no_team_starts_one_with_it_and_has_come_out_of_hiding(self):
        await self.start(2, 6)
        await self.say("CC", "/躲")
        self.assertIn("CC", self.hiding())
        self.assertIn("西队 掷出了 2 点，差一点", await self.say("CC", "/抢人 @M3"))
        self.assertEqual((self.team("CC"), self.hiding()), (None, {}))         # seen, and still without a team
        self.assertIn("西队 掷出了 6 点，把 小三 从 贝队 的队伍里抢了过来", await self.say("CC", "/抢人 @M3"))
        self.assertEqual((set(self.team("CC")), self.team("CB")), ({"M3"}, None))

    async def test_a_status_named_in_by_throws_it_for_the_team_they_are_in(self):
        await self.start(4, rules=shipped_rules().replace("    need: 4\n", "    need: 4\n    by: [队长, 小队长]\n"))
        self.assertEqual(await self.say("M1", "/抢人 @M3"), "只有队长、小队长才能用 /抢人 哦～")
        await self.say("CA", "/设为小队长 @M1")
        self.assertEqual(await self.say("M1", "/抢人 @M2"), "小二 已经在你的队伍里啦。")
        self.assertIn("小一 掷出了 4 点，把 小三 从 贝队 的队伍里抢了过来", await self.say("M1", "/抢人 @M3"))
        self.assertEqual((set(self.team("CA")), self.throws()), ({"M1", "M2", "M3"}, {"抢人": {"M1": 1}}))

    async def test_each_of_several_gets_a_throw_and_a_line_of_their_own(self):
        await self.start(2, 5, rules=shipped_rules().replace("    times: 3\n", "    times: 2\n"))
        await self.say("CA", "/抓 @M4")
        self.assertEqual(await self.say("CB", "/抢人 @M1 @M2 @M4"),
                         "🎲 贝队 掷出了 2 点，差一点：小一 还在 阿队 的队伍里。（今天还能掷 1 次）\n"
                         "🎲 贝队 掷出了 5 点，把 小二 从 阿队 的队伍里抢了过来！（今天还能掷 0 次）\n"
                         "你今天已经掷了 2 次 /抢人，明天再来吧。")
        self.assertEqual((set(self.team("CA")), set(self.team("CB"))), ({"M1", "M4"}, {"M3", "M2"}))
        self.assertEqual(self.gw.buttons, [])

    async def test_without_lines_of_its_own_it_speaks_the_styles(self):
        rules, removed = re.subn(r'(?m)^    (say|fail): "🎲.*\n', "", shipped_rules())
        self.assertEqual(removed, 2)
        await self.start(5, 1, rules=rules)
        self.assertEqual(await self.say("CB", "/抢人 @M1"),
                         "🎲 贝队 用「抢人」掷出了 5 点（4 点以上算抢到），把 小一 从 阿队 的队伍里抢了过来！今天还能掷 2 次。")
        self.assertEqual(await self.say("CB", "/抢人 @M2"),
                         "🎲 贝队 用「抢人」掷出了 1 点，要 4 点以上才算抢到：小二 还在 阿队 的队伍里。今天还能掷 1 次。")

    async def test_need_sets_what_the_die_must_show(self):
        await self.start(5, 6, 1, rules=shipped_rules().replace("    need: 4\n", "    need: 6\n"))
        self.assertIn("掷出了 5 点，差一点", await self.say("CB", "/抢人 @M1"))
        self.assertIn("掷出了 6 点，把 小一", await self.say("CB", "/抢人 @M1"))
        self.make(rules_path=self.rules_file(shipped_rules().replace("    need: 4\n", "    need: 1\n")), rng=self.dice)
        await self.say("CA", "/抓 @M1")
        self.assertIn("掷出了 1 点，把 小一", await self.say("CB", "/抢人 @M1"))                 # need: 1 never misses

    async def test_the_days_throws_outlast_a_restart_and_leave_with_the_member(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder)
        data = os.path.join(folder, "teams.json")
        await self.start(1, data_path=data)
        await self.say("CB", "/抢人 @M1")
        await self.say("ADMIN", "/清空队伍")                                      # no team left: the throw alone keeps the day
        self.make(data_path=data, rng=Dice(die=(6,)))                           # the bot restarts
        self.assertEqual(self.throws(), {"抢人": {"CB": 1}})
        await self.say("CA", "/抓 @M1")
        self.assertIn("（今天还能掷 1 次）", await self.say("CB", "/抢人 @M1"))
        self.teams.forget_member(GROUP, "CB")                                   # left the group
        self.assertEqual(self.throws(), {})

    async def test_it_goes_in_both_records_and_counts_as_being_caught(self):
        counted = []

        def count(group, member, name, key):
            counted.append((member, key))
            return [f"🎖️ {name}：{key}"] if member == "M1" else []

        await self.start(2, 5, 1, count=count)
        del counted[:]
        await self.say("CB", "/抢人 @M1")                                        # falls short: nobody was caught
        self.assertEqual(counted, [])
        self.assertEqual(await self.say("CB", "/抢人 @M1"),
                         "🎲 贝队 掷出了 5 点，把 小一 从 阿队 的队伍里抢了过来！（今天还能掷 1 次）\n🎖️ 小一：caught")
        self.assertEqual(counted, [("M1", "caught"), ("CB", "grabbed")])
        await self.say("CC", "/抢人 @M2")
        self.assertEqual(await self.say("M1", "/档案"),
                         "📒 小一 的游戏档案\n被抓进队 2 次，最常抓到 TA 的是 阿队（1 次）\n被别的队长掷骰子抢走 1 次")
        self.assertEqual(await self.say("CB", "/档案"),
                         "📒 贝队 的游戏档案\n抓过 2 人次，最常抓的是 小三（1 次）\n掷骰子抢人：抢到 1 次，失手 1 次")
        self.assertEqual(await self.say("CC", "/档案"), "📒 西队 的游戏档案\n掷骰子抢人：抢到 0 次，失手 1 次")
        self.assertEqual(self.teams.history.most(GROUP, "M1", "stolen"), ("CB", 1, "贝队"))
        self.assertEqual(self.teams.history.most(GROUP, "CB", "steals"), ("M1", 1, "小一"))
        self.teams.forget_record(GROUP, "CB")                                   # and their part in it goes with them
        self.assertEqual(self.teams.history.totals(GROUP, "M1"), {"caught": 1})

    async def test_help_gives_it_a_line_of_its_own(self):
        self.make()
        block = self.teams.help_block()
        self.assertIn("队长对自己的队员：/踢出 /设为小队长 /撤销小队长 /死亡 /复活（都要 @ 对象）", block)
        self.assertIn("\n队长：/抢人 @别队的队员 掷骰子抢人，掷出 4 点以上就抢进自己的队伍，每天 3 次\n还没进队的人", block)
        self.make(rules_path=self.rules_file(re.sub(r"(?ms)^  - name: 抢人\n.*?(?=^# 想加新的)", "", shipped_rules())))
        self.assertNotIn("掷骰子", self.teams.help_block())                       # a game without one says nothing of it


class RecordTest(TeamGameTestCase):
    """/档案: what has happened to someone in the game, across game days. Counts only."""

    async def test_a_record_builds_up_over_the_days(self):
        counted = []

        def count(group, member, name, key):
            counted.append((member, key))
            return [f"🎖️ {name} 第一次被抓"] if (member, key) == ("M1", "caught") and counted.count((member, key)) == 1 else []

        self.make(rng=Dice(pick=0), count=count)
        self.assertEqual(await self.say("M1", "/档案"), "小一 还没有游戏档案，玩过一次就有了。")
        self.assertEqual(await self.say("CA", "/抓 @M1 @M2"), "阿队 抓到了 小一、小二！\n🎖️ 小一 第一次被抓")
        self.assertEqual(counted, [("M1", "caught"), ("CA", "grabbed"), ("M2", "caught"), ("CA", "grabbed")])
        await self.say("CA", "/死亡 @M1")
        await self.say("CA", "/复活 @M1")
        await self.say("CA", "/死亡 @M1")
        await self.say("CA", "/设为小队长 @M2")
        self.now += timedelta(days=1)
        await self.say("CB", "/抓 @M1")
        await self.say("CB", "/踢出 @M1")
        await self.say("CA", "/抓 @M1")
        await self.say("CA", "/踢出 @M1")
        await self.say("M1", "/躲")
        await self.say("CA", "/找 柜子里")
        self.assertEqual(await self.say("M1", "/档案"),
                         "📒 小一 的游戏档案\n被抓进队 4 次，最常抓到 TA 的是 阿队（3 次）\n经历过：死亡 2 次\n"
                         "躲猫猫：躲了 1 次，被找到 1 次，躲到清空 0 次")
        self.assertEqual(await self.say("M3", "/档案 @CA"),
                         "📒 阿队 的游戏档案\n抓过 4 人次，最常抓的是 小一（3 次）\n找到过躲起来的人 1 人次")
        self.assertEqual(await self.say("M2", "/档案"), "📒 小二 的游戏档案\n被抓进队 1 次，最常抓到 TA 的是 阿队（1 次）\n经历过：小队长 1 次")
        self.assertEqual(await self.say("M1", "/档案", group=OTHER_GROUP), "小一 还没有游戏档案，玩过一次就有了。")
        self.teams.nickname_of = lambda group, member: {"CA": "阿队改名了"}.get(member)      # the name they go by now
        self.assertIn("最常抓到 TA 的是 阿队改名了（3 次）", await self.say("M1", "/档案"))
        self.teams.history.db.execute("UPDATE tally SET name = ''")                        # a record from before names were kept
        self.assertIn("最常抓的是 群友#M1（3 次）", await self.say("CA", "/档案"))

    async def test_a_roll_and_an_event_go_in_it_too(self):
        events = """  - name: 意外
    chance: 100
    only: [存活]
    set: 死亡
    say: "{members} 出事了。"
"""
        self.make(rules_path=self.rules_file(rules_with(events=events) + """
  - name: 掷骰
    effect: roll
    from: [小队长, 死亡]
"""), rng=Dice(pick=0))
        await self.say("CA", "/抓 @M1")
        await self.say("CA", "/掷骰 @M1")
        await self.chat("M1")
        self.assertEqual(await self.say("M1", "/档案"),
                         "📒 小一 的游戏档案\n被抓进队 1 次，最常抓到 TA 的是 阿队（1 次）\n经历过：小队长 1 次、死亡 1 次\n遇上突发事件 1 次")

    async def test_what_is_kept_and_what_deletion_takes(self):
        self.make()
        await self.say("CA", "/抓 @M1 @M2")
        await self.say("CB", "/抓 @M3")
        self.teams.forget_member(GROUP, "M1")                               # left the group: off today's team,
        self.assertEqual(set(self.team("CA")), {"M2"})
        self.assertEqual(self.teams.history.totals(GROUP, "M1"), {"caught": 1})         # their record kept for now
        self.teams.forget_record(GROUP, "M1")                               # /删除数据, or gone half a year
        self.assertEqual(self.teams.history.totals(GROUP, "M1"), {})
        self.assertEqual(self.teams.history.totals(GROUP, "CA"), {"grabbed": 1})        # and their part in the captain's
        self.assertEqual(self.teams.history.most(GROUP, "CA", "grabbed"), ("M2", 1, "小二"))
        self.teams.forget_record(GROUP, "CA")
        self.assertEqual(self.teams.history.totals(GROUP, "M2"), {})                    # caught by nobody now on record
        await self.say("CB", "/抓 @M4", group=OTHER_GROUP)
        self.teams.forget_group(GROUP)
        self.assertEqual(self.teams.history.totals(GROUP, "M3"), {})
        self.assertEqual(self.teams.history.totals(OTHER_GROUP, "M4"), {"caught": 1})


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

    async def test_and_so_can_hiding_looking_and_the_record(self):
        rules = self.rules_file(rules_with(**{"hide: 躲": "hide: 藏", "seek: 找": "seek: 搜", "record: 档案": "record: 战绩"}))
        self.make(rules_path=rules, rng=Dice(pick=0))
        self.assertIn("队长要用 /搜 地点 才找得到", await self.say("M1", "/藏"))
        self.assertIsNone(await self.say("M1", "/躲"))                        # the old words pass through
        self.assertEqual(await self.say("CA", "/抓 @M1"), "小一 躲起来了，抓不到。用 /搜 地点 去翻翻看吧。")
        self.assertEqual(self.gw.buttons, [])
        self.assertIn("要翻哪儿？/搜 地点", await self.say("CA", "/搜"))
        self.assertEqual(self.gw.buttons[-1][1][0][0], ("柜子里", "/搜 柜子里"))
        self.assertIn("找到了 小一", await self.say("CA", "/搜柜子里"))
        self.assertIn("📒 小一 的游戏档案", await self.say("M1", "/战绩"))
        self.assertEqual(await self.say("M1", "/藏 出来"), "你现在没有躲着哦。")
        block = self.teams.help_block()
        self.assertIn("还没进队的人：/藏 躲起来 · 队长：/搜 地点 去翻，翻对了就抓进队\n"
                      "队员和队长在群里聊天时，偶尔会遇上突发事件\n/战绩 看自己的游戏档案 · /战绩 @某人 看别人的", block)

    async def test_the_log_uses_the_games_own_names(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder)
        rules = os.path.join(folder, "teams.yaml")
        with open(rules, "w", encoding="utf-8") as f:
            f.write(shipped_rules().replace("game: 组队游戏", "game: 寻宝游戏").replace("captain: 队长", "captain: 船长")
                    .replace("  team: 队伍", "  team: 船队"))
        self.make(rules_path=rules)
        await self.say("CA", "/抓 @M1")
        self.now += timedelta(days=1)                       # the next game day: yesterday's team goes
        with self.assertLogs(teams_log, "INFO") as logged:
            scheduler = asyncio.create_task(self.teams.run_scheduler())
            await asyncio.sleep(0)
            scheduler.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await scheduler
        self.assertIn("寻宝游戏已开启：3 位船长，所有群，7 个操作，每天 05:00 清空船队", logged.output[0])
        self.assertIn("新的一天，已清空 1 个群的船队", logged.output[1])


class RulesFileTest(unittest.TestCase):
    """Mistakes in teams.yaml stop the bot at startup with a message its editor can act on."""

    GOOD = shipped_rules()
    SHIPPED_NAMES = "names:\n  game: 组队游戏\n  captain: 队长\n  team: 队伍\n"

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
        self.assertEqual((len(rules.spots), rules.searches, rules.hides), (6, 3, 3))
        self.assertEqual((rules.event_gap, rules.event_daily), (10, 2))
        self.assertEqual([(e.name, e.chance, e.captain, e.effect, e.status, e.only) for e in rules.events],
                         [("溜走", 2.0, False, "kick", "", ()), ("点名", 2.0, True, "", "", ()),
                          ("意外", 1.0, False, "set", "死亡", ("存活",)), ("奇迹", 5.0, False, "set", "存活", ("死亡",))])
        self.assertEqual(rules.commands, {"teams": "队伍", "disband": "解散队伍", "clear": "清空队伍", "hide": "躲",
                                          "seek": "找", "record": "档案"})

    def test_an_older_file_without_the_new_sections_still_loads(self):
        older = re.sub(r"(?ms)^# -{20,} 躲猫猫.*?(?=^# -{20,} 队长的操作)", "", self.GOOD)
        older = older.replace("  hide: 躲\n  seek: 找\n  record: 档案\n", "")
        rules = self.check(older)
        self.assertEqual((rules.spots, rules.events, rules.commands["seek"]), ((), (), "找"))

    def test_mistakes_in_events_and_hiding(self):
        event = "  - name: 溜走\n    chance: 2\n    effect: kick\n    say: \"{members} 溜了\"\n"
        bad = lambda **changed: rules_with(events=event.replace(*next(iter(changed.items()))))
        refuse = lambda old, new, *phrases: self.assertRefused(rules_with(events=event.replace(old, new)), *phrases)
        refuse("chance: 2", "chance: 0", "第 1 件突发事件（溜走）", "chance")
        refuse("chance: 2", "chance: 101", "chance")
        refuse("chance: 2", "chance: 很少", "chance")
        refuse("    chance: 2\n", "", "chance")
        refuse("effect: kick", "effect: grab", "没有叫「grab」的效果", "kick")
        refuse("effect: kick", "effect: kick\n    set: 死亡", "effect 和 set 只能写其中一种")
        refuse("effect: kick", "set: 牺牲", "「牺牲」")
        refuse("effect: kick", "effect: roll", "roll 要用 from")
        refuse("effect: kick", "effect: kick\n    from: [存活]", "只有 effect: roll 才能写 from")
        refuse("effect: kick", "effect: kick\n    only: [存活, 死亡]", "不可能同时满足")
        refuse("effect: kick", "effect: kick\n    speaker: 小队长", "speaker 只能写「队长」")
        refuse("{members} 溜了", "{actor} 溜了", "{actor}", "{members}")
        refuse("    say: \"{members} 溜了\"\n", "", "say 要写")
        refuse("  - name: 溜走", "  - chance: 5", "第 1 件突发事件缺少 name")
        self.assertRefused(rules_with(events=event + event), "第 2 件突发事件（溜走）", "重复")
        self.assertRefused(self.GOOD.replace("events:\n  - name: 溜走", "events: 溜走\nold:\n  - name: 溜走"), "events 要写成列表")
        self.assertRefused(self.GOOD.replace("event_gap: 10", "event_gap: -1"), "event_gap", "不小于 0")
        self.assertRefused(self.GOOD.replace("event_daily: 2", "event_daily: 很多"), "event_daily")
        say_only = self.check(rules_with(events="  - {name: 路过, chance: 0.5, say: [一句, 两句]}"))     # no effect at all
        self.assertEqual((say_only.events[0].effect, say_only.events[0].say, say_only.events[0].chance), ("", ("一句", "两句"), 0.5))
        hide = lambda text, *phrases: self.assertRefused(rules_with(hide=text), *phrases)
        hide("  spots: [柜子里, 床底下]", "hide.spots", "3 到 25")
        hide("  spots: [%s]" % ", ".join(f"地方{n}" for n in range(26)), "3 到 25")
        hide("  spots: [柜子里, 床底下, 柜子里]", "「柜子里」重复了")
        hide("  spots: [柜子里, 床底下, 很长很长很长的一个地方]", "最多 8 个字")
        hide("  spots: [柜子里, 床底下, '书架 后']", "不能带空格")
        hide("  spots: [柜子里, 床底下, 书架后]\n  searches: 0", "hide.searches", "不小于 1")
        hide("  spots: [柜子里, 床底下, 书架后]\n  times: 一次", "hide.times")
        hide("  spot: [柜子里, 床底下, 书架后]", "没有「spot」这一项")
        self.assertRefused(self.GOOD.replace("hide:\n  spots:", "hide: 柜子里\nold:\n  spots:"), "hide 要写成")
        self.assertRefused(self.GOOD.replace("seek: 找", "seek: 躲"), "同一个名字")
        self.assertRefused(self.GOOD.replace("aliases: [阵亡]", "aliases: [档案]"), "内置指令")

    def test_the_games_own_names(self):
        shipped = {"game": "组队游戏", "captain": "队长", "team": "队伍"}
        self.assertEqual(self.check(self.GOOD).names, shipped)
        self.assertEqual(self.check(self.GOOD.replace(self.SHIPPED_NAMES, "")).names, shipped)   # may be left out
        renamed = self.GOOD.replace("game: 组队游戏", "game: 寻宝游戏")
        self.assertEqual(self.check(renamed).names, {**shipped, "game": "寻宝游戏"})
        self.assertRefused(renamed.replace("aliases: [阵亡]", "aliases: [队伍]"), "是寻宝游戏的内置指令")

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
        self.assertRefused(self.GOOD.replace("game: 组队游戏", "boss: 老大"), "names", "「boss」")
        self.assertRefused(self.GOOD.replace("  team: 队伍\n", "  team:\n"), "names.team", "不能是空的")
        self.assertRefused(self.GOOD.replace(self.SHIPPED_NAMES, "names: [寻宝游戏]\n"), "names 要写成")
        self.assertRefused(self.GOOD.replace("{actor} 抓到了", "{who} 抓到了"), "{who}")
        self.assertRefused(self.GOOD.replace("effect: grab", "set: 小队长"), "effect: grab")
        self.assertRefused(self.GOOD.replace('reset_time: "05:00"', "reset_time: 早上五点"), "reset_time")
        self.assertRefused(self.GOOD.replace("hide_from_ai: []", "hide_from_ai: [失踪]"), "hide_from_ai", "「失踪」")
        self.assertRefused(self.GOOD.replace("aliases: [放人]", "aliases: [成员踢出]"), "/成员",
                           reserved=("/成员", "/帮助"))

    def test_a_steal_and_its_mistakes(self):
        steal = self.check(self.GOOD).action("抢人")
        self.assertEqual((steal.effect, steal.need, steal.times, len(steal.say), len(steal.fail)), ("steal", 4, 3, 1, 1))
        bare = self.check(self.GOOD + "\n  - name: 夺人\n    effect: steal\n    only: [存活]\n").action("夺人")
        self.assertEqual((bare.need, bare.times, bare.say, bare.fail, bare.only), (4, 3, (), (), ("存活",)))
        refuse = lambda old, new, *phrases: self.assertRefused(self.GOOD.replace(old, new), *phrases)
        refuse("    need: 4\n", "    need: 0\n", "第 7 个操作（抢人）的 need", "不小于 1")
        refuse("    need: 4\n", "    need: 7\n", "（抢人）的 need 最多写 6")
        refuse("    need: 4\n", "    need: 很大\n", "（抢人）的 need")
        refuse("    times: 3\n", "    times: 0\n", "（抢人）的 times", "不小于 1")
        refuse("    effect: kick\n", "    effect: kick\n    need: 4\n", "只有 effect: steal 才能写 need")
        refuse("    effect: kick\n", "    effect: kick\n    times: 3\n", "只有 effect: steal 才能写 times")
        refuse("    set: 存活\n", "    set: 存活\n    fail: 没成\n", "（复活）：只有 effect: steal 才能写 fail")
        refuse("差一点：{members}", "差一点：{status}", "fail 里的 {status}", "{left}")
        refuse("把 {members} 从", "把 {count} 个人从", "say 里的 {count}")
        refuse("{actor} 抓到了", "{actor} 掷出 {roll} 点抓到了", "{roll}")          # only a steal has a die to show
        refuse("    need: 4\n", "    need: 4\n    from: [存活]\n", "只有 effect: roll 才能写 from")
        self.assertEqual(self.check(self.GOOD.replace("    need: 4\n", "    need: 6\n")).action("抢人").need, 6)

    def test_the_check_command_says_what_is_in_the_file_or_what_is_wrong(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder)
        path = os.path.join(folder, "teams.yaml")

        def run(text):
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            out = io.StringIO()
            return check.main(path, out), out.getvalue()

        self.assertEqual(run(self.GOOD), (0, f"{path} 没有问题：7 个操作，4 件突发事件，6 个躲的地方。\n"))
        code, said = run(self.GOOD.replace("event_gap: 10", "event_gap: -1"))
        self.assertEqual((code, said.startswith("!! teams.yaml 的 event_gap")), (1, True))
        self.assertEqual(run("actions: [")[0], 1)                           # not YAML at all
        self.assertEqual(check.main(os.path.join(folder, "missing.yaml"), io.StringIO()), 1)

    def test_unquoted_reset_time(self):
        rules = self.check(self.GOOD.replace('reset_time: "05:00"', "reset_time: 06:30"))
        self.assertEqual(rules.reset_time.strftime("%H:%M"), "06:30")


if __name__ == "__main__":
    unittest.main()
