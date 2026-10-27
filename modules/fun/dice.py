import random
from core.reply import reply
from core.style import t


class DiceModule:
    """/摇骰子 -- moved out of main.py so every command lives in a module."""

    def __init__(self, api_client, gateway=None):
        self.api = api_client
        self.gw = gateway       # replies through it can carry buttons

    async def handle_dice(self, message):
        if "/摇骰子" in message.content:
            await reply(message, t("games.dice", number=random.randint(1, 6)), self.gw,
                        [[(t("games.dice_again"), "/摇骰子")]])
            return True
        return False
