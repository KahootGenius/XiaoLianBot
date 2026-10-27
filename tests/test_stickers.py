"""Stickers: the private folder, [表情:标签] in replies, and uploading them to QQ."""
import base64
import io
import json
import os
import shutil
import tempfile
import time
import unittest
import zipfile
from types import SimpleNamespace
from modules.ai import stickers
from modules.manager.openapi import OpenAPIError, QQGateway
from tests.test_ai_chat import G, AIChatTestCase

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


class GatewayImageTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.dir = folder(GOOD)
        self.addCleanup(shutil.rmtree, self.dir)
        self.path = os.path.join(self.dir, "happy.png")
        self.gateway = QQGateway(SimpleNamespace())
        self.calls, self.refuse_direct, self.ttl = [], False, 0

        async def call(method, path, body=None, query=None, **params):
            self.calls.append((path, body))
            if path.endswith("/files"):
                if "file_data" in body and self.refuse_direct:
                    raise OpenAPIError(400, 40034004, "不支持二进制上传")
                return {"file_info": f"FI{len(self.calls)}", "ttl": self.ttl}
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


class RepliesWithStickersTest(AIChatTestCase):
    def setUp(self):
        self.dir = folder(GOOD)
        self.addCleanup(shutil.rmtree, self.dir)

    async def test_the_sticker_goes_right_under_the_text(self):
        self.make(stickers_dir=self.dir)
        self.ai_client.answers["chat"].append("好呀～\n[表情:开心]")
        reply = await self.say("A", "今天考得不错")
        self.assertEqual(reply, "好呀～")
        replied_to = self.gw.sent[-1][2]                              # the same message as the text
        self.assertEqual(self.gw.images, [(G, os.path.join(self.dir, "happy.png"), replied_to, None)])
        instructions = self.last_chat()[0]["content"]
        self.assertIn("【表情包】", instructions)
        self.assertIn("- 开心：开心、得意的时候", instructions)
        self.assertEqual(self.store.recent_turns(G)[-1]["content"], "好呀～\n[表情:开心]")

    async def test_a_url_to_fall_back_on_and_a_sticker_qq_refuses(self):
        self.make(stickers_dir=self.dir, stickers_url="https://stickers.example.com/")
        self.ai_client.answers["chat"] += ["[表情:惊讶]", "[表情:开心]"]
        await self.say("A", "吓你一跳")
        self.assertEqual(self.gw.images[0][3], "https://stickers.example.com/shock.jpg")
        self.gw.refuse_images = True
        self.assertEqual(await self.say("A", "再来"), "（开心）")        # nothing went out: in words
        self.assertEqual(self.store.recent_turns(G)[-1]["content"], "（开心）")

    async def test_off_without_a_folder(self):
        self.make()
        self.ai_client.answers["chat"].append("好呀 [表情:开心]")
        self.assertEqual(await self.say("A", "嗨"), "好呀 [表情:开心]")   # no stickers: left as written
        self.assertNotIn("【表情包】", self.last_chat()[0]["content"])
        self.assertEqual(self.gw.images, [])


if __name__ == "__main__":
    unittest.main()
