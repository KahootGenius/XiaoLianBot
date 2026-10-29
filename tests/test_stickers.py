"""Stickers: the private folder, [表情:标签] in replies, QQ's image library and uploading to QQ."""
import base64
import io
import json
import os
import shutil
import struct
import tempfile
import unittest
import zipfile
from types import SimpleNamespace
from modules.ai import stickers
from modules.manager import openapi
from modules.manager.openapi import OpenAPIError, QQGateway
from tests.test_ai_chat import G, AIChatTestCase
from tests.test_manager import FakeGateway

PNG = b"\x89PNG\r\n\x1a\nfake image"


def folder(manifest, files=("happy.png", "shock.jpg")):
    path = tempfile.mkdtemp()
    for name in files:
        with open(os.path.join(path, name), "wb") as f:
            f.write(PNG)
    with open(os.path.join(path, stickers.MANIFEST), "w", encoding="utf-8") as f:
        f.write(manifest)
    return path


GOOD = """
- file: happy.png
  label: 开心
  desc: 开心、得意的时候
- file: shock.jpg
  label: 惊讶
  desc: 被吓到的时候
"""


class FolderTest(unittest.TestCase):
    def make(self, manifest, files=("happy.png", "shock.jpg")):
        path = folder(manifest, files)
        self.addCleanup(shutil.rmtree, path)
        return path

    def test_a_good_folder(self):
        path = self.make(GOOD)
        found, problems = stickers.load(path)
        self.assertEqual(problems, [])
        self.assertEqual(list(found), ["开心", "惊讶"])
        self.assertEqual(found["开心"].path, os.path.join(path, "happy.png"))
        self.assertEqual(stickers.listing(found), "- 开心：开心、得意的时候\n- 惊讶：被吓到的时候")

    def test_problems_are_named_and_left_out(self):
        path = self.make(GOOD + """
- file: happy.png
  label: 开心
- file: missing.png
  label: 不见了
- file: notes.txt
  label: 文字
- file: happy.png
  label: "[坏]"
- label: 没有文件
""", files=("happy.png", "shock.jpg", "notes.txt"))
        found, problems = stickers.load(path)
        self.assertEqual(list(found), ["开心", "惊讶"])
        text = "\n".join(problems)
        for phrase in ("重复", "找不到图片 missing.png", "只支持", "不能有方括号", "要有 file 和 label"):
            self.assertIn(phrase, text)

    def test_no_folder_no_stickers(self):
        self.assertEqual(stickers.load(tempfile.gettempdir() + "/no-such-sticker-folder"), ({}, []))
        self.assertEqual(stickers.load(), ({}, []))         # the tests never see local/

    def test_markers_in_a_reply(self):
        known = {"开心": None, "惊讶": None}
        self.assertEqual(stickers.extract("好呀～\n[表情:开心]", known), ("好呀～", ["开心"]))
        self.assertEqual(stickers.extract("哼 【表情：惊讶】 才不是", known), ("哼  才不是", ["惊讶"]))
        self.assertEqual(stickers.extract("[发送了表情包: 开心 ]\n[表情:惊讶]", known), ("", ["开心"]))   # one each reply
        self.assertEqual(stickers.extract("[表情:不存在]\n嗯", known), ("嗯", []))         # unknown: dropped
        self.assertEqual(stickers.extract("[表情:开心】不配对", known), ("[表情:开心】不配对", []))
        self.assertEqual(stickers.as_text("[表情:开心]"), "（开心）")

    def test_importing_a_seekchat_pack(self):
        pack = os.path.join(tempfile.mkdtemp(), "stickers.zip")
        self.addCleanup(shutil.rmtree, os.path.dirname(pack))
        entries = [{"file": "A1.jpg", "label": "A1", "desc": "嘲笑"},
                   {"file": "B2.jpg", "label": "得意", "desc": "很得意的时候"},
                   {"file": "C3.jpg", "label": "C3", "desc": "嘲笑"},
                   {"file": "../evil.jpg", "label": "x", "desc": "不该出去"},
                   {"file": "gone.jpg", "label": "gone", "desc": "包里没有"}]
        with zipfile.ZipFile(pack, "w") as z:
            z.writestr("stickers/stickers.json", json.dumps(entries, ensure_ascii=False))
            for name in ("A1.jpg", "B2.jpg", "C3.jpg", "evil.jpg"):
                z.writestr(f"stickers/{name}", PNG)
        target = os.path.join(tempfile.mkdtemp(), "stickers")
        self.addCleanup(shutil.rmtree, os.path.dirname(target))
        out = io.StringIO()
        self.assertEqual(stickers.main(["import", pack], target, out), 0)
        found, problems = stickers.load(target)
        self.assertEqual(problems, [])
        self.assertEqual({label: os.path.basename(s.path) for label, s in found.items()},
                         {"嘲笑": "A1.jpg", "得意": "B2.jpg", "嘲笑2": "C3.jpg", "x": "evil.jpg"})
        self.assertEqual(sorted(os.listdir(target)), ["A1.jpg", "B2.jpg", "C3.jpg", "evil.jpg", "stickers.yaml"])
        self.assertIn("skipped gone.jpg", out.getvalue())
        self.assertIn("4 stickers ready, 0 problems", out.getvalue())


class MarkdownTest(unittest.TestCase):
    def write(self, data, name):
        path = os.path.join(tempfile.mkdtemp(), name)
        self.addCleanup(shutil.rmtree, os.path.dirname(path))
        with open(path, "wb") as f:
            f.write(data)
        return path

    def test_how_big_a_sticker_shows(self):
        png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 300, 200) + b"\x08\x06\0\0\0"
        gif = b"GIF89a" + struct.pack("<HH", 64, 128) + b"\0" * 6
        jpeg = (b"\xff\xd8\xff\xe0" + struct.pack(">H", 16) + b"JFIF\0" + b"\0" * 9      # an APP0 segment,
                + b"\xff\xff\xc0" + struct.pack(">HBHH", 17, 8, 1524, 1080) + b"\0" * 10)  # padding, the frame
        self.assertEqual(stickers.image_size(self.write(png, "a.png")), (300, 200))
        self.assertEqual(stickers.image_size(self.write(gif, "b.gif")), (64, 128))
        self.assertEqual(stickers.image_size(self.write(jpeg, "c.jpg")), (1080, 1524))
        self.assertIsNone(stickers.image_size(self.write(PNG, "d.png")))          # too short to tell
        self.assertEqual(stickers.display_size(self.write(jpeg, "c.jpg"), 160), (113, 160))
        self.assertEqual(stickers.display_size(self.write(png, "a.png"), 120), (120, 80))
        self.assertEqual(stickers.display_size(self.write(PNG, "d.png"), 160), (160, 160))   # unknown: square

    def test_her_words_show_as_written(self):
        self.assertEqual(stickers.as_markdown("*哼* 才不是~\n- 一\n1. 二\n\n# 三\n> 引用 >_<"),
                         "＊哼＊ 才不是～  \n\u200b- 一  \n\u200b1. 二  \n\u200b  \n\u200b# 三  \n＞ 引用 ＞＿＜")
        self.assertEqual(stickers.as_markdown("[链接](x) a|b `c` \\"), "［链接］(x) a｜b ｀c｀ ＼")
        self.assertEqual(stickers.markdown_image("qqbot-media://image/v1/t", 113, 160),
                         "![表情 #113px #160px](qqbot-media://image/v1/t)")


class GatewayImageTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.dir = folder(GOOD)
        self.addCleanup(shutil.rmtree, self.dir)
        self.path = os.path.join(self.dir, "happy.png")
        self.gateway = QQGateway(SimpleNamespace())
        self.calls, self.requests, self.pages, self.refuse_direct, self.ttl = [], [], [], False, 0

        async def call(method, path, body=None, query=None, **params):
            self.calls.append((path, body))
            self.requests.append((method, path, query, params))
            if path.endswith("/files"):
                if "file_data" in body and self.refuse_direct:
                    raise OpenAPIError(400, 40034004, "不支持二进制上传")
                return {"file_info": f"FI{len(self.calls)}", "ttl": self.ttl}
            if path.endswith("/prepare"):
                return {"upload_id": "u1", "presigned_url": "https://cos.example.com/put?sign=1", "expires_in": 3600}
            if path.endswith("/complete"):
                return {"image_id": "i1", "url": "qqbot-media://image/v1/t1", "ttl": 7776000, "preview_url": "-"}
            if path == "/v2/media-images":
                return self.pages.pop(0)
            return {"id": "sent"}
        self.gateway._call = call

    async def test_uploaded_once_then_reused(self):
        await self.gateway.send_image(G, self.path, reply_to="m1")
        await self.gateway.send_image(G, self.path, reply_to="m2")
        uploads = [body for path, body in self.calls if path.endswith("/files")]
        self.assertEqual(len(uploads), 1)
        self.assertEqual(base64.b64decode(uploads[0]["file_data"]), PNG)
        self.assertEqual((uploads[0]["file_type"], uploads[0]["file_name"], uploads[0]["srv_send_msg"]),
                         (1, "happy.png", False))
        sends = [body for path, body in self.calls if path.endswith("/messages")]
        self.assertEqual(sends[0], {"msg_type": 7, "media": {"file_info": "FI1"}, "msg_id": "m1", "msg_seq": 1})
        self.assertEqual(sends[1]["media"], {"file_info": "FI1"})

    async def test_a_voice_message_is_uploaded_then_sent(self):
        await self.gateway.send_voice(G, b"ID3mp3", reply_to="m1")
        (upload_path, upload), (send_path, send) = self.calls
        self.assertTrue(upload_path.endswith("/files") and send_path.endswith("/messages"))
        self.assertEqual((upload["file_type"], upload["file_name"], upload["srv_send_msg"]), (3, "voice.mp3", False))
        self.assertEqual(base64.b64decode(upload["file_data"]), b"ID3mp3")
        self.assertEqual(send, {"msg_type": 7, "media": {"file_info": "FI1"}, "msg_id": "m1", "msg_seq": 1})

    async def test_an_expiring_upload_is_redone(self):
        self.ttl = 30                                     # under a minute left: upload again
        await self.gateway.send_image(G, self.path)
        await self.gateway.send_image(G, self.path)
        self.assertEqual(len([1 for path, _ in self.calls if path.endswith("/files")]), 2)

    async def test_the_url_route_when_qq_wont_take_the_file(self):
        self.refuse_direct = True
        await self.gateway.send_image(G, self.path, url="https://stickers.example.com/happy.png")
        uploads = [body for path, body in self.calls if path.endswith("/files")]
        self.assertEqual(uploads[1], {"file_type": 1, "url": "https://stickers.example.com/happy.png",
                                      "srv_send_msg": False})
        self.gateway._uploads.clear()
        with self.assertRaises(OpenAPIError):            # and with no URL to fall back on
            await self.gateway.send_image(G, self.path)

    async def test_markdown_must_show_its_images(self):
        content = "好呀\n\n![表情 #160px #160px](qqbot-media://image/v1/t1)"
        await self.gateway.send_markdown(G, content, reply_to="m1")
        self.assertEqual(self.requests[-1], ("POST", "/v2/groups/{group_openid}/messages", None,
                                             {"domain": openapi.API_DOMAIN, "group_openid": G}))
        self.assertEqual(self.calls[-1][1], {"msg_type": 2, "msg_id": "m1", "msg_seq": 1, "markdown": {
            "content": content, "force_verify_image_resource": True}})

    async def test_an_image_goes_into_the_library(self):
        put = []

        async def fake_put(url, data, kind):
            put.append((url, data, kind))
            return "etag-md5"
        self.gateway._put = fake_put
        self.assertEqual(await self.gateway.upload_library_image(self.path),
                         ("i1", "qqbot-media://image/v1/t1", 7776000))
        (_, prepare), (_, complete) = self.calls
        self.assertEqual({k: prepare[k] for k in ("file_name", "file_size", "content_type")},
                         {"file_name": "happy.png", "file_size": len(PNG), "content_type": "image/png"})
        self.assertEqual((len(prepare["md5"]), len(prepare["sha1"])), (32, 40))
        self.assertEqual(put, [("https://cos.example.com/put?sign=1", PNG, "image/png")])
        self.assertEqual(complete, {"upload_id": "u1", "md5": "etag-md5"})       # the ETag completes it
        self.assertTrue(all(params["domain"] == openapi.API_DOMAIN for *_, params in self.requests))

    async def test_listing_and_deleting_library_images(self):
        self.pages = [{"images": [{"image_id": "a"}], "next_cursor": "c2"}, {"images": [{"image_id": "b"}]}]
        self.assertEqual([image["image_id"] for image in await self.gateway.list_library_images()], ["a", "b"])
        self.assertEqual([query for _, _, query, _ in self.requests], [None, {"cursor": "c2"}])
        await self.gateway.delete_library_image("a")
        method, path, _, params = self.requests[-1]
        self.assertEqual((method, path, params["image_id"]), ("DELETE", "/v2/media-images/{image_id}/delete", "a"))


class CallAddressTest(unittest.IsolatedAsyncioTestCase):
    async def test_newer_apis_go_to_the_new_address(self):
        urls = []

        class Response:
            status, headers = 200, {}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def json(self, content_type=None):
                return {}

        class Session:
            def request(self, method, url, **kwargs):
                urls.append(url)
                return Response()

        async def check_session():
            pass

        http = SimpleNamespace(is_sandbox=False, _headers={}, _session=Session(), check_session=check_session)
        gateway = QQGateway(SimpleNamespace(_http=http))
        await gateway.delete_library_image("abc")
        await gateway._call("GET", "/v2/groups/{group_openid}/members", group_openid="G1")
        self.assertEqual(urls, ["https://api.bot.qq.com/v2/media-images/abc/delete",
                                "https://api.sgroup.qq.com/v2/groups/G1/members"])


class LibraryTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.dir = folder(GOOD)
        self.addCleanup(shutil.rmtree, self.dir)
        data = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, data)
        self.path = os.path.join(data, "data", "stickers.json")
        with open(os.path.join(self.dir, "shock.jpg"), "ab") as f:
            f.write(b"another picture")                 # the same bytes would be the same picture
        self.found, _ = stickers.load(self.dir)
        self.gw, self.now = FakeGateway(), 1_000_000

    def library(self):
        return stickers.Library(self.gw, {"happy.png", "shock.jpg"}, self.path, clock=lambda: self.now)

    async def test_uploaded_once_and_remembered_across_restarts(self):
        link = await self.library().link(self.found["开心"])
        self.assertEqual(link, "qqbot-media://image/v1/img1")
        self.assertEqual(await self.library().link(self.found["开心"]), link)
        self.assertEqual(self.gw.uploads, 1)

    async def test_a_changed_file_goes_up_again_and_its_old_version_goes(self):
        library = self.library()
        await library.link(self.found["开心"])
        with open(self.found["开心"].path, "ab") as f:
            f.write(b"edited")
        self.assertEqual(await library.link(self.found["开心"]), "qqbot-media://image/v1/img2")
        self.assertEqual(self.gw.library, {"img2": "happy.png"})

    async def test_unsent_for_nearly_90_days_it_goes_up_again(self):
        library = self.library()
        await library.link(self.found["开心"])
        self.now += 88 * 86400
        await library.link(self.found["开心"])            # still there, and kept longer for being sent
        self.now += 89 * 86400
        self.assertEqual(await library.link(self.found["开心"]), "qqbot-media://image/v1/img2")
        self.assertEqual(self.gw.library, {"img2": "happy.png"})

    async def test_a_full_library_is_tidied(self):
        self.gw.library = {"old1": "lost.png", "old2": "gone.png"}       # uploads this bot lost track of
        self.gw.library_room = 3
        library = self.library()
        await library.link(self.found["开心"])
        library.files = {"shock.jpg"}                                    # 开心 has left the folder since
        self.assertEqual(await library.link(self.found["惊讶"]), "qqbot-media://image/v1/img2")
        self.assertEqual(self.gw.library, {"img2": "shock.jpg"})


class RepliesWithStickersTest(AIChatTestCase):
    def setUp(self):
        self.dir = folder(GOOD)
        self.addCleanup(shutil.rmtree, self.dir)

    async def test_the_sticker_shows_small_inside_the_reply(self):
        self.make(stickers_dir=self.dir, sticker_size=120)
        self.ai_client.answers["chat"] += ["好呀～ *捏*\n[表情:开心]", "[表情:开心]"]
        await self.say("A", "今天考得不错")
        (group, content, replied_to), = self.gw.markdowns
        self.assertEqual((group, content), (G, "好呀～ ＊捏＊\n\n![表情 #120px #120px](qqbot-media://image/v1/img1)"))
        self.assertIsNotNone(replied_to)
        self.assertEqual((self.gw.sent, self.gw.images), ([], []))             # one message, nothing else
        self.assertEqual(self.store.recent_turns(G)[-1]["content"], "好呀～ *捏*\n[表情:开心]")
        instructions = self.last_chat()[0]["content"]
        self.assertIn("【表情包】", instructions)
        self.assertIn("- 开心：开心、得意的时候", instructions)
        await self.say("A", "再来一个")
        self.assertEqual(self.gw.markdowns[-1][1], "![表情 #120px #120px](qqbot-media://image/v1/img1)")
        self.assertEqual(self.gw.uploads, 1)

    async def test_when_qq_refuses_that_the_sticker_goes_under_the_text(self):
        self.make(stickers_dir=self.dir)
        self.gw.refuse_markdown = True
        self.ai_client.answers["chat"] += ["好呀～\n[表情:开心]", "嗯\n[表情:惊讶]"]
        self.assertEqual(await self.say("A", "今天考得不错"), "好呀～")
        self.assertEqual(self.gw.images[0][1], os.path.join(self.dir, "happy.png"))
        self.gw.refuse_markdown = False
        self.assertEqual(await self.say("A", "再来"), "嗯")                  # for an hour, the old way
        self.assertEqual(self.gw.markdowns, [])
        self.assertEqual(self.gw.images[1][1], os.path.join(self.dir, "shock.jpg"))

    async def test_the_old_way_with_a_url_to_fall_back_on_and_a_sticker_qq_refuses(self):
        self.make(stickers_dir=self.dir, stickers_url="https://stickers.example.com/", stickers_inline=False)
        self.ai_client.answers["chat"] += ["[表情:惊讶]", "[表情:开心]"]
        await self.say("A", "吓你一跳")
        self.assertEqual(self.gw.images[0][3], "https://stickers.example.com/shock.jpg")
        self.gw.refuse_images = True
        self.assertEqual(await self.say("A", "再来"), "（开心）")        # nothing went out: in words
        self.assertEqual(self.store.recent_turns(G)[-1]["content"], "（开心）")
        self.assertEqual(self.gw.markdowns, [])

    async def test_off_without_a_folder(self):
        self.make()
        self.ai_client.answers["chat"].append("好呀 [表情:开心]")
        self.assertEqual(await self.say("A", "嗨"), "好呀 [表情:开心]")   # no stickers: left as written
        self.assertNotIn("【表情包】", self.last_chat()[0]["content"])
        self.assertEqual((self.gw.images, self.gw.markdowns), ([], []))


if __name__ == "__main__":
    unittest.main()
