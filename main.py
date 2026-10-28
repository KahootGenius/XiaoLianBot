import asyncio
from collections import OrderedDict
import botpy
from botpy import logging
from core.config import load_config
from core.style import STYLES
from modules.help import HelpModule
from modules.ai import ai_chat
from modules.ai.ai_chat import AIChatModule
from modules.ai.deepseek import DeepSeekModule
from modules.fun.blackjack import BlackjackModule
from modules.fun.crazy_talk import CrazyTalkModule
from modules.fun.dice import DiceModule
from modules.fun import social
from modules.fun.social import SocialModule
from modules.fun.sleep import SleepModule
from modules.fun.teams import TeamsModule
from modules.info.epic_games import EpicGamesModule
from modules.info.minecraft import MinecraftModule
from modules.info.steam import SteamModule
from modules.manager import Dashboard, ManagerModule, QQGateway, events


config = load_config()
_log = logging.get_logger()

# Command words of the Castorice modules, which match anywhere in a message.
# Team-game actions (prompts/teams.yaml) may not take them.
CASTORICE_COMMANDS = ("/epicfree", "/订阅epic", "/epicTD", "/steamfree", "/订阅steam", "/steamTD",
                      "/mc绑定", "/mc解绑", "/mc状态", "/21点", "/抽卡", "/停止", "/晚安", "/早安",
                      "/deepseek", "/发癫", "/摇骰子")

# Which style file speaks (styles/<name>.yaml): config.yaml's `style` for every group,
# `group_styles` for particular groups. Set only on this machine, never from chat.
STYLES.configure(config)

# botpy 1.2.1 doesn't know GROUP_MESSAGE_CREATE (every group message, not just
# @-messages). Teach it before the client starts.
events.install()


class MyClient(botpy.Client):
    def __init__(self, intents):
        super().__init__(intents=intents)
        # One gateway for every module that replies through it, so reply sequence numbers never collide.
        self.gateway = QQGateway(self.api, buttons=config.get("buttons", True))
        self.epic_module = EpicGamesModule(self.api)
        self.minecraft_module = MinecraftModule(self.api, gateway=self.gateway)
        self.steam_module = SteamModule(self.api)
        self.blackjack_module = BlackjackModule(self.api, gateway=self.gateway)
        self.sleep_module = SleepModule(self.api)
        self.dice_module = DiceModule(self.api, gateway=self.gateway)
        self.deepseek_module = DeepSeekModule(self.api)
        self.crazy_talk_module = CrazyTalkModule(self.api, gateway=self.gateway)
        self.manager_module = ManagerModule(self.gateway, config, styles=STYLES)
        self.ai_chat_module = AIChatModule(self.gateway, config, is_admin=self.manager_module.is_admin,
                                           nickname_of=self.manager_module.known_nickname)
        if self.ai_chat_module.enabled:
            self.manager_module.digest_commenter = self.ai_chat_module.comment_on_digest
        self.social_module = SocialModule(self.gateway, is_admin=self.manager_module.is_admin,
                                          activity=self.manager_module.active_members)
        reserved = (HelpModule.TRIGGERS + CASTORICE_COMMANDS + ai_chat.COMMANDS + social.COMMANDS
                    + tuple(name for name, *_ in self.manager_module.commands))
        self.teams_module = TeamsModule(self.gateway, config, is_admin=self.manager_module.is_admin,
                                        nickname_of=self.manager_module.known_nickname, reserved=reserved)
        # AI chat knows today's teams, birthdays and poll, so she can play along.
        self.ai_chat_module.teams_today = self.teams_module.today_for_ai
        self.ai_chat_module.social_today = self.social_module.today_for_ai
        # /删除数据 @某人 also clears their team place and what AI chat kept about them.
        self.manager_module.forget_hooks += [self.teams_module.forget_member, self.ai_chat_module.forget_member,
                                             self.social_module.forget_member]
        dashboard_config = (config.get("manager") or {}).get("dashboard") or {}
        self.dashboard = None
        if self.manager_module.enabled and dashboard_config.get("enabled", True):
            self.dashboard = Dashboard.from_config(self.manager_module, dashboard_config)
        self.help_module = HelpModule(self.api, ai_enabled=self.ai_chat_module.enabled,
                                      manager_enabled=self.manager_module.enabled, teams=self.teams_module,
                                      gateway=self.gateway)
        self._answered = OrderedDict()
        self._started = False

    async def on_ready(self):
        _log.info(f"robot 「{self.robot.name}」 on_ready!")
        _log.info(f"[Style] 默认风格：{STYLES.default_name}；可用：{'、'.join(STYLES.names())}")
        for group, name in STYLES.groups.items():
            _log.info(f"[Style] 群 {group[:8]}… 使用风格：{name}")
        # Lets RichGroupMessage recognise the bot's own @mention inside
        # every-message events.
        events.RichGroupMessage.self_ids.update({str(self.robot.id), str(config.get("appid"))})
        # ...and "@小蓮女神" typed as plain text, which QQ doesn't send as a mention.
        if self.robot.name:
            events.RichGroupMessage.self_names.add(str(self.robot.name))
        # READY can arrive again after a reconnect; background jobs must only start once.
        if self._started:
            return
        self._started = True
        self.epic_module.epic_task = asyncio.create_task(self.epic_module.schedule_epic_push())
        self.steam_module.steam_task = asyncio.create_task(self.steam_module.schedule_steam_push())
        self.manager_module.task = asyncio.create_task(self.manager_module.run_scheduler())
        self.teams_module.task = asyncio.create_task(self.teams_module.run_scheduler())
        self.ai_chat_module.task = asyncio.create_task(self.ai_chat_module.run_scheduler())
        if self.dashboard is not None:
            await self.dashboard.start()

    async def on_group_message_create(self, message):
        """Every group message. Only delivered once the group owner lets the bot receive all messages."""
        if message.is_bot:
            return
        # Everything said while handling this message speaks in the group's style.
        with STYLES.scope(message.group_openid):
            await self.manager_module.observe(message)
            await self.social_module.observe(message)
            self.ai_chat_module.noticed(message)        # it isn't quiet: only the time is kept
            if message.mentions_bot:
                await self._handle_command(message)
            else:
                self.ai_chat_module.hear(message)       # only kept with ai_chat.hear_all

    async def on_group_at_message_create(self, message):
        with STYLES.scope(message.group_openid):
            await self.manager_module.observe(message)
            await self.social_module.observe(message)
            self.ai_chat_module.noticed(message)
            await self._handle_command(message)

    async def on_group_del_robot(self, event):
        # The privacy policy promises a group's data is deleted when the bot leaves it.
        self.manager_module.forget_group(event.group_openid)
        self.teams_module.forget_group(event.group_openid)
        self.ai_chat_module.forget_group(event.group_openid)
        self.social_module.forget_group(event.group_openid)

    def _first_time(self, message_id):
        """With every-message access an @-message arrives as both events; answer it once."""
        if message_id in self._answered:
            return False
        self._answered[message_id] = True
        while len(self._answered) > 1000:
            self._answered.popitem(last=False)
        return True

    async def _handle_command(self, message):
        if not self._first_time(message.id):
            return
        if await self.help_module.handle_help(message):
            return
        if await self.manager_module.handle_manager(message):
            return
        if await self.teams_module.handle_teams(message):
            return
        if await self.social_module.handle_social(message):
            return
        if await self.epic_module.handle_epic(message):
            return
        if await self.minecraft_module.handle_minecraft(message):
            return
        if await self.steam_module.handle_steam(message):
            return
        if await self.blackjack_module.handle_blackjack(message):
            return
        if await self.sleep_module.handle_sleep(message):
            return
        if await self.deepseek_module.handle_deepseek(message):
            return
        if await self.crazy_talk_module.handle_crazy_talk(message):
            return
        if await self.dice_module.handle_dice(message):
            return
        # AI chat is the catch-all fallback: it replies to anything, so every
        # specific command handler must be checked before this point.
        if await self.ai_chat_module.handle_ai_chat(message):
            return
        _log.info(f"User member_openid: {message.author.member_openid}")


if __name__ == "__main__":
    if not config.get("appid") or not config.get("secret"):
        raise SystemExit("请先把 config.yaml.example 复制为 config.yaml，并填写 appid 和 secret。")
    intents = botpy.Intents(public_messages=True, public_guild_messages=True)
    client = MyClient(intents=intents)
    client.run(appid=config["appid"], secret=config["secret"])
