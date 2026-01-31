import os

import random

import botpy

import asyncio

import pytz

from botpy import logging

from botpy.ext.cog_yaml import read

from modules.epic_games import EpicGamesModule

from modules.minecraft import MinecraftModule

from modules.steam import SteamModule

from modules.blackjack import BlackjackModule

from modules.sleep import SleepModule

from modules.deepseek import DeepSeekModule

from modules.ai_chat import AIChatModule

from modules.crazy_talk import CrazyTalkModule

test_config = read(os.path.join(os.path.dirname(__file__), "config.yaml"))

_log = logging.get_logger()

class MyClient(botpy.Client):

    def __init__(self, intents):

        super().__init__(intents=intents)

        self.tz = pytz.timezone('Asia/Shanghai')

        self.epic_module = EpicGamesModule(self.api)

        self.minecraft_module = MinecraftModule(self.api)

        self.steam_module = SteamModule(self.api)

        self.blackjack_module = BlackjackModule(self.api)

        self.sleep_module = SleepModule(self.api)

        self.deepseek_module = DeepSeekModule(self.api)

        self.ai_chat_module = AIChatModule(self.api)

        self.crazy_talk_module = CrazyTalkModule(self.api)

        self.epic_task = None

        self.steam_task = None

        self.group_members = {}

    async def on_ready(self):

        _log.info(f"robot 「{self.robot.name}」 on_ready!")

        self.epic_module.epic_task = asyncio.create_task(self.epic_module.schedule_epic_push())

        self.steam_module.steam_task = asyncio.create_task(self.steam_module.schedule_steam_push())

    async def on_group_at_message_create(self, message):

        if await self.epic_module.handle_epic(message):

            return

        if await self.minecraft_module.handle_minecraft(message):

            return

        if await self.steam_module.handle_steam(message):

            return

        if await self.blackjack_module.handle_blackjack(message):

            return

        if await self.sleep_module.handle_sleep(message):

            return

        if await self.deepseek_module.handle_deepseek(message):

            return

        if await self.crazy_talk_module.handle_crazy_talk(message):

            return

        if await self.ai_chat_module.handle_ai_chat(message):

            return

        if "/摇骰子" in message.content:

            dice_number = random.randint(1, 6)

            await message.reply(content=f"你投出了: {dice_number} 点 ")

            return

        _log.info(f"User member_openid: {message.author.member_openid}")

if __name__ == "__main__":

    intents = botpy.Intents(public_messages=True, public_guild_messages=True)

    client = MyClient(intents=intents)

    client.run(appid=test_config["appid"], secret=test_config["secret"])
