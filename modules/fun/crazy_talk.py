import os
import json
import random
from botpy import logging
from core.config import root_path
from core.reply import reply
from core.style import t


_log = logging.get_logger()


class CrazyTalkModule:
    def __init__(self, api, gateway=None):
        self.api = api
        self.gw = gateway       # replies through it can carry buttons
        self.json_folder = root_path("assets", "quotes")
        self.all_quotes = self._load_json("all.json")
        self.psycho_quotes = self._load_json("psycho.json")

    def _load_json(self, filename):
        """Load the quote list from a JSON file."""
        try:
            file_path = os.path.join(self.json_folder, filename)
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            _log.error(f"加载{filename}失败: {e}")
            return []

    def _replace_name(self, text, name):
        """Substitute the target's name into the quote's name placeholders."""
        text = text.replace("<name>", name)
        text = text.replace("{target_name}", name)
        return text

    def get_random_quote(self, name, quote_type="all"):
        """Pick a random quote and substitute the target's name into it."""
        quotes = self.all_quotes if quote_type == "all" else self.psycho_quotes
        if not quotes:
            return t("games.crazy_talk.no_quotes", type=quote_type)
        quote = random.choice(quotes)
        return self._replace_name(quote, name)

    async def handle_crazy_talk(self, message):
        """Handle the /发癫 ("go crazy") command."""
        content = message.content
        if "/发癫" in content:
            parts = content.split("/发癫", 1)
            if len(parts) > 1:
                target_name = parts[1].strip()
                if not target_name:
                    await message.reply(content=t("games.crazy_talk.usage"))
                    return True
                quote_type = random.choice(["all", "psycho"])
                quote = self.get_random_quote(target_name, quote_type)
                await reply(message, quote, self.gw, [[(t("games.crazy_talk.again"), f"/发癫 {target_name}")]])
                return True
            else:
                await message.reply(content=t("games.crazy_talk.usage"))
                return True
        return False
