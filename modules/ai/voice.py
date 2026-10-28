"""Her voice: short voice messages spoken by MiniMax (text to speech), as in seekchat's 语音.

When a moment calls for it (good night, a birthday wish), she writes [语音:要说的话] on a line of
its own. The bot takes the marker out of the reply, has MiniMax say those words in the voice set
in config.yaml (voice.voice_id), and sends the mp3 under the reply as a QQ voice message. Only
her own words go to MiniMax, never anything a member said.
"""
import re
import aiohttp


# A MiniMax key and its voices belong to the site they were made on. The 国内 site moved from
# minimaxi.com to minimax.cn (seekchat still uses api.minimaxi.chat); voice.host overrides either.
HOSTS = {"global": "https://api.minimax.io", "cn": "https://api.minimax.cn"}
MODEL = "speech-2.8-turbo"
MAX_CHARS = 60      # a voice message is a sentence or two, not a speech
PER_DAY = 5         # voice messages per group per day, unless voice.per_day says otherwise
TIMEOUT = 30        # seconds MiniMax gets to speak

MARKER = re.compile(r"\[[ \t]*语音[ \t]*[:：][ \t]*([^\]\n]{1,200}?)[ \t]*\]"
                    r"|【[ \t]*语音[ \t]*[:：][ \t]*([^】\n]{1,200}?)[ \t]*】")
# What a voice can't say (seekchat's voicetext.ts): other markers, actions in brackets, emoji.
_OTHER_MARKER = re.compile(r"\[[^\[\]\n]{1,40}[:：][^\[\]\n]{0,80}\]|【[^【】\n]{1,40}[:：][^【】\n]{0,80}】")
_ACTION = re.compile(r"（[^（）]*）|\([^()]*\)")
_EMOJI = re.compile("[\U0001F300-\U0001FBFF\u2600-\u27BF\u2B00-\u2BFF\u2300-\u23FF\u200D\uFE0F]")
_PAUSES = "。！？!?～~…，,"


class VoiceError(Exception):
    """MiniMax didn't speak. The message carries its status code: 1004 is a wrong key, or a key
    from the other site (voice.region)."""


def _words(match):
    return (match.group(1) or match.group(2)).strip()


def extract(text, limit=1):
    """(the reply without voice markers, [what to say]). The first `limit` markers leave the text
    to be spoken; any after them stay as plain words."""
    spoken = []

    def take(match):
        if _words(match) and len(spoken) < limit:
            spoken.append(_words(match))
            return ""
        return _words(match)

    clean = MARKER.sub(take, text)
    clean = re.sub(r"[ \t]+\n", "\n", clean)
    return re.sub(r"\n{3,}", "\n\n", clean).strip(), spoken


def as_text(text):
    """A reply whose voice can't go out, with each marker turned back into her words."""
    return MARKER.sub(_words, text).strip()


def speakable(text, limit=MAX_CHARS):
    """What MiniMax should say: no markers, bracketed actions or emoji, and at most `limit`
    characters, cut after a pause where there is one."""
    text = _OTHER_MARKER.sub("", MARKER.sub(_words, text))
    text = re.sub(r"\s+", " ", _EMOJI.sub("", _ACTION.sub("", text))).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    pause = max(cut.rfind(mark) for mark in _PAUSES)
    return cut[:pause + 1] if pause >= limit // 2 else cut


def audio_from(answer):
    """The mp3 bytes in a t2a_v2 answer. Raises VoiceError with MiniMax's status when it refused."""
    status = (answer or {}).get("base_resp") or {}
    if status.get("status_code") != 0:
        raise VoiceError(f"MiniMax {status.get('status_code')}: {status.get('status_msg') or '没有说明'}")
    try:
        audio = bytes.fromhex(str((answer.get("data") or {}).get("audio") or ""))
    except ValueError:
        audio = b""
    if not audio:
        raise VoiceError("MiniMax 的回答里没有读到音频")
    return audio


class MiniMax:
    """MiniMax's text to speech (POST /v1/t2a_v2): words in, mp3 bytes out."""

    def __init__(self, api_key, voice_id, region="global", model=MODEL, speed=1.0, host=None):
        if region not in HOSTS:
            raise ValueError(f"voice.region 只能是 {' 或 '.join(HOSTS)}，现在是 {region}")
        self.api_key, self.voice_id, self.host = api_key, voice_id, (host or HOSTS[region]).rstrip("/")
        self.model, self.speed = model, speed

    async def speak(self, text):
        body = {"model": self.model, "text": text, "stream": False, "output_format": "hex",
                "voice_setting": {"voice_id": self.voice_id, "speed": self.speed, "vol": 1, "pitch": 0},
                "audio_setting": {"format": "mp3", "sample_rate": 32000, "bitrate": 128000, "channel": 1}}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as session:
            async with session.post(f"{self.host}/v1/t2a_v2", json=body,
                                    headers={"Authorization": f"Bearer {self.api_key}"}) as resp:
                if resp.status != 200:
                    raise VoiceError(f"MiniMax HTTP {resp.status}: {(await resp.text())[:200]}")
                return audio_from(await resp.json(content_type=None))
