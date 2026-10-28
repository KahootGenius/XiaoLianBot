"""今日运势, birthdays, polls, 征集意见 and 翻牌子; buttons, under these and the games; the digest's AI
comment; rolls in the team game."""
import os
import random
import shutil
import tempfile
import unittest
from datetime import timedelta
from types import SimpleNamespace
from core.config import root_path
from core.style import DEFAULT_STYLE, STYLES
from modules.ai.ai_chat import AIChatModule
from modules.ai.store import ChatStore
from modules.fun.blackjack import BlackjackModule
from modules.fun.crazy_talk import CrazyTalkModule
from modules.fun.dice import DiceModule
from modules.fun.social import SocialModule
from modules.fun.social.module import parse_birthday, read_flip_special
from modules.fun.social.store import FunStore
from modules.fun.teams.rules import load_rules
from modules.manager.openapi import QQGateway
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
    def make(self, activity=None, config=None, nickname_of=None, rng=None):
        self.now = START
        self.gw = FakeGateway()
        self.store = FunStore(":memory:")
        self.addCleanup(self.store.db.close)
        self.social = SocialModule(self.gw, store=self.store, clock=lambda: self.now,
                                   is_admin=lambda group, member, role=None: member == "ADMIN",
                                   activity=activity, rng=rng or random.Random(7), config=config,
                                   nickname_of=nickname_of)
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
        self.assertEqual(self.gw.buttons[-1], (GROUP, [[("我也抽一签", "/今日运势")]]))
        week = set()
        for day in range(7):
            self.now = START + timedelta(days=day)
            week.add(await self.say("A", "/今日运势"))
        self.assertGreater(len(week), 1)                                    # and a new one tomorrow


class BirthdayTest(SocialTestCase):
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
        self.assertEqual(self.gw.buttons[-1], (GROUP, [[("本月生日", "/本月生日")]]))
        self.assertEqual(await self.say("A", "今天上课好累"), "")             # not the day yet
        self.now = START + timedelta(days=1)
        wish = await self.say("A", "早上好")                               # either of the style's two wishes
        self.assertIn("小明", wish)
        self.assertIn("生日快乐", wish)
        self.assertEqual(await self.say("A", "谢谢大家"), "")                 # once a day
        self.assertIn("11 月过生日的群友（1 位）：\n11月2日 小明", await self.say("B", "/本月生日"))
        self.assertIn("删掉了", await self.say("A", "/生日 删除"))
        self.assertIn("还没有人登记", await self.say("B", "/本月生日"))
        self.assertEqual(self.gw.buttons[-1], (GROUP, [[("登记我的生日", "/生日 ")]]))     # typed in, for the date

    async def test_leap_day_birthdays_come_on_the_28th(self):
        self.make()
        await self.say("A", "/生日 2-29")
        self.now = START.replace(year=2027, month=2, day=28)
        self.assertNotEqual(await self.say("A", "早"), "")


class TodayForAITest(SocialTestCase):
    async def test_ai_chat_hears_of_todays_birthdays_and_the_open_poll(self):
        self.make()
        self.assertEqual(self.social.today_for_ai(GROUP), {"birthdays": [], "poll": None, "opinions": None})
        await self.say("A", "/生日 11-1")                     # today
        await self.say("B", "/生日 11-2")
        await self.say("A", "/投票 周六几点开？ 19点 20点")
        await self.say("B", "/投 A")
        self.assertEqual(self.social.today_for_ai(GROUP),
                         {"birthdays": ["小明"], "poll": ("周六几点开？", [("A", "19点", 1), ("B", "20点", 0)]),
                          "opinions": None})
        self.now += timedelta(days=8)                         # the poll has run out of time
        self.assertEqual(self.social.today_for_ai(GROUP), {"birthdays": [], "poll": None, "opinions": None})

    async def test_and_of_the_open_call_for_opinions(self):
        self.make()
        await self.say("ADMIN", "/征集意见 下个月玩什么？")
        await self.say("A", "/意见 狼人杀")
        self.assertEqual(self.social.today_for_ai(GROUP)["opinions"], ("下个月玩什么？", 1))
        self.now += timedelta(days=30)                        # nobody ended it: it closes by itself
        self.assertIsNone(self.social.today_for_ai(GROUP)["opinions"])
        self.assertIn("没有在征集意见", await self.say("A", "/征集意见"))


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


class OpinionTest(SocialTestCase):
    async def test_a_call_for_opinions_from_start_to_end(self):
        self.make()
        self.assertIn("没有在征集意见", await self.say("A", "/征集意见"))
        self.assertIn("只有管理员", await self.say("A", "/征集意见 下个月玩什么？"))
        started = await self.say("ADMIN", "/征集意见 下个月玩什么？")
        self.assertIn("📮 管理员 发起了意见征集：下个月玩什么？", started)
        self.assertIn("每人最多 3 条", started)
        self.assertEqual(self.gw.buttons[-1], (GROUP, [[("提意见", "/意见 ")]]))     # typed in, for the opinion
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


class FlipTest(SocialTestCase):
    async def test_this_weeks_active_members_weighted(self):
        rows = [{"member_openid": "B", "nickname": "小红", "n": 1000}, {"member_openid": "C", "nickname": "小刚", "n": 1},
                {"member_openid": "A", "nickname": "小明", "n": 5000}]
        self.make(activity=lambda group, since: rows)
        self.assertIn("小红", await self.say("A", "/翻牌子"))                 # 1000 messages against 1; never themself
        self.assertEqual(self.gw.buttons[-1], (GROUP, [[("我也翻一张", "/翻牌子")]]))
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

    def test_reading_the_tables(self):
        tables = read_flip_special({"A": {"B": 60, "C": "25%", "D": 0, "E": "很多", "A": 50, "F": -3, "G": ".nan"},
                                    "H": "不是名单", "I": {"B": 0}})
        self.assertEqual(tables, {"A": {"B": 60.0, "C": 25.0}})
        self.assertEqual(read_flip_special(None), {})
        self.assertEqual(read_flip_special(["A"]), {})


class ButtonsTest(unittest.IsolatedAsyncioTestCase):
    def make_api(self, refuse):
        calls = []

        async def post_group_message(**kwargs):
            calls.append(kwargs)
            if refuse and kwargs.get("msg_type") == 2:
                raise RuntimeError("custom keyboard not enabled")
        return SimpleNamespace(post_group_message=post_group_message), calls

    async def test_buttons_go_under_the_reply(self):
        api, calls = self.make_api(refuse=False)
        await QQGateway(api).send("G", "运势", reply_to="m1", buttons=[[("我也抽", "/今日运势"), ("抓人", "/抓 ")]])
        self.assertEqual([c["msg_type"] for c in calls], [0, 2])
        self.assertEqual([c["msg_seq"] for c in calls], [1, 2])
        buttons = calls[1]["keyboard"]["content"]["rows"][0]["buttons"]
        self.assertEqual([(b["action"]["data"], b["action"]["enter"]) for b in buttons],
                         [("/今日运势", True), ("/抓", False)])            # "/抓 " is only typed in

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
        self.assertEqual(await play(DiceModule(None, gateway=gw), "handle_dice", "/摇骰子"), [[("再摇一次", "/摇骰子")]])
        self.assertEqual(await play(CrazyTalkModule(None, gateway=gw), "handle_crazy_talk", "/发癫 小红"),
                         [[("再来一条", "/发癫 小红")]])

    async def test_refused_buttons_fall_back_to_text_for_a_while(self):
        api, calls = self.make_api(refuse=True)
        gateway = QQGateway(api)
        await gateway.send("G", "运势", reply_to="m1", buttons=[[("我也抽", "/今日运势")]])
        await gateway.send("G", "运势", reply_to="m2", buttons=[[("我也抽", "/今日运势")]])
        self.assertEqual([c["msg_type"] for c in calls], [0, 2, 0])         # tried once, then text only
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
