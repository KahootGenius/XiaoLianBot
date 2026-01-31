import random

class BlackjackModule:

    def __init__(self, api_client):

        self.api = api_client

        self.game_status = {}

        self.player_cards = {}

        self.bot_cards = {}

    def calculate_points(self, cards):

        points = 0

        ace_count = 0

        for card in cards:

            if card == 'A':

                ace_count += 1

            elif card in ['J', 'Q', 'K']:

                points += 10

            else:

                points += int(card)

        for _ in range(ace_count):

            if points + 11 <= 21:

                points += 11

            else:

                points += 1

        return points

    async def handle_blackjack(self, message):

        if "/21点" in message.content:

            return await self._start_game(message)

        elif "/抽卡" in message.content and self.game_status.get(message.group_openid):

            return await self._draw_card(message)

        elif "/停止" in message.content and self.game_status.get(message.group_openid):

            return await self._stop_game(message)

        return False

    async def _start_game(self, message):

        self.game_status[message.group_openid] = True

        self.player_cards[message.group_openid] = []

        self.bot_cards[message.group_openid] = []

        cards = ['A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K']

        self.player_cards[message.group_openid].append(random.choice(cards))

        self.bot_cards[message.group_openid].append(random.choice(cards))

        player_points = self.calculate_points(self.player_cards[message.group_openid])

        await message.reply(content=f"游戏开始！\n你的手牌: {', '.join(self.player_cards[message.group_openid])} (点数: {player_points})\n机器人的明牌: {self.bot_cards[message.group_openid][0]}\n你可以输入 /抽卡 继续要牌，或输入 /停止 停止要牌")

        return True

    async def _draw_card(self, message):

        cards = ['A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K']

        self.player_cards[message.group_openid].append(random.choice(cards))

        player_points = self.calculate_points(self.player_cards[message.group_openid])

        if player_points > 21:

            self.game_status[message.group_openid] = False

            await message.reply(content=f"你的手牌: {', '.join(self.player_cards[message.group_openid])} (点数: {player_points})\n爆牌了！你输了！")

        else:

            await message.reply(content=f"你的手牌: {', '.join(self.player_cards[message.group_openid])} (点数: {player_points})\n机器人的明牌: {self.bot_cards[message.group_openid][0]}")

        return True

    async def _stop_game(self, message):

        cards = ['A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K']

        while self.calculate_points(self.bot_cards[message.group_openid]) < 17:

            self.bot_cards[message.group_openid].append(random.choice(cards))

        player_points = self.calculate_points(self.player_cards[message.group_openid])

        bot_points = self.calculate_points(self.bot_cards[message.group_openid])

        result = f"游戏结束！\n你的手牌: {', '.join(self.player_cards[message.group_openid])} (点数: {player_points})\n"

        result += f"机器人的手牌: {', '.join(self.bot_cards[message.group_openid])} (点数: {bot_points})\n"

        if bot_points > 21:

            result += "机器人爆牌了！你赢了！"

        elif player_points > bot_points:

            result += "恭喜你赢了！"

        elif player_points < bot_points:

            result += "很遗憾，你输了！"

        else:

            result += "平局！"

        await message.reply(content=result)

        self.game_status[message.group_openid] = False

        return True
