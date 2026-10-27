import random
from core.reply import reply
from core.style import t


# Under each hand: draw or stand. Once the game is over: another one.
PLAYING = [[("抽卡", "/抽卡"), ("停止", "/停止")]]


class BlackjackModule:
    def __init__(self, api_client, gateway=None):
        self.api = api_client
        self.gw = gateway       # replies through it can carry buttons
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
        # /抽卡 and /停止 only make sense mid-game, but they must still answer
        # when used on their own -- otherwise they fall through to AI chat and
        # the user (or a platform reviewer) gets an unrelated reply.
        elif "/抽卡" in message.content:
            if not self.game_status.get(message.group_openid):
                await reply(message, t("games.blackjack.no_game"), self.gw, self._again())
                return True
            return await self._draw_card(message)
        elif "/停止" in message.content:
            if not self.game_status.get(message.group_openid):
                await reply(message, t("games.blackjack.no_game"), self.gw, self._again())
                return True
            return await self._stop_game(message)
        return False

    @staticmethod
    def _again():
        return [[(t("games.blackjack.again"), "/21点")]]

    async def _start_game(self, message):
        self.game_status[message.group_openid] = True
        self.player_cards[message.group_openid] = []
        self.bot_cards[message.group_openid] = []
        cards = ['A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K']
        self.player_cards[message.group_openid].append(random.choice(cards))
        self.bot_cards[message.group_openid].append(random.choice(cards))
        hand = self.player_cards[message.group_openid]
        await reply(message, t("games.blackjack.start", hand=', '.join(hand), points=self.calculate_points(hand),
                               dealer=self.bot_cards[message.group_openid][0]), self.gw, PLAYING)
        return True

    async def _draw_card(self, message):
        cards = ['A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K']
        self.player_cards[message.group_openid].append(random.choice(cards))
        hand = self.player_cards[message.group_openid]
        player_points = self.calculate_points(hand)
        if player_points > 21:
            self.game_status[message.group_openid] = False
            await reply(message, t("games.blackjack.bust", hand=', '.join(hand), points=player_points),
                        self.gw, self._again())
        else:
            await reply(message, t("games.blackjack.hand", hand=', '.join(hand), points=player_points,
                                   dealer=self.bot_cards[message.group_openid][0]), self.gw, PLAYING)
        return True

    async def _stop_game(self, message):
        cards = ['A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K']
        while self.calculate_points(self.bot_cards[message.group_openid]) < 17:
            self.bot_cards[message.group_openid].append(random.choice(cards))
        hand = self.player_cards[message.group_openid]
        dealer_hand = self.bot_cards[message.group_openid]
        player_points = self.calculate_points(hand)
        bot_points = self.calculate_points(dealer_hand)
        if bot_points > 21:
            outcome = t("games.blackjack.dealer_bust")
        elif player_points > bot_points:
            outcome = t("games.blackjack.win")
        elif player_points < bot_points:
            outcome = t("games.blackjack.lose")
        else:
            outcome = t("games.blackjack.draw")
        self.game_status[message.group_openid] = False
        await reply(message, t("games.blackjack.result", hand=', '.join(hand), points=player_points,
                               dealer_hand=', '.join(dealer_hand), dealer_points=bot_points, outcome=outcome),
                    self.gw, self._again())
        return True
