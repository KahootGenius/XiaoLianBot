"""Stickers for AI chat: images she can send under a reply by writing [表情:标签].

As in seekchat: a manifest gives each sticker a short label and when to use it,
her prompt lists them, and a marker in her reply becomes the image. It goes out
inside the reply, small, as markdown showing a picture from QQ's image library
(Library below); if that fails, as its own message right under the text.
Stickers are private, in local/stickers/, never on GitHub:

    local/stickers/stickers.yaml        - file: happy.png
                                          label: 开心
                                          desc: 开心、得意的时候
    local/stickers/happy.png

    python -m modules.ai.stickers                          # check the folder
    python -m modules.ai.stickers import stickers.zip      # bring in a seekchat sticker pack

Talking to QQ is QQGateway's job: send_markdown, upload_library_image, send_image.
"""
import hashlib
import json
import os
import re
import struct
import sys
import time
import zipfile
from dataclasses import dataclass
import yaml
from botpy import logging
from core.config import LOCAL_DIR, root_path, use_local


_log = logging.get_logger()


STICKER_DIR = os.path.join(LOCAL_DIR, "stickers")
MANIFEST = "stickers.yaml"
FORMATS = (".png", ".jpg", ".jpeg", ".gif")
MAX_BYTES = 5 * 1024 * 1024     # the most QQ takes in a direct upload
MAX_LABEL = 20
MAX_PER_REPLY = 1               # a group chat, not a sticker war
SIZE = 160                      # how big a sticker shows inside a reply: its longer side, in px
LIBRARY_FILE = root_path("data", "stickers.json")
LIBRARY_FULL = 40035007         # QQ's image library holds 100 images per bot
LIBRARY_TTL = 90 * 86400        # and keeps each one this long after it was last sent

# Tolerant like seekchat's: the model drifts into [表情包:X], [发送了表情包:X] and 【表情：X】,
# so every variant counts. Each opener pairs with its own closer, and nothing crosses a line.
MARKER = re.compile(r"\[[ \t]*(?:发送了)?表情包?[ \t]*[:：][ \t]*([^\]\n]{1,40}?)[ \t]*\]"
                    r"|【[ \t]*(?:发送了)?表情包?[ \t]*[:：][ \t]*([^】\n]{1,40}?)[ \t]*】")

HEADER = """\
# 表情包：AI 聊天时她可以发的图片。每张一项：
#   file  图片文件名，放在这个文件夹里（png、jpg 或 gif，每张不超过 5MB）
#   label 标签：她在回复里写 [表情:标签] 就会发这张，不超过 20 个字，不能重复
#   desc  什么时候用，写给她看的
# 改完运行 python -m modules.ai.stickers 检查，再重启机器人。
"""


@dataclass(frozen=True)
class Sticker:
    label: str
    desc: str
    path: str


def load(directory=None):
    """({label: Sticker}, [problems]) from a sticker folder's stickers.yaml. No folder or no manifest
    means no stickers. A broken entry is left out and named in the problems."""
    if directory is None:
        if not use_local():
            return {}, []
        directory = STICKER_DIR
    manifest = os.path.join(directory, MANIFEST)
    if not os.path.exists(manifest):
        return {}, []
    try:
        with open(manifest, encoding="utf-8") as f:
            entries = yaml.safe_load(f) or []
    except yaml.YAMLError as e:
        return {}, [f"{MANIFEST} 写得不对：{e}"]
    if not isinstance(entries, list):
        return {}, [f"{MANIFEST} 要写成列表，每张图以「- file:」开头"]
    stickers, problems = {}, []
    for i, entry in enumerate(entries, 1):
        where = f"第 {i} 张"
        if not isinstance(entry, dict) or not all(isinstance(entry.get(k), str) for k in ("file", "label")):
            problems.append(f"{where}：要有 file 和 label")
            continue
        label, file = entry["label"].strip(), entry["file"].strip()
        path = os.path.join(directory, os.path.basename(file))
        if not label or len(label) > MAX_LABEL or re.search(r"[\[\]【】\n]", label):
            problems.append(f"{where}（{file}）：label 要有，不超过 {MAX_LABEL} 个字，不能有方括号或换行")
        elif label in stickers:
            problems.append(f"{where}（{file}）：label「{label}」和前面的重复了")
        elif not file.lower().endswith(FORMATS):
            problems.append(f"{where}（{file}）：只支持 {'、'.join(FORMATS)}")
        elif not os.path.isfile(path):
            problems.append(f"{where}：找不到图片 {file}")
        elif os.path.getsize(path) > MAX_BYTES:
            problems.append(f"{where}（{file}）：超过 5MB，QQ 不收")
        else:
            stickers[label] = Sticker(label, str(entry.get("desc") or "").strip(), path)
    return stickers, problems


def extract(text, known, limit=MAX_PER_REPLY):
    """(the reply without sticker markers, [labels to send]). Every marker leaves the text; only
    labels in `known` are sent, each once, at most `limit`."""
    labels = []

    def take(match):
        label = (match.group(1) or match.group(2)).strip()
        if label in known and label not in labels and len(labels) < limit:
            labels.append(label)
        return ""

    clean = MARKER.sub(take, text)
    clean = re.sub(r"[ \t]+\n", "\n", clean)
    return re.sub(r"\n{3,}", "\n\n", clean).strip(), labels


def as_text(text):
    """A reply whose stickers couldn't be sent, with each marker turned into words."""
    return MARKER.sub(lambda m: f"（{(m.group(1) or m.group(2)).strip()}）", text).strip()


def listing(stickers):
    """The lines her prompt gets: "- 标签：什么时候用"."""
    return "\n".join(f"- {s.label}：{s.desc}" if s.desc else f"- {s.label}" for s in stickers.values())


def image_size(path):
    """(width, height) of a PNG, GIF or JPEG, read from its header; None if it can't tell."""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
            if head.startswith(b"\x89PNG\r\n\x1a\n") and len(head) == 24:
                return struct.unpack(">II", head[16:24])
            if head[:6] in (b"GIF87a", b"GIF89a") and len(head) >= 10:
                return struct.unpack("<HH", head[6:10])
            if head[:2] == b"\xff\xd8":
                f.seek(2)
                return _jpeg_size(f)
    except (OSError, struct.error):
        pass
    return None


def _jpeg_size(f):
    """Walk a JPEG's segments to the frame header, which holds the size."""
    while True:
        if f.read(1) != b"\xff":
            return None
        code = f.read(1)
        while code == b"\xff":                     # padding before a marker
            code = f.read(1)
        if not code:
            return None
        if code[0] == 0x01 or 0xD0 <= code[0] <= 0xD8:     # markers without a length
            continue
        length, = struct.unpack(">H", f.read(2))
        if 0xC0 <= code[0] <= 0xCF and code[0] not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">xHH", f.read(5))
            return width, height
        f.seek(length - 2, 1)


def display_size(path, size=SIZE):
    """(width, height) to show an image at: its longer side `size` px, the shape kept."""
    found = image_size(path)
    if not found or not all(found):
        return size, size
    width, height = found
    scale = size / max(width, height)
    return max(1, round(width * scale)), max(1, round(height * scale))


# Characters markdown would act on, swapped for full-width look-alikes so her words show as written
# (QQ's markdown doesn't promise backslash escapes). Line starts that would make a list or a rule too.
_PLAIN = str.maketrans({"*": "＊", "_": "＿", "~": "～", "`": "｀", "#": "＃", ">": "＞", "<": "＜",
                        "[": "［", "]": "］", "|": "｜", "\\": "＼"})
_LINE_START = re.compile(r"^(?:([-+=])|(\d+)([.)]))")
_WIDE = {"-": "－", "+": "＋", "=": "＝", ".": "．", ")": "）"}


def as_markdown(text):
    """Plain text as markdown that shows the same: each line a paragraph, nothing read as syntax."""
    lines = (_LINE_START.sub(lambda m: _WIDE[m.group(1)] if m.group(1) else m.group(2) + _WIDE[m.group(3)],
                             line.strip())
             for line in text.translate(_PLAIN).splitlines())
    return "\n\n".join(line for line in lines if line)


def markdown_image(link, width, height):
    return f"![表情 #{width}px #{height}px]({link})"


class Library:
    """Stickers in QQ's image library (素材管理), for markdown to show. Each image is uploaded once:
    its qqbot-media:// link is kept in data/stickers.json (path None: in memory only), under the
    image's sha1, so a changed file goes up again and its old version is deleted. QQ forgets an
    image 90 days after it was last sent and holds 100 per bot; when the library is full, images no
    current sticker uses are deleted to make room."""

    def __init__(self, gateway, files=(), path=LIBRARY_FILE, clock=time.time):
        self.gw, self.path, self.clock = gateway, path, clock
        self.files = set(files)             # the sticker folder's images, the ones worth keeping
        self.entries = {}                   # sha1 -> {"id", "link", "file", "ttl", "used"}
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    self.entries = json.load(f)
            except (OSError, ValueError) as e:
                _log.warning(f"[Stickers] {path} 读不了，表情包会重新上传: {e}")

    async def link(self, sticker):
        """The sticker's qqbot-media:// link, uploading it first if the library doesn't have it."""
        with open(sticker.path, "rb") as f:
            digest = hashlib.sha1(f.read()).hexdigest()
        now = self.clock()
        entry = self.entries.get(digest)
        if entry and now - entry["used"] < entry["ttl"] - 86400:
            entry["used"] = now
            self._save()
            return entry["link"]
        if entry:                               # unsent for nearly 90 days: QQ is about to forget it
            await self._delete(digest)
        try:
            image_id, link, ttl = await self.gw.upload_library_image(sticker.path)
        except Exception as e:
            if getattr(e, "code", None) != LIBRARY_FULL:
                raise
            await self._tidy()
            image_id, link, ttl = await self.gw.upload_library_image(sticker.path)
        file = os.path.basename(sticker.path)
        for old in [d for d, e in self.entries.items() if e["file"] == file]:      # its older versions
            await self._delete(old)
        self.entries[digest] = {"id": image_id, "link": link, "file": file, "ttl": ttl or LIBRARY_TTL, "used": now}
        self._save()
        return link

    async def _tidy(self):
        """The library is full: delete what no current sticker uses, including images it has that
        this bot has lost track of."""
        _log.info("[Stickers] QQ 图片库满了（每个机器人 100 张），清理不再用的图")
        for digest in [d for d, e in self.entries.items() if e["file"] not in self.files]:
            await self._delete(digest)
        ours = {entry["id"] for entry in self.entries.values()}
        for image in await self.gw.list_library_images():
            if image.get("image_id") and image["image_id"] not in ours:
                await self._delete_image(image["image_id"], image.get("file_name"))

    async def _delete(self, digest):
        entry = self.entries.pop(digest)
        self._save()
        await self._delete_image(entry["id"], entry["file"])

    async def _delete_image(self, image_id, name):
        try:
            await self.gw.delete_library_image(image_id)
        except Exception as e:
            _log.warning(f"[Stickers] 图片库里的旧图 {name} 没删掉: {e}")

    def _save(self):
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        temp = self.path + ".tmp"
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(self.entries, f, ensure_ascii=False, indent=1)
        os.replace(temp, self.path)


def import_pack(source, directory=STICKER_DIR, out=sys.stdout):
    """A seekchat sticker pack -- its .zip, or a folder with stickers.json -- into the sticker folder.
    seekchat labels are often just file names, so a sticker's desc becomes its label then."""
    if zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as pack:
            names = {os.path.basename(n): n for n in pack.namelist() if os.path.basename(n)}
            entries = json.loads(pack.read(names["stickers.json"]).decode("utf-8"))
            images = {name: pack.read(path) for name, path in names.items() if name.lower().endswith(FORMATS)}
        read = images.get
    else:
        with open(os.path.join(source, "stickers.json"), encoding="utf-8") as f:
            entries = json.load(f)

        def read(name):
            path = os.path.join(source, name)
            if not os.path.isfile(path):
                return None
            with open(path, "rb") as f:
                return f.read()
    os.makedirs(directory, exist_ok=True)
    manifest, taken = [], set()
    for entry in entries:
        file = os.path.basename(str(entry.get("file", "")))     # never a path out of the folder
        data = read(file) if file.lower().endswith(FORMATS) else None
        if data is None:
            print(f"skipped {file or entry}: no such image in the pack", file=out)
            continue
        label = str(entry.get("label") or "").strip()
        desc = str(entry.get("desc") or "").strip()
        if not label or label == os.path.splitext(file)[0]:
            label = desc
        label = re.sub(r"[\[\]【】\n]", "", label)[:MAX_LABEL] or os.path.splitext(file)[0][:MAX_LABEL]
        base, n = label, 2
        while label in taken:
            label, n = f"{base[:MAX_LABEL - len(str(n))]}{n}", n + 1
        taken.add(label)
        with open(os.path.join(directory, file), "wb") as f:
            f.write(data)
        manifest.append({"file": file, "label": label, "desc": desc or label})
    with open(os.path.join(directory, MANIFEST), "w", encoding="utf-8") as f:
        f.write(HEADER)
        yaml.safe_dump(manifest, f, allow_unicode=True, sort_keys=False)
    print(f"Imported {len(manifest)} stickers into {directory}", file=out)


def main(argv, directory=STICKER_DIR, out=sys.stdout):
    if argv[:1] == ["import"] and len(argv) == 2:
        import_pack(argv[1], directory, out)
    elif argv:
        print("Usage: python -m modules.ai.stickers [import PACK.zip|FOLDER]", file=out)
        return 2
    stickers, problems = load(directory)
    for sticker in stickers.values():
        print(f"[表情:{sticker.label}]  {os.path.basename(sticker.path)}  {sticker.desc}", file=out)
    for problem in problems:
        print(f"!! {problem}", file=out)
    print(f"{len(stickers)} stickers ready, {len(problems)} problems.", file=out)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
