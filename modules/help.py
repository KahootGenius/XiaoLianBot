from core.style import t


class HelpModule:
    """Lists the bot's commands in-chat.

    The QQ platform's "/" command panel only shows commands registered in
    发布设置 -> 指令配置, and only to users who think to type "/". This gives
    the bot a way to answer "what can you do?" on its own.
    """

    TRIGGERS = ("/帮助", "/菜单", "/help", "/指令")

    def __init__(self, api_client, ai_enabled=True, manager_enabled=False, teams=None, gateway=None):
        self.api = api_client
        # Optional features are only advertised when actually usable.
        self.ai_enabled = ai_enabled
        self.manager_enabled = manager_enabled
        self.teams = teams
        self.gw = gateway       # replies through it can carry buttons

    def buttons(self):
        """Quick-tap buttons under /帮助, for the features this bot has switched on."""
        first = [("我的状态", "/我的状态"), ("排行", "/排行")] if self.manager_enabled else []
        second = []
        if self.teams is not None and self.teams.enabled:
            word = self.teams.rules.commands["teams"]
            second.append((word, f"/{word}"))
        second += [("投票", "/投票"), ("翻牌子", "/翻牌子")]
        return [first + [("今日运势", "/今日运势")], second]

    def _build_help_text(self):
        sections = [t("help.title")]
        if self.manager_enabled:
            sections.append(t("manager.help_block"))
        sections += [t("help.games"), t("help.minecraft"), t("help.fun")]
        if self.teams is not None and self.teams.enabled:
            sections.append(self.teams.help_block())
        sections += [t("help.social"), t("help.sleep")]
        if self.ai_enabled:
            sections.append(t("help.ai"))
        footer = t("help.footer")
        if self.manager_enabled:
            footer += "\n" + t("help.admin")
        sections.append(footer)
        return "\n\n".join(sections)

    async def handle_help(self, message):
        content = message.content
        # "/管理帮助" contains "帮助" but not "/帮助", so it falls through to the manager.
        if any(k in content for k in self.TRIGGERS):
            if self.gw is None:
                await message.reply(content=self._build_help_text())
            else:
                await self.gw.send(message.group_openid, self._build_help_text(), reply_to=message.id,
                                   buttons=self.buttons())
            return True
        return False
