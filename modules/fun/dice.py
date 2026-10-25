import random
from core.style import t


class DiceModule:
    """/摇骰子 -- moved out of main.py so every command lives in a module."""

    def __init__(self, api_client):
        self.api = api_client

    async def handle_dice(self, message):
        if "/摇骰子" in message.content:
            await message.reply(content=t("games.dice", number=random.randint(1, 6)))
            return True
        return False
