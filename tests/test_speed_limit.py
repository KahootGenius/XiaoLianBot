import unittest
from modules.fun.speed_limit import COMMANDS, SpeedLimit
from tests.test_manager import GROUP, FakeGateway, FakeMessage


class SpeedLimitTest(unittest.IsolatedAsyncioTestCase):
    def make(self, config=None):
        self.now = 1000.0
        self.gw = FakeGateway()
        return SpeedLimit(self.gw, {"games": {"per_minute": 3}} if config is None else config, clock=lambda: self.now)

    async def play(self, limit, text="/21点", member="A", group=GROUP):
        return await limit.handle_speed_limit(FakeMessage(group, member, member, text, content=text))

    async def test_past_the_limit_they_are_told_once_then_ignored(self):
        limit = self.make()
        self.assertEqual([await self.play(limit, command) for command in ("/21点", "/抽卡", "/停止")], [False] * 3)
        self.now += 20
        self.assertTrue(await self.play(limit, "/21点"))
        self.assertEqual(self.gw.texts(GROUP), ["玩得太快啦，歇 40 秒再来～"])
        self.assertTrue(await self.play(limit, "/摇骰子"))
        self.assertEqual(len(self.gw.texts(GROUP)), 1)                  # answering every try would flood too

        self.assertFalse(await self.play(limit, member="B"))            # everyone has their own limit
        self.assertFalse(await self.play(limit, group="OTHER"))

        self.now += 41
        self.assertFalse(await self.play(limit))
        for _ in range(2):
            await self.play(limit)
        self.assertTrue(await self.play(limit))
        self.assertEqual(len(self.gw.texts(GROUP)), 2)                  # told again the next time

    async def test_only_game_commands_count(self):
        limit = self.make()
        for _ in range(5):
            self.assertFalse(await self.play(limit, "/今日运势"))
        for command in COMMANDS:
            limit = self.make()
            for _ in range(3):
                await self.play(limit, f"{command} 小红")
            self.assertTrue(await self.play(limit, f"{command} 小红"), command)

    async def test_config(self):
        limit = self.make({"games": {"per_minute": 0}})                 # switched off
        for _ in range(50):
            self.assertFalse(await self.play(limit))
        self.assertEqual(self.make({}).per_minute, 10)
        self.assertEqual(self.make({"games": {"per_minute": "很多"}}).per_minute, 10)


if __name__ == "__main__":
    unittest.main()
