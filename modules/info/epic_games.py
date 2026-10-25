import asyncio
from datetime import datetime, timedelta
import pytz
from requests import get
from core.style import STYLES, t


class EpicGamesModule:
    def __init__(self, api_client):
        self.api = api_client
        self.epic_subscribers = set()
        self.epic_task = None
        # (title, price in yuan, promotion period) of the games currently free / free soon.
        self.now = []
        self.soon = []
        self.tz = pytz.timezone('Asia/Shanghai')

    def _get_title(self, item):
        s = item.get("title", "")
        lst = s.strip("《").split("》")
        return "".join(lst)

    def _get_original_price(self, item):
        details = item.get("price", {}).get("totalPrice", {})
        return int(details.get("originalPrice", 0))

    def _get_time(self, item):
        if item.get("promotions", None) is not None:
            if len(item["promotions"]["promotionalOffers"]) != 0:
                startDate = item["promotions"]["promotionalOffers"][0]['promotionalOffers'][0]['startDate'].split("T")[0]
                endDate = item["promotions"]["promotionalOffers"][0]['promotionalOffers'][0]['endDate'].split("T")[0]
                return f"{startDate}——{endDate}"
            elif len(item["promotions"]['upcomingPromotionalOffers']) != 0:
                startDate = item["promotions"]["upcomingPromotionalOffers"][0]['promotionalOffers'][0]['startDate'].split("T")[0]
                endDate = item["promotions"]["upcomingPromotionalOffers"][0]['promotionalOffers'][0]['endDate'].split("T")[0]
                return f"{startDate}——{endDate}"
        return ""

    def _entry(self, item):
        return (self._get_title(item), self._get_original_price(item) / 100, self._get_time(item))

    def _is_free(self, item):
        if self._get_original_price(item) == 0:
            return False
        if item.get("promotions", None) is not None:
            if len(item["promotions"]["promotionalOffers"]) != 0:
                if item["promotions"]["promotionalOffers"][0]["promotionalOffers"][0]["discountSetting"]["discountPercentage"] == 0:
                    self.now.append(self._entry(item))
                    return True
            elif len(item["promotions"]['upcomingPromotionalOffers']) != 0:
                if item["promotions"]['upcomingPromotionalOffers'][0]["promotionalOffers"][0]["discountSetting"]["discountPercentage"] == 0:
                    self.soon.append(self._entry(item))
                    return True
        return False

    async def _fetch(self):
        url = "https://store-site-backend-static-ipv4.ak.epicgames.com/freeGamesPromotions?locale=zh-CN&country=CN&allowCountries=CN"
        response = await asyncio.get_event_loop().run_in_executor(None, get, url)
        hashMap = response.json()
        ls = hashMap["data"]["Catalog"]["searchStore"]["elements"]
        self.now = []
        self.soon = []
        for item in ls:
            if self._is_free(item):
                continue

    def _format(self):
        """The fetched games as a message, in the current style."""
        message = t("info.epic.title") + "\n"
        for label, games in (("info.epic.now", self.now), ("info.epic.soon", self.soon)):
            if games:
                message += "\n" + t(label) + "\n"
                for title, price, period in games:
                    message += t("info.epic.item", title=title, price=price, period=period) + "\n"
        return message

    async def get_epic_free_games(self):
        await self._fetch()
        return self._format()

    async def schedule_epic_push(self):
        while True:
            now = datetime.now(self.tz)
            next_run = now.replace(hour=12, minute=0, second=0, microsecond=0)
            if now >= next_run:
                next_run += timedelta(days=1)
            await asyncio.sleep((next_run - now).seconds)
            await self._fetch()
            for group_id in self.epic_subscribers:
                # Fetched once, worded in each group's own style.
                with STYLES.scope(group_id):
                    message = self._format()
                await self.api.post_group_message(
                    group_openid=group_id,
                    msg_type=1,
                    content=message
                )

    async def handle_epic(self, message):
        if "/epicfree" in message.content:
            message_content = await self.get_epic_free_games()
            await message.reply(content=message_content)
            return True
        if "订阅epic" in message.content:
            self.epic_subscribers.add(message.group_openid)
            await message.reply(content=t("info.epic.subscribed"))
            return True
        if "epicTD" in message.content:
            self.epic_subscribers.discard(message.group_openid)
            await message.reply(content=t("info.epic.unsubscribed"))
            return True
        return False
