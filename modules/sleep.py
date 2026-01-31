import asyncio

from datetime import datetime

import pytz

class SleepModule:

    def __init__(self, api_client):

        self.api = api_client

        self.sleep_records = {}

        self.tz = pytz.timezone('Asia/Shanghai')

    async def clear_sleep_record(self, user_id):

        await asyncio.sleep(24 * 3600)

        if user_id in self.sleep_records:

            del self.sleep_records[user_id]

    async def handle_sleep(self, message):

        if "/晚安" in message.content:

            return await self._record_sleep(message)

        elif "/早安" in message.content:

            return await self._record_wake(message)

        return False

    async def _record_sleep(self, message):

        current_time = datetime.now(self.tz)

        user_id = f"{message.group_openid}_{message.author.member_openid}"

        self.sleep_records[user_id] = {

            'sleep_time': current_time,

            'timer': asyncio.create_task(self.clear_sleep_record(user_id))

        }

        formatted_time = current_time.strftime("%H:%M")

        await message.reply(content=f"晚安！现在是{formatted_time}，祝你好梦~ 🌙")

        return True

    async def _record_wake(self, message):

        user_id = f"{message.group_openid}_{message.author.member_openid}"

        if user_id in self.sleep_records:

            wake_time = datetime.now(self.tz)

            sleep_time = self.sleep_records[user_id]['sleep_time']

            self.sleep_records[user_id]['timer'].cancel()

            sleep_duration = wake_time - sleep_time

            hours = sleep_duration.seconds // 3600

            minutes = (sleep_duration.seconds % 3600) // 60

            del self.sleep_records[user_id]

            formatted_wake_time = wake_time.strftime("%H:%M")

            await message.reply(

                content=f"早安！现在是{formatted_wake_time}。\n"

                       f"你睡了{hours}小时{minutes}分钟。\n"

                       f"{'要注意休息哦！' if hours < 6 else '睡眠时间刚刚好！' if hours <= 8 else '睡得有点久哦！'}"

            )

        else:

            await message.reply(content="你还没有记录睡眠时间呢，请先使用 /晚安 指令~")

        return True
