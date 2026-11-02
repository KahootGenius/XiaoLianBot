"""Reference pictures of characters, so she can name the ones the group posts.

On fan art DeepSeek named 1 newer character in 34, and its guesses were wrong with confidence; told
not to guess, it refused even ones it knew. With a labelled picture of each character in the request
it got 31 of 40, with 1 wrong name. So a character's name only ever comes from here, and anything
else is dropped.

The pictures are private, in local/characters/, never on GitHub. One clear, full-body picture of each
character, named after them. Art full of effects, where the character is small, does worse: 27 of 44
against 34, and it mixed up 遐蝶 and 昔涟 by their colours:

    local/characters/遐蝶.jpg
    local/characters/芙宁娜.png

    python -m modules.ai.gallery        # check the folder

One each, because a second did no good: 15 right of 20 with 3 wrong names. Keep their size, too: at
512 px it got 12 of 20 with 4 wrong. They all go to DeepSeek with every picture being checked, each
adding about 500 tokens, so have only the characters the group really posts: about 20 worked, and 60
was slow and worse. DeepSeek's cache keeps it cheap: a request that starts the same costs a fiftieth
as much.

A short note on a character's looks can go in notes.yaml in the same folder. It goes in the line before
their picture, and helps tell apart characters with similar colours: on 71 clear pictures it took 66
right with 2 wrong names to 68 with none, for about 10 tokens each:

    花火: 黑色双马尾，红衣，狐狸面具
"""
import base64
import os
import sys
import yaml
from botpy import logging
from core.config import LOCAL_DIR, use_local
from modules.ai import vision


_log = logging.get_logger()

GALLERY_DIR = os.path.join(LOCAL_DIR, "characters")
NOTES_FILE = "notes.yaml"
FORMATS = (".png", ".jpg", ".jpeg", ".gif", ".webp")
MAX_BYTES = 500 * 1024          # each one travels with every picture checked
MAX_PICTURES = 30               # at 60 it got 11 of 18, and each check took about 47 seconds
CHECK_SECONDS = 15              # how long a check may hold up her reply: it can be slow, a name isn't vital
MAX_NAME = 30
MAX_NOTE = 40                   # a few words are enough, and each travels with every picture checked


def read_notes(directory, characters):
    """({character: note}, [problems]) from the folder's notes file, if it has one. A bad note is left
    out and named in the problems. A broken file costs the notes, not the pictures."""
    path = os.path.join(directory, NOTES_FILE)
    if not os.path.isfile(path):
        return {}, []
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (yaml.YAMLError, UnicodeDecodeError) as e:
        return {}, [f"{NOTES_FILE}：读不出来（{type(e).__name__}）"]
    if not isinstance(data, dict):
        return {}, [f"{NOTES_FILE}：应该一行写一个「角色: 备注」"]
    notes, problems = {}, []
    for character, note in data.items():
        if character not in characters:
            problems.append(f"{NOTES_FILE}：「{character}」没有参考图，名字要和文件名一样")
        elif not isinstance(note, str) or not 0 < len(note.strip()) <= MAX_NOTE:
            problems.append(f"{NOTES_FILE}：「{character}」的备注应该是一句不超过 {MAX_NOTE} 个字的话")
        else:
            notes[character] = note.strip()
    return notes, problems


def load(directory=None):
    """([(character, data URL, note)], [problems]) from a gallery folder, with "" for a character
    without a note. Sorted, so that requests start the same and DeepSeek's cache can reuse them. No
    folder means no gallery. A bad file is left out and named in the problems."""
    if directory is None:
        if not use_local():
            return [], []
        directory = GALLERY_DIR
    if not os.path.isdir(directory):
        return [], []
    references, problems = [], []
    for file in sorted(os.listdir(directory)):
        path = os.path.join(directory, file)
        if file.startswith(".") or file == NOTES_FILE or not os.path.isfile(path):
            continue
        stem, extension = os.path.splitext(file)
        name = stem.strip()
        if extension.lower() not in FORMATS:
            problems.append(f"{file}：只支持 {'、'.join(FORMATS)}")
        elif not name or len(name) > MAX_NAME:
            problems.append(f"{file}：文件名是角色的名字，不超过 {MAX_NAME} 个字")
        elif os.path.getsize(path) > MAX_BYTES:
            problems.append(f"{file}：超过 {MAX_BYTES // 1024} KB，缩小一点：sips -Z 1000 \"{file}\"")
        elif len(references) >= MAX_PICTURES:
            problems.append(f"{file}：最多 {MAX_PICTURES} 张，这张没用上")
        else:
            with open(path, "rb") as f:
                data = f.read()
            kind = vision.image_format(data)
            if kind is None:
                problems.append(f"{file}：读不出来，不像是图片")
            else:
                references.append((name, vision.data_url(data, kind)))
    notes, trouble = read_notes(directory, {name for name, _ in references})
    return [(name, url, notes.get(name, "")) for name, url in references], problems + trouble


def main(argv, directory=GALLERY_DIR, out=sys.stdout):
    if argv:
        print("Usage: python -m modules.ai.gallery", file=out)
        return 2
    references, problems = load(directory)
    size = 0
    for name, url, note in references:
        bytes_ = len(base64.b64decode(url.split(",", 1)[1]))
        size += bytes_
        print(f"{name}  {bytes_ // 1024} KB  {note}".rstrip(), file=out)
    for problem in problems:
        print(f"!! {problem}", file=out)
    print(f"{len(references)} characters, {size // 1024} KB in all, {len(problems)} problems.", file=out)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
