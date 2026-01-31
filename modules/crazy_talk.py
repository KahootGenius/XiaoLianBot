import os

import json

import random

from botpy import logging

_log = logging.get_logger()

class CrazyTalkModule:

    def __init__(self, api):

        self.api = api

        self.json_folder = os.path.join(os.path.dirname(os.path.dirname(__file__)), "gobananas")

        self.all_quotes = self._load_json("all.json")

        self.psycho_quotes = self._load_json("psycho.json")

    def _load_json(self, filename):

        """加载JSON文件中的语录"""

        try:

            file_path = os.path.join(self.json_folder, filename)

            with open(file_path, 'r', encoding='utf-8') as f:

                return json.load(f)

        except Exception as e:

            _log.error(f"加载{filename}失败: {e}")

            return []

    def _replace_name(self, text, name):

        """替换文本中的名称变量"""

        text = text.replace("<name>", name)

        text = text.replace("{target_name}", name)

        return text

    def get_random_quote(self, name, quote_type="all"):

        """获取随机语录并替换名称"""

        quotes = self.all_quotes if quote_type == "all" else self.psycho_quotes

        if not quotes:

            return f"没有找到{quote_type}类型的语录"

        quote = random.choice(quotes)

        return self._replace_name(quote, name)

    async def handle_crazy_talk(self, message):

        """处理发癫命令"""

        content = message.content

        if "/发癫" in content:

            parts = content.split("/发癫", 1)

            if len(parts) > 1:

                target_name = parts[1].strip()

                if not target_name:

                    await message.reply(content="请输入对象名称，例如：/发癫 小明")

                    return True

                quote_type = random.choice(["all", "psycho"])

                quote = self.get_random_quote(target_name, quote_type)

                await message.reply(content=quote)

                return True

            else:

                await message.reply(content="请输入对象名称，例如：/发癫 小明")

                return True

        return False
