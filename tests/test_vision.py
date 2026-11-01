"""Fetching pictures from QQ, and describing what QQ sends without repeating any of it."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock
import aiohttp
from modules.ai import vision

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 32
GIF = b"GIF89a" + b"\0" * 32
WEBP = b"RIFF\x24\0\0\0WEBPVP8 " + b"\0" * 20
PHOTO = "https://multimedia.nt.qq.com.cn/download?appid=1&fileid=F&rkey=SECRET"


class Page:
    """What the fake web answers for a link: a body, or an exception to raise."""

    def __init__(self, body=PNG, status=200, location=None, error=None):
        self.body, self.status, self.error = body, status, error
        self.headers = {"Location": location} if location else {}
        self.content = SimpleNamespace(iter_chunked=self.chunks)
        self.chunks_read = 0

    async def chunks(self, size):
        for start in range(0, len(self.body), size):
            self.chunks_read += 1
            yield self.body[start:start + size]

    async def __aenter__(self):
        if self.error:
            raise self.error
        return self

    async def __aexit__(self, *exc):
        return False


class FakeWeb:
    """Stands in for aiohttp.ClientSession: answers from `pages`, and remembers what was asked for."""

    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def __call__(self, timeout=None):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, allow_redirects=True):
        assert allow_redirects is False         # each hop has to pass qq_link
        self.asked.append(url)
        return self.pages[url]


class QQLinkTest(unittest.TestCase):
    def test_links_on_qq_servers_come_back_as_https(self):
        for link, wanted in ((PHOTO, PHOTO),
                             ("http://gchat.qpic.cn/a/b.jpg", "https://gchat.qpic.cn/a/b.jpg"),
                             ("//multimedia.nt.qq.com.cn/a", "https://multimedia.nt.qq.com.cn/a"),
                             ("gchat.qpic.cn/a?x=1", "https://gchat.qpic.cn/a?x=1"),
                             ("  HTTPS://QQ.COM/a  ", "https://QQ.COM/a")):
            self.assertEqual(vision.qq_link(link), wanted)

    def test_anything_else_is_refused(self):
        for link in ("https://example.com/a.png", "https://notqq.com/a", "https://qq.com.example.com/a",
                     "https://example.com/qq.com/a", "https://example.com?x=.qq.com", "https://example.com#.qq.com",
                     "https://qq.com@example.com/a", "https://example.com\\@qq.com/a", "ftp://qq.com/a",
                     "https://[qq.com/a", "", None):
            self.assertIsNone(vision.qq_link(link), link)


class ImageFormatTest(unittest.TestCase):
    def test_it_goes_by_the_file_itself(self):
        for data, wanted in ((PNG, "image/png"), (JPEG, "image/jpeg"), (GIF, "image/gif"), (WEBP, "image/webp"),
                             (b"<html>expired</html>", None), (b"RIFF\0\0\0\0WAVEfmt ", None), (b"", None)):
            self.assertEqual(vision.image_format(data), wanted)


class FetchTest(unittest.IsolatedAsyncioTestCase):
    def web(self, pages):
        web = FakeWeb(pages)
        patcher = mock.patch("modules.ai.vision.aiohttp.ClientSession", web)
        patcher.start()
        self.addCleanup(patcher.stop)
        return web

    async def refused(self, url=PHOTO):
        with self.assertRaises(vision.PictureError) as caught:
            await vision.fetch(url)
        return caught.exception

    async def test_a_picture_comes_back_with_its_real_format(self):
        self.web({PHOTO: Page(JPEG)})
        self.assertEqual(await vision.fetch(PHOTO), (JPEG, "image/jpeg"))

    async def test_a_redirect_is_followed_while_it_stays_on_qq(self):
        web = self.web({PHOTO: Page(status=302, location="/moved?rkey=SECRET"),
                        "https://multimedia.nt.qq.com.cn/moved?rkey=SECRET": Page(PNG)})
        self.assertEqual((await vision.fetch(PHOTO))[1], "image/png")
        self.assertEqual(len(web.asked), 2)

    async def test_a_redirect_off_qq_is_never_followed_and_the_log_says_where(self):
        web = self.web({PHOTO: Page(status=302, location="https://example.com/a.png?token=SECRET")})
        with self.assertLogs(vision._log, level="INFO") as logs:
            self.assertEqual((await self.refused()).reason, "not_qq")
        self.assertEqual(web.asked, [PHOTO])
        self.assertIn("example.com", logs.output[0])
        self.assertNotIn("SECRET", logs.output[0])

    async def test_a_link_off_qq_is_never_opened(self):
        web = self.web({})
        self.assertEqual((await self.refused("https://example.com/a.png")).reason, "not_qq")
        self.assertEqual(web.asked, [])

    async def test_redirects_dont_go_on_forever(self):
        web = self.web({PHOTO: Page(status=301, location=PHOTO)})
        self.assertEqual((await self.refused()).reason, "redirects")
        self.assertEqual(len(web.asked), vision.MAX_REDIRECTS + 1)

    async def test_an_expired_link_says_what_the_server_said(self):
        self.web({PHOTO: Page(b"gone", status=403)})
        failure = await self.refused()
        self.assertEqual((failure.reason, failure.detail), ("http", 403))

    async def test_a_huge_file_stops_being_read(self):
        page = Page(PNG + b"\0" * (vision.MAX_BYTES * 2))
        self.web({PHOTO: page})
        failure = await self.refused()
        self.assertEqual((failure.reason, failure.detail), ("too_big", 10))
        self.assertLess(page.chunks_read, 170)              # 10 MB in 64 KB pieces, and a little more

    async def test_a_page_that_isnt_a_picture_is_refused(self):
        self.web({PHOTO: Page(b"<html>this link has expired</html>")})
        self.assertEqual((await self.refused()).reason, "not_image")

    async def test_failures_never_hold_the_link(self):
        self.web({PHOTO: Page(error=asyncio.TimeoutError())})
        self.assertEqual((await self.refused()).reason, "timeout")
        self.web({PHOTO: Page(error=aiohttp.ClientPayloadError(f"broken reading {PHOTO}"))})
        failure = await self.refused()
        self.assertEqual((failure.reason, failure.detail), ("network", "ClientPayloadError"))
        self.assertNotIn("SECRET", str(failure))


class ShapeTest(unittest.TestCase):
    PAYLOAD = {
        "id": "ROBOT1.0_abc", "content": "看看这张图", "message_type": 103,
        "author": {"member_openid": "MEMBER", "username": "小明"},
        "message_scene": {"source": "default", "ext": ["msg_idx=REFIDX_a", "auth_token=TOKEN", "ref_msg_idx=TMP_b"]},
        "msg_elements": [{"content": "朋友发的私事", "message_type": 0, "ark_data": {"ark_type": "picture", "prompt": "私事"},
                          "attachments": [{"content_type": "image/jpeg", "url": PHOTO, "width": 1080, "height": 1920,
                                           "size": 153022}]}],
    }

    def test_it_tells_the_structure_and_nothing_anyone_said(self):
        line = vision.shape(self.PAYLOAD)
        self.assertEqual(line, "type=103 text=5 scene=auth_token,msg_idx,ref_msg_idx elements[type=0 text=6 "
                               "image/jpeg 1080x1920 153022B https:multimedia.nt.qq.com.cn ark=picture]")
        for secret in ("看看", "私事", "小明", "MEMBER", "TOKEN", "REFIDX", "TMP_", "SECRET", "fileid"):
            self.assertNotIn(secret, line)

    def test_a_reply_leaves_web_addresses_out_as_qq_refuses_them(self):
        line = vision.shape(self.PAYLOAD, hosts=False)
        self.assertIn("image/jpeg 1080x1920 153022B", line)
        self.assertNotIn("qq.com", line)

    def test_it_says_when_a_link_comes_without_a_scheme(self):
        line = vision.shape({"attachments": [{"content_type": "image/png", "url": "gchat.qpic.cn/a/b?c=1"}]})
        self.assertIn("no-scheme:gchat.qpic.cn", line)

    def test_only_messages_carrying_something_are_logged(self):
        with self.assertLogs(vision._log, level="INFO") as logs:
            vision.note(SimpleNamespace(raw=self.PAYLOAD))
        self.assertIn("[Picture] type=103", logs.output[0])
        self.assertNotIn("SECRET", logs.output[0])
        with self.assertNoLogs(vision._log, level="INFO"):
            vision.note(SimpleNamespace(raw={"content": "你好", "message_type": 0}))


if __name__ == "__main__":
    unittest.main()
