"""Stickers for AI chat: images she can send under a reply by writing [表情:标签].

As in seekchat: a manifest gives each sticker a short label and when to use it,
her prompt lists them, and a marker in her reply becomes the image, sent right
under the text. Stickers are private, in local/stickers/, never on GitHub:

    local/stickers/stickers.yaml        - file: happy.png
                                          label: 开心
                                          desc: 开心、得意的时候
    local/stickers/happy.png

    python -m modules.ai.stickers                          # check the folder
    python -m modules.ai.stickers import stickers.zip      # bring in a seekchat sticker pack

Uploading to QQ is QQGateway.send_image's job.
"""
import json
import os
import re
import sys
import zipfile
from dataclasses import dataclass
import yaml
from core.config import LOCAL_DIR, use_local


STICKER_DIR = os.path.join(LOCAL_DIR, "stickers")
MANIFEST = "stickers.yaml"
FORMATS = (".png", ".jpg", ".jpeg", ".gif")
MAX_BYTES = 5 * 1024 * 1024     # the most QQ takes in a direct upload
MAX_LABEL = 20
MAX_PER_REPLY = 1               # a group chat, not a sticker war

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
