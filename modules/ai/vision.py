"""Pictures for AI chat: fetching one from QQ, what she has of it, and seeing what QQ really sends.

A picture reaches the bot as a link on QQ's servers, in a message's attachments, or nested in
msg_elements when someone quotes it (RichGroupMessage.images). A link carries a token and may not
last, so a picture is fetched when it's needed and held in memory only: nothing here writes to
disk, and a link is never logged or shown. /看图测试 uses this to check what QQ sends; shape() is
the same check for the log.

She looks at a picture directly, in the reply call. Before that a separate call that knows nothing
about the group says whether real people are in it (prompts/chat.yaml person_check). If so, her
reply gets nothing about the group either. In tests she pinned photos of strangers on members she
remembered in 21 replies of 25; a rule against it still left 15, and a neutral description of the
picture in place of the picture itself, 22. She can't name someone she was never told about.
"""
import asyncio
import base64
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit
import aiohttp
from botpy import logging


_log = logging.get_logger()

TIMEOUT = 8                     # seconds for one picture
MAX_BYTES = 10 * 1024 * 1024    # the server has 1 GB of memory, and phones send far less than this
MAX_PICTURES = 3                # from one message
MAX_REDIRECTS = 3
PER_DAY = 50                    # pictures a group can show her a day (ai_chat.vision.per_day)
RETRY_SECONDS = 600             # after DeepSeek fails on one, she doesn't look for this long
CAREFUL_SECONDS = 120           # after real people in a picture, her replies stay this careful
QUOTE = 103                     # message_type of a message that quotes another
QQ_DOMAINS = ("qq.com", "qq.com.cn", "qpic.cn")
REDIRECTS = (301, 302, 303, 307, 308)
# What DeepSeek reads, by how the file starts, since a link's content_type can be wrong. WebP is RIFF.
FORMATS = ((b"\xff\xd8\xff", "image/jpeg"), (b"\x89PNG\r\n\x1a\n", "image/png"), (b"GIF8", "image/gif"))


@dataclass
class Sight:
    """What she has of a message's pictures, for her reply."""
    pictures: list = field(default_factory=list)    # as data URLs, for the request
    strangers: bool = False     # real people may be in them: her reply gets nothing about the group
    missed: bool = False        # the message has pictures she can't see, which she should say


NOTHING = Sight()


class PictureError(Exception):
    """A picture that couldn't be fetched. `reason` names a line in the style's ai.picture_test.why
    and `detail` goes in it. Neither ever holds the link, which QQ would refuse in a message."""

    def __init__(self, reason, detail=""):
        super().__init__(reason)
        self.reason, self.detail = reason, detail


def qq_link(url):
    """`url` as an https link, or None unless it's on QQ's own servers. Links can come without a
    scheme. Nothing else is fetched, wherever a message or a redirect points."""
    url = re.sub(r"^(https?:)?//", "", str(url or "").strip(), flags=re.I)
    try:
        parts = urlsplit(f"https://{url}")
        host = (parts.hostname or "").lower()
    except ValueError:
        return None
    if "\\" in url or "@" in parts.netloc or not any(host == d or host.endswith(f".{d}") for d in QQ_DOMAINS):
        return None
    return parts.geturl()


def _host(url):
    """Where a link points, without the rest of it, which can hold a token."""
    return re.split(r"[/?#]", re.sub(r"^(\w+:)?/*", "", url))[0]


def image_format(data):
    """The format of a picture DeepSeek can read, from the file itself, or None."""
    for start, name in FORMATS:
        if data.startswith(start):
            return name
    return "image/webp" if data[:4] == b"RIFF" and data[8:12] == b"WEBP" else None


def data_url(data, kind):
    """A picture as a request carries it: DeepSeek gets the picture itself, never QQ's link."""
    return f"data:{kind};base64,{base64.b64encode(data).decode()}"


async def _read(resp):
    data = bytearray()
    async for chunk in resp.content.iter_chunked(64 * 1024):
        data += chunk
        if len(data) > MAX_BYTES:
            raise PictureError("too_big", MAX_BYTES // (1024 * 1024))
    return bytes(data)


async def fetch(url):
    """(bytes, format) of the picture at `url`. Raises PictureError."""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as session:
            for _ in range(MAX_REDIRECTS + 1):
                link = qq_link(url)
                if link is None:
                    _log.info(f"[Picture] 不是 QQ 的服务器，没有打开：{_host(str(url))}")
                    raise PictureError("not_qq")
                async with session.get(link, allow_redirects=False) as resp:
                    if resp.status in REDIRECTS:
                        url = urljoin(link, resp.headers.get("Location", ""))
                        continue
                    if resp.status != 200:
                        raise PictureError("http", resp.status)
                    data = await _read(resp)
                kind = image_format(data)
                if kind is None:
                    raise PictureError("not_image")
                return data, kind
    except asyncio.TimeoutError:
        raise PictureError("timeout") from None
    except aiohttp.ClientError as e:        # its message can hold the link, its type can't
        raise PictureError("network", type(e).__name__) from None
    raise PictureError("redirects")


def _attachment(item, hosts):
    found = f"{item.get('content_type')} {item.get('width')}x{item.get('height')} {item.get('size')}B"
    if not hosts:
        return found
    url = str(item.get("url") or "")
    scheme = url.split("://")[0] if "://" in url else "no-scheme"
    return f"{found} {scheme}:{_host(url)}"


def shape(part, hosts=True):
    """One line on what a message carries: its structure, never anyone's words, a link or a token.
    QQ refuses a message with a web address in it, so a reply leaves hosts out."""
    found = [f"type={part.get('message_type')}", f"text={len(part.get('content') or '')}"]
    found += [_attachment(item, hosts) for item in part.get("attachments") or []]
    if part.get("ark_data"):
        found.append(f"ark={part['ark_data'].get('ark_type')}")
    scene = (part.get("message_scene") or {}).get("ext") or []
    if scene:
        found.append("scene=" + ",".join(sorted(str(entry).split("=")[0] for entry in scene)))
    elements = part.get("msg_elements") or []
    if elements:
        found.append("elements[" + " | ".join(shape(element, hosts) for element in elements) + "]")
    return " ".join(found)


def note(message):
    """Log what came with an @-message, so what QQ sends can be seen even if no command ran."""
    raw = message.raw
    if raw.get("attachments") or raw.get("msg_elements") or raw.get("ark_data"):
        _log.info(f"[Picture] {shape(raw)}")
