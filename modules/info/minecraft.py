import asyncio
import socket
from mcstatus import JavaServer
from core.style import t


class MinecraftModule:
    def __init__(self, api_client):
        self.api = api_client
        self.mc_servers = {}

    async def handle_minecraft(self, message):
        if "/mc绑定" in message.content:
            try:
                server_address = message.content.split()[1]
                self.mc_servers[message.group_openid] = server_address
                await message.reply(content=t("info.minecraft.bound"))
                return True
            except IndexError:
                await message.reply(content=t("info.minecraft.bind_usage"))
                return True
        elif "/mc解绑" in message.content:
            if message.group_openid in self.mc_servers:
                del self.mc_servers[message.group_openid]
                await message.reply(content=t("info.minecraft.unbound"))
            else:
                await message.reply(content=t("info.minecraft.not_bound"))
            return True
        elif "/mc状态" in message.content:
            if message.group_openid not in self.mc_servers:
                await message.reply(content=t("info.minecraft.bind_first"))
                return True
            try:
                server = JavaServer.lookup(self.mc_servers[message.group_openid])
                status = await asyncio.get_event_loop().run_in_executor(None, server.status)
                if status.players.sample:
                    players = "\n".join([p.name for p in status.players.sample])
                else:
                    players = t("info.minecraft.no_players")
                await message.reply(content=t("info.minecraft.status", latency=f"{status.latency:.1f}",
                                              online=status.players.online, max=status.players.max,
                                              players=players))
                return True
            except (socket.timeout, ConnectionRefusedError, ConnectionError):
                await message.reply(content=t("info.minecraft.unreachable"))
                return True
            except Exception as e:
                await message.reply(content=t("info.minecraft.failed", error=e))
                return True
        return False
