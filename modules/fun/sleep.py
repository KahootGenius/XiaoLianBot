import asyncio
from datetime import datetime
import pytz
from core.style import t


# A night of at least the first and under the second many hours between /晚安 and /早安
# counts toward a badge (the "sleep" counter in achievements.yaml).
GOOD_NIGHT_HOURS = (7, 9)


class SleepModule:
    def __init__(self, api_client, clock=None, count=None):
        self.api = api_client
        self.sleep_records = {}
        self.tz = pytz.timezone('Asia/Shanghai')
        self.clock = clock or (lambda: datetime.now(self.tz))
        # (group, member_openid, nickname, counter) -> congratulations for any badge that earned.
        # main.py sets the group manager's.
        self.count = count or (lambda group, member, nickname, key: [])

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
        current_time = self.clock()
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
            wake_time = self.clock()
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
            text = t("games.sleep.good_morning", time=wake_time.strftime("%H:%M"),
                     hours=hours, minutes=minutes, comment=comment)
            if GOOD_NIGHT_HOURS[0] <= hours < GOOD_NIGHT_HOURS[1]:
                text = "\n".join([text, *self.count(message.group_openid, message.author.member_openid,
                                                    getattr(message, "nickname", None), "sleep")])
            await message.reply(content=text)
        else:
            await message.reply(content=t("games.sleep.no_record"))
        return True
