import asyncio
from core.style import t


class DeepSeekModule:
    def __init__(self, api_client):
        self.api = api_client

    async def handle_deepseek(self, message):
        if "/deepseek" in message.content:
            await asyncio.sleep(5)
            pic_url = 'https://upload-bbs.miyoushe.com/upload/2025/02/18/296483262/f33b5aa297121aec283b348292269b0e_7812237951825207540.png?x-oss-process=image//resize,s_600/quality,q_80/auto-orient,0/interlace,1/format,png'
            try:
                upload_media = await self.api.post_group_file(
                    group_openid=message.group_openid,
                    file_type=1,
                    url=pic_url
                )
                await message.reply(
                    msg_type=7,
                    media=upload_media,
                    msg_seq=2
                )
                return True
            except Exception as e:
                await message.reply(content=t("ai.image_error", error=e))
                return True
        return False
