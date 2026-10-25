import unittest
from aiohttp.test_utils import TestClient, TestServer
from modules.manager.dashboard import Dashboard
from tests.test_manager import ADMIN_GROUP, GROUP, STICKER, FakeGateway, ManagerTestCase


class MemberTableTest(ManagerTestCase):
    async def asyncSetUp(self):
        self.make(FakeGateway())
        self.at(0, 12)
        await self.post("A", STICKER)
        await self.post("A", "这个问题我觉得可以用第二种方法解决")
        self.at(0, 13)
        await self.post("B", "我也遇到过这个问题，重装依赖就好了")
        await self.post("B", STICKER)
        await self.post("C", "/我的状态", command=True, mentions_bot=True)
        await self.post("D", "=HYPERLINK(\"http://example.com\")", nickname="=坏昵称")
        self.at(3, 9)

    def rows(self):
        return {r["member_openid"]: r for r in self.mgr.member_table(GROUP)}

    def test_latest_message_and_whether_it_counted(self):
        rows = self.rows()
        self.assertEqual((rows["A"]["latest_valid"], rows["A"]["latest_kind"]), (True, "讨论"))
        self.assertEqual((rows["B"]["latest_valid"], rows["B"]["latest_kind"]), (False, "表情"))
        self.assertEqual(rows["B"]["snippet"], "我也遇到过这个问题，重装依赖就好了")
        self.assertEqual(rows["B"]["last_valid_at_text"], "11-01 13:00")
        self.assertEqual((rows["C"]["latest_valid"], rows["C"]["latest_kind"]), (False, "指令"))
        self.assertEqual(rows["C"]["last_valid_at"], None)
        self.assertEqual(rows["A"]["latest_at_text"], "11-01 12:00")
        self.assertEqual(rows["A"]["days"], 3)
        self.assertEqual(rows["A"]["valid_total"], 1)

    def test_status_follows_the_rule(self):
        self.mgr.settings.admins.add("A")
        self.store.set_exempt(GROUP, "B", True)
        rows = self.rows()
        self.assertEqual((rows["A"]["status"], rows["A"]["status_key"]), ("管理员", "exempt"))
        self.assertEqual((rows["B"]["status"], rows["B"]["status_key"]), ("豁免", "exempt"))
        self.assertEqual((rows["C"]["status"], rows["C"]["status_key"]), ("正常", "ok"))

    async def test_http_endpoints(self):
        dashboard = Dashboard(self.mgr, token="secret")
        async with TestClient(TestServer(dashboard.app())) as client:
            self.assertEqual((await client.get("/api/members")).status, 403)
            self.assertEqual((await client.get("/?token=wrong")).status, 403)

            page = await client.get("/?token=secret")
            self.assertEqual(page.status, 200)
            html = await page.text()
            self.assertIn("成员活跃表", html)
            self.assertNotIn("innerHTML", html)          # member text is only ever set as textContent

            data = await (await client.get("/api/members?token=secret")).json()
            self.assertEqual(data["group"], GROUP)
            self.assertEqual({r["member_openid"] for r in data["rows"]}, {"A", "B", "C", "D"})

            self.assertEqual((await client.get("/api/members?token=secret&group=OTHER")).status, 404)

            csv_text = await (await client.get("/members.csv?token=secret")).text()
            self.assertTrue(csv_text.startswith("﻿昵称,状态"))
            self.assertIn("'=坏昵称", csv_text)           # defused, not a formula
            self.assertNotIn(",=坏昵称", csv_text)

    async def test_refuses_to_listen_publicly_without_a_token(self):
        self.assertIsNone(await Dashboard(self.mgr, host="0.0.0.0", token="").start())

    async def test_tunnels_need_a_token(self):
        """A tunnel connects from 127.0.0.1 too; its host name and headers give it away."""
        async with TestClient(TestServer(Dashboard(self.mgr).app())) as client:
            self.assertEqual((await client.get("/api/members")).status, 200)
            tunnelled = await client.get("/api/members", headers={"X-Forwarded-For": "203.0.113.9"})
            self.assertEqual(tunnelled.status, 403)
            public_name = await client.get("/api/members", headers={"Host": "xiaolian.cpolar.top"})
            self.assertEqual(public_name.status, 403)
            self.assertIn("token", await public_name.text())

    # ---- the same table as QQ commands ----

    async def ask(self, text, group=ADMIN_GROUP, **kwargs):
        await self.post("ADMIN", text, group=group, command=True, **kwargs)
        return self.gw.texts(group)[-1]

    async def test_member_table_command(self):
        table = await self.ask("/成员表")
        self.assertIn("📋 成员活跃表 · 第 1/1 页", table)
        self.assertIn("共 4 人", table)                # the admin group itself isn't tracked
        self.assertIn("✗表情", table)
        self.assertIn("筛选：", table)
        invalid = await self.ask("/成员表 无效")
        self.assertIn("（无效）", invalid)
        self.assertIn("小B", invalid)
        self.assertNotIn("小A", invalid)

    async def test_member_table_pages(self):
        for i in range(10):
            await self.post(f"N{i}", f"新人{i}号报到，请多指教，以后常来聊天")
        first = await self.ask("/成员表")
        self.assertIn("第 1/2 页", first)
        self.assertIn("下一页：/成员表 2", first)
        second = await self.ask("/成员表 2")
        self.assertIn("第 2/2 页", second)
        self.assertIn("9. ", second)

    async def test_member_detail_command(self):
        card = await self.ask("/成员 小B")
        self.assertIn("👤 小B", card)
        self.assertIn("✗ 不算（表情）", card)
        self.assertIn("「我也遇到过这个问题，重装依赖就好了」", card)
        self.assertIn("找到 3 位", await self.ask("/成员 小"))
        self.assertIn("没找到", await self.ask("/成员 不存在的人"))
        by_mention = await self.ask("/成员 @小A", group=GROUP, mentioned_members=[("A", "小A")])
        self.assertIn("为了不在大群里公开", by_mention)     # an admin group exists, so not here

    async def test_member_table_in_main_group_without_admin_group(self):
        self.mgr.settings.report_group = ""
        table = await self.ask("/成员表", group=GROUP)
        self.assertIn("📋 成员活跃表", table)


if __name__ == "__main__":
    unittest.main()
