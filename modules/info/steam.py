import asyncio
import itertools
from datetime import datetime, timedelta
import pytz
from requests import get, RequestException
import re
from bs4 import BeautifulSoup
from core.config import load_config
from core.style import STYLES, t


class SteamModule:
    def __init__(self, api_client):
        self.api = api_client
        self.steam_subscribers = set()
        self.steam_task = None
        self.steam_free_games = []
        self.tz = pytz.timezone('Asia/Shanghai')
        self.proxies = self._load_proxies()
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
        }

    def _load_proxies(self):
        """Read the optional `steam.proxy` URL from config.yaml.

        Returns a requests-style proxies dict, or None when no proxy is
        configured. Absent or malformed config is not fatal -- the module
        simply operates without a proxy.
        """
        try:
            config = load_config()
            proxy = (config.get('steam') or {}).get('proxy')
            return {'http': proxy, 'https': proxy} if proxy else None
        except Exception as e:
            print(f"[Steam] 读取代理配置失败: {e}")
            return None

    async def _fetch(self, use_proxy=False):
        """Scrape Steam's free-promotion search. Returns (games, None), or (None, error text) after 5 tries."""
        max_retries = 5
        retry_delay = 2
        last_error = None
        for attempt in range(max_retries):
            try:
                domain = "store.steampowered.com"
                path = "/search/?maxprice=free&specials=1"
                url = "https://" + domain + path
                print(f"[Steam] 尝试第{attempt+1}次请求URL: {url}")
                request_kwargs = {
                    'headers': self.headers,
                    'timeout': 15
                }
                if use_proxy:
                    request_kwargs['proxies'] = self.proxies
                    print(f"[Steam] 尝试使用代理访问")
                response = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: get(url, **request_kwargs)
                )
                response.raise_for_status()
                soup = BeautifulSoup(response.text, 'html.parser')
                games = []
                game_elements = soup.select('#search_resultsRows > a')
                for game in game_elements:
                    try:
                        title = game.select_one('.title').text.strip()
                        discount_pct = game.select_one('.search_discount span')
                        if discount_pct and discount_pct.text.strip() == '-100%':
                            original_price_elem = game.select_one('.search_price span strike')
                            original_price = original_price_elem.text.strip() if original_price_elem else "未知"
                            app_id_match = re.search(r'/app/(\d+)/', game.get('href', ''))
                            game_id = app_id_match.group(1) if app_id_match else "未知"
                            promo_elem = game.select_one('.discount_ends')
                            promo_time = promo_elem.text.strip() if promo_elem else ""
                            games.append({
                                "title": title,
                                "original_price": original_price,
                                "game_id": game_id,
                                "promo_time": promo_time
                            })
                    except Exception as e:
                        print(f"Error processing game: {e}")
                return games, None
            except RequestException as e:
                last_error = e
                print(f"[Steam] 第{attempt+1}次请求失败: {str(e)}")
                if attempt < max_retries - 1:
                    print(f"[Steam] 等待{retry_delay}秒后重试...")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
        return None, str(last_error)

    def _format(self, games):
        """The scraped games as a message, in the current style."""
        message = t("info.steam.title") + "\n\n"
        if games:
            for game in games:
                period = t("info.steam.period", period=game["promo_time"]) if game["promo_time"] else ""
                message += t("info.steam.game", title=game["title"], price=game["original_price"], period=period,
                             id=game["game_id"]) + "\n\n"
        else:
            message += t("info.steam.none") + "\n"
        return message

    async def get_steam_free_games(self, use_proxy=False):
        """(message, ok). The flag, not the wording, says whether it worked -- styles may reword failures."""
        if use_proxy and not self.proxies:
            return t("info.steam.no_proxy"), False
        games, error = await self._fetch(use_proxy)
        if games is None:
            return t("info.steam.failed", error=error), False
        self.steam_free_games = games
        return self._format(games), True

    async def _get_promotion_time(self, url):
        try:
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: get(url, headers=self.headers, timeout=10)
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            promo_elem = soup.select_one('.game_purchase_discount_countdown')
            if promo_elem:
                return promo_elem.text.strip()
            return ""
        except Exception:
            return ""

    async def schedule_steam_push(self):
        while True:
            try:
                now = datetime.now(self.tz)
                next_run = now.replace(hour=18, minute=0, second=0, microsecond=0)
                if now >= next_run:
                    next_run += timedelta(days=1)
                wait_seconds = (next_run - now).seconds
                print(f"[Steam] 下一次推送将在{wait_seconds}秒后进行（{next_run.strftime('%Y-%m-%d %H:%M:%S')}）")
                await asyncio.sleep(wait_seconds)
                print(f"[Steam] 开始获取免费游戏信息用于定时推送")
                games, error = await self._fetch(use_proxy=False)
                if games is None and self.proxies:
                    print(f"[Steam] 不使用代理获取失败，尝试使用代理")
                    games, error = await self._fetch(use_proxy=True)
                if not self.steam_subscribers:
                    print(f"[Steam] 没有订阅者，跳过推送")
                    continue
                print(f"[Steam] 开始向{len(self.steam_subscribers)}个群组推送信息")
                for group_id in self.steam_subscribers:
                    # Fetched once, worded in each group's own style.
                    with STYLES.scope(group_id):
                        message = t("info.steam.failed", error=error) if games is None else self._format(games)
                    message = self._sanitize_message(message)
                    try:
                        await self.api.post_group_message(
                            group_openid=group_id,
                            msg_type=1,
                            content=message
                        )
                        print(f"[Steam] 成功向群组{group_id}推送信息")
                        await asyncio.sleep(1)
                    except Exception as e:
                        print(f"[Steam] 推送到群组{group_id}失败: {str(e)}")
                        try:
                            parts = self._split_message(message)
                            for part in parts:
                                await self.api.post_group_message(
                                    group_openid=group_id,
                                    msg_type=1,
                                    content=part
                                )
                                await asyncio.sleep(1)
                            print(f"[Steam] 成功向群组{group_id}分段推送信息")
                        except Exception as e2:
                            print(f"[Steam] 分段推送到群组{group_id}也失败: {str(e2)}")
            except Exception as e:
                print(f"[Steam] 定时推送任务发生错误: {str(e)}")
                await asyncio.sleep(600)

    def _sanitize_message(self, message):
        """Break up Steam URLs/domains so QQ's link filter doesn't reject the message."""
        sanitized = message
        sensitive_domains = ["store.steampowered.com", "steampowered.com", "steam"]
        for domain in sensitive_domains:
            parts = domain.split('.')
            if len(parts) > 1:
                invisible_char = "\u200B"
                modified_domain = invisible_char.join(parts)
                sanitized = sanitized.replace(domain, modified_domain)
            sanitized = sanitized.replace(f"http://{domain}", f"h-t-t-p://{domain}")
            sanitized = sanitized.replace(f"https://{domain}", f"h-t-t-p-s://{domain}")
        return sanitized

    def _split_message(self, message, max_length=800):
        """Split an over-long message into chunks that fit QQ's length limit."""
        if len(message) <= max_length:
            return [message]
        parts = []
        lines = message.split('\n\n')
        current_part = lines[0]
        for i in range(1, len(lines)):
            if len(current_part) + len(lines[i]) + 2 <= max_length:
                current_part += '\n\n' + lines[i]
            else:
                parts.append(current_part)
                current_part = lines[i]
        if current_part:
            parts.append(current_part)
        return parts

    async def handle_steam(self, message):
        if "/steamfree" in message.content:
            # QQ rejects a second reply to the same msg_id unless msg_seq
            # differs (error 40054005 "消息被去重"). This handler replies
            # several times -- a "please wait" first, then the result, then
            # possibly split chunks -- so every reply needs a fresh sequence.
            seq = itertools.count(1)
            use_proxy = "proxy" in message.content.lower()
            waiting = "info.steam.fetching_proxy" if use_proxy else "info.steam.fetching"
            await message.reply(content=t(waiting), msg_seq=next(seq))
            message_content, _ = await self.get_steam_free_games(use_proxy=use_proxy)
            message_content = self._sanitize_message(message_content)
            try:
                await message.reply(content=message_content, msg_seq=next(seq))
            except Exception as e:
                print(f"发送Steam免费游戏信息失败: {str(e)}")
                parts = self._split_message(message_content)
                for part in parts:
                    try:
                        await message.reply(content=part, msg_seq=next(seq))
                        await asyncio.sleep(1)
                    except Exception as e2:
                        print(f"分段发送失败: {str(e2)}")
                        sanitized_part = part.replace("Steam", "S-t-e-a-m")
                        try:
                            await message.reply(content=t("info.steam.limited", text=sanitized_part), msg_seq=next(seq))
                        except Exception as e3:
                            # Last resort failed too; log it rather than let the
                            # exception escape and kill the event handler.
                            print(f"最终发送失败，已放弃该分段: {str(e3)}")
            return True
        if "订阅steam" in message.content:
            self.steam_subscribers.add(message.group_openid)
            await message.reply(content=t("info.steam.subscribed"))
            return True
        if "steamTD" in message.content:
            self.steam_subscribers.discard(message.group_openid)
            await message.reply(content=t("info.steam.unsubscribed"))
            return True
        return False
