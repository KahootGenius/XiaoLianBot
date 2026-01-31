import asyncio

import socket

from mcstatus import JavaServer

class MinecraftModule:

    def __init__(self, api_client):

        self.api = api_client

        self.mc_servers = {}

    async def handle_minecraft(self, message):

        if "/mc绑定" in message.content:

            try:

                server_address = message.content.split()[1]                  

                self.mc_servers[message.group_openid] = server_address

                await message.reply(content=f"已绑定Minecraft服务器")

                return True

            except IndexError:

                await message.reply(content="请提供服务器地址，格式：/mc绑定 域名或IP:端口")

                return True

        elif "/mc解绑" in message.content:

            if message.group_openid in self.mc_servers:

                del self.mc_servers[message.group_openid]

                await message.reply(content="已解除 Minecraft 服务器绑定")

            else:

                await message.reply(content="当前群组未绑定任何服务器")

            return True

        elif "/mc状态" in message.content:

            if message.group_openid not in self.mc_servers:

                await message.reply(content="请先使用 /mc绑定 命令绑定服务器地址")

                return True

            try:

                server = JavaServer.lookup(self.mc_servers[message.group_openid])

                status = await asyncio.get_event_loop().run_in_executor(None, server.status)

                players = "无" if not status.players.sample else "\n".join([p.name for p in status.players.sample])

                response = (

                    f"服务器状态：在线\n"

                    f"延迟：{status.latency:.1f}ms\n"

                    f"在线人数：{status.players.online}/{status.players.max}\n"

                    f"在线玩家：\n{players}"

                )

                await message.reply(content=response)

                return True

            except (socket.timeout, ConnectionRefusedError, ConnectionError):

                await message.reply(content="无法连接到服务器，请检查服务器地址是否正确或服务器是否在线")

                return True

            except Exception as e:

                await message.reply(content=f"查询失败：{str(e)}")

                return True

        return False
