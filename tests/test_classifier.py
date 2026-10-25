import unittest
from modules.manager.classifier import ClassifierSettings, RecentMessages, judge

STICKER = '<faceType=1,faceId="14",ext="eyJ0ZXh0Ijoi5b6u56yRIn0=">'


class JudgeTest(unittest.TestCase):
    def assertJudged(self, content, valid, kind, **kwargs):
        j = judge(content, **kwargs)
        self.assertEqual((j.valid, j.kind), (valid, kind), f"{content!r} -> {j}")
        return j

    def test_substantive_text_counts(self):
        self.assertJudged("这个问题我觉得可以用第二种方法解决", True, "text")
        self.assertJudged("I think we should try the second approach", True, "text")

    def test_questions_and_replies_count_with_less_text(self):
        self.assertJudged("几点开始？", True, "question")
        self.assertJudged("这个是什么", True, "question")
        self.assertJudged("说得对", False, "short")
        self.assertJudged("说得对", True, "reply", message_type=103)
        self.assertJudged("啥？", False, "short")

    def test_emoji_stickers_and_fillers_do_not_count(self):
        self.assertJudged(STICKER, False, "sticker")
        self.assertJudged(STICKER * 3 + "  ", False, "sticker")
        self.assertJudged("哈哈哈哈哈哈", False, "filler")
        self.assertJudged("666666", False, "filler")
        self.assertJudged("好的收到", False, "short")
        self.assertJudged("<@!abc123> 好的", False, "filler")

    def test_check_ins_do_not_count(self):
        self.assertJudged("签到", False, "filler")
        self.assertJudged("每日签到打卡", False, "checkin")

    def test_sharing_counts(self):
        self.assertJudged("https://example.com/a-good-article", True, "share")
        self.assertJudged("", True, "share", attachments=[{"content_type": "application/pdf"}])
        self.assertJudged("看这个", True, "share", attachments=[{"content_type": "image/png"}])

    def test_media_alone_is_off_by_default(self):
        self.assertJudged("", False, "image", attachments=[{"content_type": "image/gif"}])
        lenient = ClassifierSettings(image_only_counts=True)
        self.assertJudged("", True, "image", attachments=[{"content_type": "image/png"}], settings=lenient)

    def test_voice_uses_speech_to_text(self):
        voice = [{"content_type": "voice", "asr_refer_text": "我觉得这个方案可以试试"}]
        self.assertJudged("", True, "text", attachments=voice)
        self.assertJudged("", False, "voice", attachments=[{"content_type": "voice"}])

    def test_commands_and_bot_chat_do_not_count(self):
        self.assertJudged("/我的状态", False, "command")
        self.assertJudged("今天天气怎么样呀小莲", False, "bot_chat", mentions_bot=True)

    def test_copy_paste_does_not_count(self):
        recent = RecentMessages()
        text = "今天的作业是第三章全部习题"
        first = self.assertJudged(text, True, "text", member="A", recent=recent, now_ts=1000)
        recent.remember("A", first, 1000)
        self.assertJudged(text, False, "repeat", member="B", recent=recent, now_ts=1060)
        self.assertJudged(text, False, "duplicate", member="A", recent=recent, now_ts=1120)
        # Outside the 30-minute window the same words are fine again.
        self.assertJudged(text, True, "text", member="C", recent=recent, now_ts=1000 + 3600)

    def test_jielong_chains_do_not_count(self):
        recent = RecentMessages()
        first = judge("#接龙 周六聚餐\n1. 张三\n2. 李四", member="A", recent=recent, now_ts=0)
        recent.remember("A", first, 0)
        self.assertJudged("#接龙 周六聚餐\n1. 张三\n2. 李四\n3. 王五", False, "chain",
                          member="B", recent=recent, now_ts=60)


if __name__ == "__main__":
    unittest.main()
