import asyncio
from datetime import datetime
import pytz
from core.style import t


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
        await message.reply(content=t("games.sleep.good_night", time=current_time.strftime("%H:%M")))
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
            if hours < 6:
                comment = t("games.sleep.too_short")
            elif hours <= 8:
                comment = t("games.sleep.just_right")
            else:
                comment = t("games.sleep.too_long")
            await message.reply(content=t("games.sleep.good_morning", time=wake_time.strftime("%H:%M"),
                                          hours=hours, minutes=minutes, comment=comment))
        else:
            await message.reply(content=t("games.sleep.no_record"))
        return True
