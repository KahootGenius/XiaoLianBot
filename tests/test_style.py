import glob
import os
import re
import shutil
import tempfile
import unittest
from core import style
from core.config import root_path
from core.style import DEFAULT_STYLE, STYLES, StyleBook, placeholders, t
from modules.help import HelpModule
from tests.test_manager import GROUP, FakeGateway, ManagerTestCase


class DefaultStyleTest(unittest.TestCase):
    def test_every_key_the_code_asks_for_exists(self):
        base = STYLES.styles[DEFAULT_STYLE]
        missing, prefixes = [], set()
        for path in glob.glob(root_path("modules", "**", "*.py"), recursive=True) + [root_path("main.py")]:
            with open(path, encoding="utf-8") as f:
                source = f.read()
            for key in re.findall(r'\bt\(\s*"([a-z_.]+)"', source):
                if base.own(key) is None:
                    missing.append(f"{os.path.basename(path)}: {key}")
            prefixes.update(re.findall(r'\bt\(\s*f"([a-z_.]+)\.\{', source))
        self.assertEqual(missing, [])
        for prefix in prefixes:     # keys built at runtime, like f"manager.modes.{mode}"
            self.assertIsInstance(base.own(prefix), dict, prefix)

    def test_every_line_has_valid_placeholders(self):
        for key, value in style._leaves(STYLES.styles[DEFAULT_STYLE].data):
            for variant in value if isinstance(value, list) else [value]:
                placeholders(str(variant))     # raises on broken braces

    def test_help_text_is_unchanged(self):
        expected = ("指令列表\n\n【群管理】\n/我的状态 活跃卡片  /排行 本周发言榜（/排行 月）\n/成就 我的成就  /群规 查看群规\n"
                    "/请假 天数 原因  /销假 提前结束请假\n\n【游戏资讯】\n/epicfree 查看Epic免费游戏\n"
                    "/订阅epic 每日12点推送  /epicTD 取消\n/steamfree 查看Steam免费游戏\n/订阅steam 每日18点推送  /steamTD 取消"
                    "\n\n【Minecraft】\n/mc绑定 <地址> 绑定服务器\n/mc状态 查询服务器状态\n/mc解绑 解除绑定\n\n【娱乐】\n"
                    "/21点 开始游戏  /抽卡 要牌  /停止 停牌\n/摇骰子 掷一个骰子\n/发癫 <名称> 随机语录\n\n【作息】\n"
                    "/晚安 记录入睡  /早安 查看睡眠时长\n\n【AI】\n/服务器QA <问题> 服务器问答\n直接@我聊天即可\n"
                    "/我的记忆 我记得的关于你的事  /忘记 序号 删掉一条\n\n"
                    "提示：所有指令需@我使用\n管理员指令：/管理帮助")
        self.assertEqual(HelpModule(None, ai_enabled=True, manager_enabled=True)._build_help_text(), expected)


class CustomStyleTest(unittest.TestCase):
    """A second style file that only rewrites a few lines, as a user would start one."""

    PEACH = """
name: 桃子
system_prompt: 你是桃子。
common:
  not_admin: 这个只有管理员能用哒～
manager:
  status:
    title: "🍑 {member} 的桃子卡片"
  leave:
    none: "{name}查了一下，你没有在请假哟"
    usage: "用法写错啦 {mystery}"
  welcome_back: "又见面啦～ {member.__class__}"
  typo_key: 这一项不存在
"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        shutil.copy(root_path("styles", f"{DEFAULT_STYLE}.yaml"), self.dir)
        with open(os.path.join(self.dir, "桃子.yaml"), "w", encoding="utf-8") as f:
            f.write(self.PEACH)
        self.book = StyleBook(directory=self.dir)
        self.book.configure({"group_styles": {"G1": "桃子"}})

    def test_lines_come_from_the_groups_style(self):
        with self.book.scope("G1"):
            self.assertEqual(t("common.not_admin"), "这个只有管理员能用哒～")
            self.assertEqual(t("manager.leave.none"), "桃子查了一下，你没有在请假哟")
            self.assertEqual(style.current().system_prompt, "你是桃子。")
        with self.book.scope("G2"):                       # another group keeps the default
            self.assertEqual(t("common.not_admin"), "这个指令只有管理员能用哦～")

    def test_missing_and_broken_lines_fall_back_to_the_default(self):
        with self.book.scope("G1"):
            # Not written in 桃子.yaml: 小莲's wording, but {name} is still 桃子.
            self.assertIn("桃子不会催你", t("manager.leave.granted", member="小明", until="11月1日"))
            # {mystery} is never filled, {member.__class__} reaches into an object: both fall back.
            self.assertIn("单次最长 90 天", t("manager.leave.usage", max=90))
            self.assertIn(t("manager.welcome_back"), ("看到你啦～提醒解除，桃子记下了！", "欢迎回来！这次的提醒就作废啦～"))

    def test_check_names_the_problems(self):
        problems = "\n".join(self.book.check("桃子"))
        self.assertIn("typo_key", problems)
        self.assertIn("{mystery}", problems)
        self.assertNotIn("not_admin", problems)
        self.assertIn("manager.leave.granted", self.book.missing("桃子"))

    def test_styles_are_chosen_in_config_only(self):
        self.book.configure({"style": "桃子", "group_styles": {"G2": "小莲", "G3": "葡萄"}})
        self.assertEqual(self.book.for_group("G1").file_name, "桃子")        # the new default
        self.assertEqual(self.book.for_group("G2").file_name, DEFAULT_STYLE)
        self.assertEqual(self.book.for_group("G3").file_name, "桃子")        # no 葡萄.yaml: default
        self.book.configure({"style": "葡萄", "group_styles": ["G1"]})       # both wrong: ignored
        self.assertEqual(self.book.for_group("G1").file_name, DEFAULT_STYLE)


class NoStyleCommandInChatTest(ManagerTestCase):
    async def test_style_command_is_gone(self):
        self.make(FakeGateway())
        message = await self.post("ADMIN", "/风格 桃子")
        self.assertFalse(await self.mgr.handle_manager(message))     # falls through like any unknown text
        self.assertEqual(self.gw.sent, [])
        self.assertFalse(hasattr(StyleBook, "choose"))


if __name__ == "__main__":
    unittest.main()
