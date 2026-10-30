import ast
import glob
import os
import re
import shutil
import tempfile
import unittest
from core import style
from core.config import root_path
from core.style import ADMIN, DEFAULT_STYLE, STYLES, StyleBook, placeholders, t
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

    def test_every_placeholder_is_filled_where_the_line_is_used(self):
        """A {placeholder} in the default style that the t() call doesn't pass would fail mid-reply."""
        base = STYLES.styles[DEFAULT_STYLE]
        problems = []
        for path in glob.glob(root_path("modules", "**", "*.py"), recursive=True) + [root_path("main.py")]:
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "t"
                        and node.args and isinstance(node.args[0], ast.Constant)):
                    continue
                if any(k.arg is None for k in node.keywords):       # t(key, **fields): can't tell from here
                    continue
                given = {k.arg for k in node.keywords} | {"name"}
                # The line and its versions for particular people (key@admin) are filled from the same call.
                for line in StyleBook._versions_in(base, node.args[0].value):
                    for variant in line if isinstance(line, list) else [line]:
                        missing = placeholders(str(variant)) - given
                        if missing:
                            problems.append(f"{os.path.basename(path)}:{node.lineno} {node.args[0].value}: "
                                            f"{sorted(missing)}")
        self.assertEqual(problems, [])

    def test_every_line_has_valid_placeholders(self):
        for key, value in style._leaves(STYLES.styles[DEFAULT_STYLE].data):
            for variant in value if isinstance(value, list) else [value]:
                placeholders(str(variant))     # raises on broken braces

    def test_help_text_is_unchanged(self):
        expected = ("指令列表\n\n【群管理】\n/我的状态 活跃卡片  /排行 本周发言榜（/排行 月）\n/成就 我的成就  /群规 查看群规\n"
                    "/请假 天数 原因  /销假 提前结束请假\n\n【游戏资讯】\n/epicfree 查看Epic免费游戏\n"
                    "/订阅epic 每日12点推送  /epicTD 取消\n/steamfree 查看Steam免费游戏\n/订阅steam 每日18点推送  /steamTD 取消"
                    "\n\n【Minecraft】\n/mc绑定 <地址> 绑定服务器\n/mc状态 查询服务器状态\n/mc解绑 解除绑定\n\n【娱乐】\n"
                    "/21点 开始游戏  /抽卡 要牌  /停止 停牌\n/摇骰子 掷一个骰子\n/发癫 <名称> 随机语录\n\n【互动】\n"
                    "/今日运势 每天一签  /翻牌子 随机翻一位群友的牌子\n/生日 月-日 登记生日  /本月生日 这个月谁过生日\n"
                    "/投票 问题 选项1 选项2…  /投 A  /结束投票\n/征集意见 看看在征集什么  /意见 内容 提意见\n\n【作息】\n"
                    "/晚安 记录入睡  /早安 查看睡眠时长\n\n【AI】\n/服务器QA <问题> 服务器问答\n直接@我聊天即可\n"
                    "/我的记忆 我记得的关于你的事  /忘记 序号 删掉一条\n\n"
                    "提示：所有指令需@我使用")
        self.assertEqual(HelpModule(None, ai_enabled=True, manager_enabled=True)._build_help_text(), expected)

    def test_admins_see_the_admin_commands_in_help(self):
        help = HelpModule(None, ai_enabled=True, manager_enabled=True)
        member = help._build_help_text()
        with STYLES.scope("G", audiences=[ADMIN]):
            admin = help._build_help_text()
            self.assertEqual(admin, member + "\n\n" + t("manager.admin_help"))
            # Without the group manager there are no admin commands to list.
            self.assertNotIn("/管理状态", HelpModule(None, manager_enabled=False)._build_help_text())
        self.assertNotIn("/管理", member)


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


class PrivateStyleTest(unittest.TestCase):
    """local/styles/ holds private versions that never go to GitHub."""

    def test_a_private_default_changes_only_its_own_lines(self):
        shared, private = tempfile.mkdtemp(), tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, shared)
        self.addCleanup(shutil.rmtree, private)
        shutil.copy(root_path("styles", f"{DEFAULT_STYLE}.yaml"), shared)
        with open(os.path.join(shared, "桃子.yaml"), "w", encoding="utf-8") as f:
            f.write("name: 桃子\ncommon:\n  error: 桃子出错了\n")
        with open(os.path.join(private, f"{DEFAULT_STYLE}.yaml"), "w", encoding="utf-8") as f:
            f.write('common:\n  not_admin: 只有管理员可以哦\n  someone: "{oops}"\n')
        with open(os.path.join(private, "桃子.yaml"), "w", encoding="utf-8") as f:
            f.write("name: 桃子\ncommon:\n  not_admin: 私人桃子\n")
        book = StyleBook(directory=shared, local_directory=private)
        book.configure({"group_styles": {"P": "桃子"}})
        with book.scope("G"):
            self.assertEqual(t("common.not_admin"), "只有管理员可以哦")              # the private line
            self.assertEqual(t("common.date", month=1, day=2), "1月2日")             # the rest: shared
            self.assertEqual(t("common.someone"), "某位群友")                        # broken: shared
        with book.scope("P"):                         # the private 桃子 replaced the shared one
            self.assertEqual(t("common.not_admin"), "私人桃子")
            self.assertIn("出错了", t("common.error", command="/x", error="e"))
            self.assertNotIn("桃子出错了", t("common.error", command="/x", error="e"))
        self.assertIn("{oops}", "\n".join(book.check(DEFAULT_STYLE)))


class AudienceTest(unittest.TestCase):
    """Versions of a line for particular people, written next to it as key@audience."""

    PEACH = """
name: 桃子
system_prompt: 你是桃子。
system_prompt@admin: 你是桃子，正在和管理员说话。
common:
  not_admin: 这个只有管理员能用哒～
  not_admin@vip: 贵宾也不行哦～
  someone@admin: "{oops}"
manager:
  leave:
    none@admin: "{mystery}查了一下，管理员没有请假"
  typo@admin: 这一句不存在
  welcome_back@nobody: 没有这一类人
"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        shutil.copy(root_path("styles", f"{DEFAULT_STYLE}.yaml"), self.dir)
        with open(os.path.join(self.dir, "桃子.yaml"), "w", encoding="utf-8") as f:
            f.write(self.PEACH)
        self.book = StyleBook(directory=self.dir)
        self.book.configure({"group_styles": {"G1": "桃子"}, "audiences": {"vip": ["V", "W"], "作者": ["W"]}})

    def say(self, key, *audiences, group="G1", **kwargs):
        with self.book.scope(group, audiences=audiences):
            return t(key, **kwargs)

    def test_each_person_gets_their_version_or_the_line_itself(self):
        self.assertEqual(self.say("common.not_admin"), "这个只有管理员能用哒～")
        self.assertEqual(self.say("common.not_admin", "vip", ADMIN), "贵宾也不行哦～")
        self.assertEqual(self.say("common.not_admin", "作者", ADMIN), "这个只有管理员能用哒～")   # no version for either
        # A version the style leaves out comes from the default, like any other line: still as 桃子.
        self.assertEqual(self.say("help.admin", ADMIN, admin_help="管理指令"), "管理指令")
        self.assertEqual(self.say("help.admin", admin_help="管理指令"), "")
        # A version that can't be filled in falls back to the line for everyone.
        self.assertEqual(self.say("common.someone", ADMIN), self.say("common.someone"))
        with self.book.scope("G1", audiences=[ADMIN]):
            self.assertEqual(style.current().system_prompt, "你是桃子，正在和管理员说话。")
        with self.book.scope("G1"):
            self.assertEqual(style.current().system_prompt, "你是桃子。")

    def test_who_is_in_which_audience_comes_from_config(self):
        self.assertEqual(self.book.audiences_of("W"), ["vip", "作者"])
        self.assertEqual(self.book.audiences_of("X"), [])
        self.book.configure({"audiences": {"a.b": ["X"], ADMIN: ["X"], "好友": "X", "同学": ["X"]}})
        self.assertEqual(self.book.audiences_of("X"), ["同学"])      # the others can't be used

    def test_an_audience_can_be_a_list_kept_elsewhere_in_config(self):
        self.book.configure({"teams": {"captains": ["C1", "C2"]},
                             "audiences": {"captains": "teams.captains", "没有": "teams.nobody"}})
        self.assertEqual(self.book.audiences_of("C2"), ["captains"])
        self.assertEqual(list(self.book.audiences), ["captains"])          # nothing at teams.nobody

    def test_check_names_the_problems(self):
        problems = "\n".join(self.book.check("桃子"))
        self.assertIn("typo@admin：小莲.yaml 里没有 manager.typo 这一句", problems)
        self.assertIn("welcome_back@nobody：没有叫「nobody」的群友类别", problems)
        self.assertIn("none@admin：程序不会填写 {mystery}", problems)
        self.assertIn("someone@admin：程序不会填写 {oops}", problems)
        self.assertNotIn("not_admin@vip", problems)
        self.assertNotIn("system_prompt@admin", problems)


class NoStyleCommandInChatTest(ManagerTestCase):
    async def test_style_command_is_gone(self):
        self.make(FakeGateway())
        message = await self.post("ADMIN", "/风格 桃子")
        self.assertFalse(await self.mgr.handle_manager(message))     # falls through like any unknown text
        self.assertEqual(self.gw.sent, [])
        self.assertFalse(hasattr(StyleBook, "choose"))


if __name__ == "__main__":
    unittest.main()
