from openai import OpenAI

import yaml

import os

class AIChatModule:

    def __init__(self, api_client):

        self.api = api_client

        self.config = self._load_config()

        self.openai_client = OpenAI(

            api_key=self.config['deepseek']['api_key'],

            base_url="https://api.deepseek.com"

        )

        self.qa_client = OpenAI(

            api_key=self.config['deepseek'].get('qa_api_key', self.config['deepseek']['api_key']),

            base_url="https://api.deepseek.com"

        )

        self.qa_system_prompt = self._load_qa_prompt()

    def _load_config(self):

        config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")

        with open(config_path, 'r', encoding='utf-8') as f:

            return yaml.safe_load(f)

    def _load_qa_prompt(self):

        """从专门的提示词文件加载服务器QA的system提示词"""

        default_prompt = "你是一个专业的Minecraft服务器管理助手，擅长回答有关服务器配置、管理和故障排除的问题。请提供准确、专业的回答。"

        prompt_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts", "server_qa_prompt.yaml")

        try:

            if os.path.exists(prompt_path):

                with open(prompt_path, 'r', encoding='utf-8') as f:

                    prompt_data = yaml.safe_load(f)

                    return prompt_data.get('system_prompt', default_prompt)

            else:

                return default_prompt

        except Exception as e:

            print(f"加载提示词文件出错: {str(e)}")

            return default_prompt

    async def handle_ai_chat(self, message):

        if "/服务器QA" in message.content:

            return await self.handle_server_qa(message)

        try:

            response = self.openai_client.chat.completions.create(

                model="deepseek-chat",

                messages=[

                    {"role": "system", "content": "你是一个友好的助手，名字雨喵，是一名女大学生，性格呆萌但是心思细腻，有点小傲娇，待人友善且会用简短的语言回答问题，记住自己的名字和人设。"},

                    {"role": "user", "content": message.content}

                ],

                stream=False

            )

            reply_content = response.choices[0].message.content

            await message.reply(content=reply_content)

            return True

        except Exception as e:

            await message.reply(content=f"AI回复出错：{str(e)}")

            return True

    async def handle_server_qa(self, message):

        """处理服务器QA模式，使用专门的system提示词"""

        try:

            question = message.content.replace("/服务器QA", "").strip()

            if not question:

                await message.reply(content="请在指令后提供您的问题，例如：/服务器QA 如何重启服务器？")

                return True

            response = self.qa_client.chat.completions.create(

                model="deepseek-chat",          

                messages=[

                    {"role": "system", "content": self.qa_system_prompt},

                    {"role": "user", "content": question}

                ],

                stream=False

            )

            reply_content = response.choices[0].message.content

            await message.reply(content=reply_content)

            return True

        except Exception as e:

            await message.reply(content=f"服务器QA回复出错：{str(e)}")

            return True
