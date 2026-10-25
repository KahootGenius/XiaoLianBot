import unittest
from modules.manager.events import RichGroupMessage

PAYLOAD = {
    "id": "ROBOT1.0_abc",
    "content": "<@!BOTID> /请假 30 考试 <@!M2>",
    "timestamp": "2026-11-01T12:00:00+08:00",
    "group_openid": "G1",
    "author": {"member_openid": "M1", "username": "小明", "bot": False},
    "message_type": 0,
    "mentions": [
        {"scope": "single", "id": "BOTID", "is_you": True, "bot": True},
        {"scope": "single", "id": "M2", "member_openid": "M2", "nickname": "小红"},
    ],
    "attachments": [{"content_type": "image/png", "url": "https://example.com/a.png"}],
}


class RichGroupMessageTest(unittest.TestCase):
    def test_keeps_what_botpy_drops(self):
        m = RichGroupMessage(None, "evt", PAYLOAD, "GROUP_MESSAGE_CREATE")
        self.assertEqual((m.member_openid, m.nickname, m.is_bot), ("M1", "小明", False))
        self.assertEqual(m.group_openid, "G1")
        self.assertEqual(m.raw_content, PAYLOAD["content"])
        self.assertEqual(m.attachments_raw[0]["content_type"], "image/png")

    def test_content_reads_naturally_for_command_modules(self):
        m = RichGroupMessage(None, "evt", PAYLOAD, "GROUP_MESSAGE_CREATE")
        self.assertEqual(m.content, "/请假 30 考试 @小红")
        self.assertTrue(m.mentions_bot)
        self.assertEqual(m.mentioned_members, [("M2", "小红")])

    def test_mention_detection_without_is_you(self):
        RichGroupMessage.self_ids.add("BOTID")
        try:
            payload = dict(PAYLOAD, mentions=[], content="<@!BOTID> 你好")
            m = RichGroupMessage(None, "evt", payload, "GROUP_MESSAGE_CREATE")
            self.assertTrue(m.mentions_bot)
            self.assertEqual(m.content, "你好")
        finally:
            RichGroupMessage.self_ids.discard("BOTID")

    def test_plain_message_does_not_mention_bot(self):
        payload = dict(PAYLOAD, mentions=[], content="大家晚上好")
        m = RichGroupMessage(None, "evt", payload, "GROUP_MESSAGE_CREATE")
        self.assertFalse(m.mentions_bot)


if __name__ == "__main__":
    unittest.main()
