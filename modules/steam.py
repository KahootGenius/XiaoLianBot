import asyncio

from datetime import datetime, timedelta

import pytz

from requests import get, RequestException

import re

from bs4 import BeautifulSoup

import time

class SteamModule:

    def __init__(self, api_client):

        self.api = api_client

        self.steam_subscribers = set()

        self.steam_task = None

        self.steam_free_games = []

        self.tz = pytz.timezone('Asia/Shanghai')

        self.headers = {

            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',

            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',

            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'

        }

    async def get_steam_free_games(self, use_proxy=False):

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

                if use_proxy and attempt > 0:                   

                    print(f"[Steam] 尝试使用代理访问")

                response = await asyncio.get_event_loop().run_in_executor(

                    None,

                    lambda: get(url, **request_kwargs)

                )

                response.raise_for_status()

                soup = BeautifulSoup(response.text, 'html.parser')

                self.steam_free_games = []

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

                            self.steam_free_games.append({

                                "title": title,

                                "original_price": original_price,

                                "game_id": game_id,

                                "promo_time": promo_time

                            })

                    except Exception as e:

                        print(f"Error processing game: {e}")

                message = "Steam免费游戏推送\n\n"

                if self.steam_free_games:

                    for game in self.steam_free_games:

                        message += f"游戏：{game['title']}\n"

                        message += f"原价：{game['original_price']}\n"

                        if game['promo_time']:

                            message += f"促销时间：{game['promo_time']}\n"

                        message += f"游戏ID：{game['game_id']}\n"

                        message += f"获取方式：访问Steam商店页面，搜索此游戏ID或在商店页面后添加/app/{game['game_id']}/\n\n"

                else:

                    message += "当前没有限时免费游戏。\n"

                return message

            except RequestException as e:

                last_error = e

                print(f"[Steam] 第{attempt+1}次请求失败: {str(e)}")

                if attempt < max_retries - 1:

                    print(f"[Steam] 等待{retry_delay}秒后重试...")

                    await asyncio.sleep(retry_delay)

                    retry_delay *= 2        

                    continue

                error_msg = f"获取Steam免费游戏信息失败: {str(last_error)}"

                error_msg += "\n\n可能的原因:\n"

                error_msg += "1. 网络连接问题\n"

                error_msg += "2. Steam网站暂时无法访问\n"

                error_msg += "3. 可能需要使用VPN或代理\n"

                error_msg += "\n请稍后再试或使用'/steamfree proxy'命令尝试通过代理访问"

                return error_msg

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

                message = await self.get_steam_free_games(use_proxy=False)

                if "获取Steam免费游戏信息失败" in message:

                    print(f"[Steam] 不使用代理获取失败，尝试使用代理")

                    message = await self.get_steam_free_games(use_proxy=True)

                message = self._sanitize_message(message)

                if not self.steam_subscribers:

                    print(f"[Steam] 没有订阅者，跳过推送")

                    continue

                print(f"[Steam] 开始向{len(self.steam_subscribers)}个群组推送信息")

                for group_id in self.steam_subscribers:

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

        """确保消息不包含敏感URL"""

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

        """将消息分割成多个部分"""

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

            use_proxy = "proxy" in message.content.lower()

            if use_proxy:

                await message.reply(content="正在通过代理获取Steam免费游戏信息，请稍候...")

            else:

                await message.reply(content="正在获取Steam免费游戏信息，请稍候...")

            message_content = await self.get_steam_free_games(use_proxy=use_proxy)

            message_content = self._sanitize_message(message_content)

            try:

                await message.reply(content=message_content)

            except Exception as e:

                print(f"发送Steam免费游戏信息失败: {str(e)}")

                parts = self._split_message(message_content)

                for part in parts:

                    try:

                        await message.reply(content=part)

                        await asyncio.sleep(1)          

                    except Exception as e2:

                        print(f"分段发送失败: {str(e2)}")

                        sanitized_part = part.replace("Steam", "S-t-e-a-m")

                        await message.reply(content=f"消息发送受限，已进一步处理:\n\n{sanitized_part}")

            return True

        if "订阅steam" in message.content:

            self.steam_subscribers.add(message.group_openid)

            await message.reply(content="已订阅Steam免费游戏推送，将在每天下午6点推送信息。")

            return True

        if "steamTD" in message.content:

            self.steam_subscribers.discard(message.group_openid)

            await message.reply(content="已取消订阅Steam免费游戏推送。")

            return True

        return False
