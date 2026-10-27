"""Answering a group message from the modules that were written for botpy's own reply."""


async def reply(message, text, gateway=None, buttons=None):
    """Through the bot's gateway, which keeps reply numbers straight and can put tap-to-send
    buttons (rows of (label, command)) under the reply; without one, botpy's own reply."""
    if gateway is None:
        await message.reply(content=text)
    else:
        await gateway.send(message.group_openid, text, reply_to=message.id, buttons=buttons)
