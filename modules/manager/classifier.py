"""Decides whether a group message counts as 有效发言 (valid speech).

Group rule: substantive discussion, questions, answers and sharing count;
pure emoji/stickers, check-ins and copy-paste do not. This is a rule-based,
deliberately lenient judgement -- a wrong "invalid" can get someone removed,
a wrong "valid" only buys them more time.
"""
import re
import unicodedata
from collections import deque
from dataclasses import dataclass, field


# Inline markup QQ embeds in message text.
FACE_TAG_RE = re.compile(r'<faceType=\d+,faceId="[^"]*",ext="[^"]*">')
MENTION_RE = re.compile(r'<@!?[^>\s]+>')
QQBOT_TAG_RE = re.compile(r'<qqbot-[^>]*>')
URL_RE = re.compile(r'https?://\S+', re.IGNORECASE)
# A 1-3 character unit repeated 3+ times ("哈哈哈哈", "233333", "hahaha") collapses to one copy.
REPEAT_RE = re.compile(r'(.{1,3}?)\1{2,}')
CJK_RE = re.compile(r'[㐀-䶿一-鿿豈-﫿]')
WORD_RE = re.compile(r'[a-z0-9]+')
QUESTION_END_RE = re.compile(r'[吗呢么嘛]$')

# message_type of a quote-reply, per Tencent's own qqbot SDK.
MSG_TYPE_QUOTE = 103

CHECKIN_WORDS = ("签到", "打卡", "冒泡")
# Whole messages that are never substantive, compared after normalize().
DEFAULT_FILLERS = frozenset({
    "签到", "打卡", "冒泡", "每日签到", "早", "早安", "早上好", "午安", "晚安", "晚上好",
    "哈", "嗯", "哦", "噢", "嗷", "啊", "好", "好的", "好滴", "收到", "了解", "明白",
    "ok", "okk", "1", "2", "6", "66", "草", "顶", "赞", "牛", "强", "在", "在吗",
    "谢谢", "感谢", "辛苦了", "哈哈", "嘿嘿", "嘻嘻", "呜呜", "笑死", "确实", "是的", "对",
})


@dataclass
class ClassifierSettings:
    min_units: int = 5                 # "units": CJK characters plus Latin/number words
    min_units_question: int = 3
    min_units_reply: int = 3
    min_units_with_media: int = 3      # a photo with a short caption is sharing
    image_only_counts: bool = False    # stickers arrive as images too, so off by default
    video_only_counts: bool = False
    voice_counts: bool = False         # voice without speech-to-text attached
    files_count: bool = True
    links_count: bool = True
    count_bot_chat: bool = False       # chatting with the bot shouldn't keep someone in the group
    repeat_minutes: int = 30
    fillers: frozenset = DEFAULT_FILLERS

    @classmethod
    def from_config(cls, cfg):
        cfg = cfg or {}
        known = {k: cfg[k] for k in cls.__dataclass_fields__ if k in cfg and k != "fillers"}
        settings = cls(**known)
        extra = cfg.get("extra_fillers") or []
        if extra:
            settings.fillers = DEFAULT_FILLERS | {normalize(str(w)) for w in extra}
        return settings


@dataclass
class Judgement:
    valid: bool
    kind: str            # why: text/question/reply/share or sticker/short/filler/checkin/repeat/...
    units: int = 0
    question: bool = False
    reply: bool = False
    share: bool = False
    text: str = ""       # cleaned text, used for the "last valid message" snippet
    norm: str = ""
    lines: frozenset = field(default_factory=frozenset)


def clean_text(content):
    """Strip QQ face tags and @mention markers, keeping only what the person typed."""
    text = FACE_TAG_RE.sub(" ", content or "")
    text = MENTION_RE.sub(" ", text)
    text = QQBOT_TAG_RE.sub(" ", text)
    return text.strip()


def normalize(text):
    """Lowercase, drop punctuation/symbols/emoji, collapse "哈哈哈哈"-style repeats."""
    kept = []
    for ch in (text or "").lower():
        category = unicodedata.category(ch)
        if category[0] in ("L", "N"):
            kept.append(ch)
        elif ch.isspace():
            kept.append(" ")
    collapsed = REPEAT_RE.sub(r"\1", "".join(kept))
    return " ".join(collapsed.split())


def count_units(norm):
    cjk = len(CJK_RE.findall(norm))
    words = len(WORD_RE.findall(norm))
    # Kana, hangul and other non-ASCII letters count one per character.
    other = sum(1 for ch in norm if ch.isalpha() and not ch.isascii() and not CJK_RE.match(ch))
    return cjk + words + other


def is_question(text):
    if "?" in text or "？" in text:
        return True
    tail = re.sub(r'[\s\W]+$', '', text)
    return bool(QUESTION_END_RE.search(tail))


def attachment_kinds(attachments):
    kinds = set()
    for att in attachments or ():
        content_type = (att.get("content_type") or "").lower()
        if content_type.startswith("image/"):
            kinds.add("image")
        elif content_type == "voice" or content_type.startswith("audio/") or "silk" in content_type or "amr" in content_type:
            kinds.add("voice")
        elif content_type.startswith("video/"):
            kinds.add("video")
        elif content_type.startswith(("application/", "text/")) or content_type == "file":
            kinds.add("file")
    return kinds


class RecentMessages:
    """Short memory of one group's latest messages, for spotting 复读 and 接龙."""

    def __init__(self, size=40, minutes=30):
        self.items = deque(maxlen=size)
        self.window = minutes * 60

    def check(self, member, norm, lines, now_ts):
        for other_member, other_norm, other_lines, ts in reversed(self.items):
            if now_ts - ts > self.window:
                break
            if norm and norm == other_norm:
                return "duplicate" if other_member == member else "repeat"
            # 接龙: the new message re-posts most lines of an earlier one plus its own.
            if len(lines) >= 2 and len(other_lines) >= 2:
                shared = len(lines & other_lines)
                if shared >= 2 and shared >= 0.8 * len(other_lines):
                    return "chain"
        return None

    def remember(self, member, judgement, now_ts):
        if judgement.norm:
            self.items.append((member, judgement.norm, judgement.lines, now_ts))


def judge(content, *, attachments=(), message_type=None, mentions_bot=False,
          member=None, recent=None, now_ts=0, settings=None):
    settings = settings or ClassifierSettings()
    raw = content or ""
    text = clean_text(raw)
    if text.startswith("/"):
        return Judgement(False, "command", text=text)
    if mentions_bot and not settings.count_bot_chat:
        return Judgement(False, "bot_chat", text=text)

    kinds = attachment_kinds(attachments)
    # Voice messages may carry the platform's speech-to-text; judge that like typed text.
    asr = " ".join(a.get("asr_refer_text") or "" for a in attachments or () if a.get("asr_refer_text"))
    if asr:
        text = f"{text} {asr}".strip()

    norm = normalize(text)
    units = count_units(norm)
    lines = frozenset(n for n in (normalize(line) for line in text.splitlines()) if n)
    has_link = bool(URL_RE.search(text))
    base = dict(units=units, text=text, norm=norm, lines=lines)

    if units == 0 and not has_link:
        if "file" in kinds and settings.files_count:
            return Judgement(True, "share", share=True, **base)
        if "image" in kinds:
            return Judgement(settings.image_only_counts, "image", share=settings.image_only_counts, **base)
        if "video" in kinds:
            return Judgement(settings.video_only_counts, "video", share=settings.video_only_counts, **base)
        if "voice" in kinds:
            return Judgement(settings.voice_counts, "voice", **base)
        if FACE_TAG_RE.search(raw):
            return Judgement(False, "sticker", **base)
        return Judgement(False, "empty", **base)

    if recent is not None:
        copied = recent.check(member, norm, lines, now_ts)
        if copied:
            return Judgement(False, copied, **base)

    if norm in settings.fillers:
        return Judgement(False, "filler", **base)
    if units <= 8 and any(word in norm for word in CHECKIN_WORDS):
        return Judgement(False, "checkin", **base)

    if (has_link and settings.links_count) or ("file" in kinds and settings.files_count):
        return Judgement(True, "share", share=True, **base)

    question = is_question(text)
    reply = message_type == MSG_TYPE_QUOTE
    with_media = bool(kinds & {"image", "video"})
    threshold = settings.min_units
    if question:
        threshold = min(threshold, settings.min_units_question)
    if reply:
        threshold = min(threshold, settings.min_units_reply)
    if with_media:
        threshold = min(threshold, settings.min_units_with_media)
    if units < threshold:
        return Judgement(False, "short", question=question, reply=reply, **base)

    if question:
        kind = "question"
    elif reply:
        kind = "reply"
    elif with_media:
        kind = "share"
    else:
        kind = "text"
    return Judgement(True, kind, question=question, reply=reply, share=with_media, **base)
