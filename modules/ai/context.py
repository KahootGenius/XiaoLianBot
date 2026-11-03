"""What a request to DeepSeek carries, adapted from seekchat's context.ts and prompt-envelope.ts.

Every request has two lanes before the chat itself:
1. instructions: the style's persona plus the group-chat rules;
2. evidence: the rolling summary and members' memories, introduced as data,
   not instructions, so nothing a member slipped into them can steer the bot.
Then as much recent chat as fits a token budget, each member's line labelled
with their nickname.
"""
import math
import re
from modules.ai.memory import first_json


# seekchat's 动态想象力: the pre-reply call names a register, which picks the reply's temperature,
# and a length, which picks the length line in prompts/chat.yaml. seekchat's 奔放 is 1.3; at 1.3
# deepseek-flash garbled about one reply in ten in rehearsals, at 1.15 none in twenty.
# ai_chat.temperatures overrides any of them.
REGISTERS = {"严谨": 0.6, "平衡": 1.0, "奔放": 1.15}
LENGTHS = {"短": "length_short", "中": "length_medium", "长": "length_long"}
# When the pre-reply call asks for a web search (modules/ai/search.py): how many days old pages may be.
FRESHNESS = {"今天": 1, "这周": 7, "最近": 90}
SEARCH_WORDS = 80   # characters; the API takes 128 with its operators
LORE_TURNS = 4      # lore keywords are looked for in the last few turns...
LORE_LIMIT = 3      # ...and at most this many entries ride along
CAREFUL_TURNS = 10  # of the chat are kept when real people are in a picture
DAY_EVENTS = 4      # her day: at most this many things happen in it (the prompt asks for three)...
DAY_TEXT = 40       # ...each at most this many characters
# A bracketed action in a reply, like "（歪了歪头）". Short, so a long aside isn't taken for one.
_ACTION = re.compile(r"[（(]([^（）()\n]{1,20})[）)]")
# CJK ideographs, kana and full-width forms each cost about a token.
_WIDE = re.compile(r"[⺀-鿿豈-﫿＀-￯]")


def estimate_tokens(text):
    """A rough token count that knows Chinese characters cost about twice what Latin letters do."""
    wide = len(_WIDE.findall(text))
    return math.ceil(0.35 * (len(text) - wide) + 0.7 * wide)


def turn_text(turn):
    """How a stored turn reads in the transcript: members' lines carry their nickname, and the
    group chat that didn't @ the bot (ai_chat.hear_all) is marked so."""
    if turn["role"] == "assistant":
        return turn["content"]
    heard = "addressed" in turn.keys() and not turn["addressed"]
    return f"{turn['name']}（没@你）：{turn['content']}" if heard else f"{turn['name']}：{turn['content']}"


def select_window(turns, budget):
    """The newest turns that fit `budget` estimated tokens, always at least the newest one."""
    window, used = [], 0
    for turn in reversed(turns):
        cost = estimate_tokens(turn_text(turn))
        if window and used + cost > budget:
            break
        window.append(turn)
        used += cost
    window.reverse()
    return window


def unsummarized(turns, window, up_to_id):
    """Turns that have scrolled out of the window but aren't in the summary yet, oldest first."""
    oldest = window[0]["id"] if window else math.inf
    return [turn for turn in turns if up_to_id < turn["id"] < oldest]


def recent_actions(window, replies=5, limit=8):
    """The bracketed actions in the bot's last few replies, newest first, each once. Listed in the
    request so the next reply picks a new one: models otherwise copy their own habits."""
    found = []
    for turn in reversed(window):
        if turn["role"] != "assistant":
            continue
        for action in _ACTION.findall(turn["content"]):
            if action.strip() and action.strip() not in found:
                found.append(action.strip())
        replies -= 1
        if replies == 0:
            break
    return found[:limit]


def parse_register(raw):
    """(register, length) from the pre-reply call's answer, each None when unreadable."""
    data = first_json(raw)
    register, length = str(data.get("register") or "").strip(), str(data.get("length") or "").strip()
    return (register if register in REGISTERS else None), (length if length in LENGTHS else None)


def parse_search(raw):
    """(words, days) when the pre-reply call's answer asks for a web search, else None."""
    data = first_json(raw)
    words = re.sub(r"\s+", " ", str(data.get("search") or "")).strip()
    if not words or words.lower() == "null":
        return None
    return words[:SEARCH_WORDS], FRESHNESS.get(str(data.get("fresh") or "").strip(), FRESHNESS["最近"])


def no_people(raw):
    """Whether the person check's answer says for sure that no real people are in the pictures."""
    return first_json(raw).get("people") == 0


def parse_characters(raw, known):
    """The gallery characters the character check's answer names, each once. Any other name is
    dropped, so she can't be handed a name that isn't in the gallery."""
    found = first_json(raw).get("characters")
    names = [name for name in found if isinstance(name, str) and name in known] if isinstance(found, list) else []
    return list(dict.fromkeys(names))


def with_sender(window, member, someone):
    """The chat when real people are in a picture: the sender's lines and her replies to them, so she
    can follow what they're talking about. Anyone else's stay out, even without a name: 「刚才说去
    拉琴的那位」 names them to everyone who was there."""
    window = window[-CAREFUL_TURNS:]
    others = {turn["name"] for turn in window if turn["role"] == "user" and turn["member_openid"] != member}
    kept = []
    for before, turn in zip([None] + window[:-1], window):
        if turn["role"] == "user" and turn["member_openid"] == member:
            kept.append(turn)
        elif turn["role"] == "assistant" and before is not None and before["member_openid"] == member:
            text = turn["content"]
            for name in sorted(others, key=len, reverse=True):
                text = text.replace(name, someone)
            kept.append(dict(turn, content=text))
    return kept


def parse_day(raw):
    """Her day from the planning call's answer: short lines like "上午：…", [] when unreadable.
    An event may also come as {"time": "上午", "event": "…"}."""
    events = first_json(raw).get("events")
    lines = []
    for event in events if isinstance(events, list) else []:
        if isinstance(event, dict):
            event = "：".join(str(event[key]).strip() for key in ("time", "event") if event.get(key))
        text = re.sub(r"\s+", " ", str(event or "")).strip()
        if text:
            lines.append(text[:DAY_TEXT])
    return lines[:DAY_EVENTS]


def parse_lore(entries):
    """[(keywords, text)] from a style's lore lines, each "关键词、关键词：内容". Others are skipped."""
    parsed = []
    for entry in entries if isinstance(entries, list) else []:
        entry = str(entry)
        cuts = [i for i in (entry.find("："), entry.find(":")) if i > 0]
        if not cuts:
            continue
        keys = [k.strip() for k in re.split(r"[、,，/|]", entry[:min(cuts)]) if k.strip()]
        text = entry[min(cuts) + 1:].strip()
        if keys and text:
            parsed.append((keys, text))
    return parsed


def lore_for(entries, window):
    """The lore whose keywords come up in the last few turns, in the style file's order."""
    recent = "\n".join(turn["content"] for turn in window[-LORE_TURNS:])
    return [text for keys, text in parse_lore(entries) if any(key in recent for key in keys)][:LORE_LIMIT]


def compose(instructions, evidence, policy, title):
    """(instruction text, evidence text or None). Empty sections are skipped; the policy
    explaining how to treat evidence is added only when there is some."""
    body = "\n\n".join(s.strip() for s in instructions if s and s.strip())
    facts = "\n\n".join(s.strip() for s in evidence if s and s.strip())
    if not facts:
        return body, None
    return f"{body}\n\n{policy.strip()}", f"{title}\n{facts}"


def with_pictures(text, pictures):
    """A member's message with pictures in it, as data URLs."""
    return [{"type": "text", "text": text}, *({"type": "image_url", "image_url": {"url": url}} for url in pictures)]


def compare_pictures(references, question, pictures):
    """The character check's message: `references` as (line naming the character, data URL), each
    picture after its line, then the question and the pictures to check. The references come first
    and never change, so DeepSeek's cache can reuse them."""
    parts = []
    for line, url in references:
        parts += with_pictures(line, [url])
    return parts + with_pictures(question, pictures)


def build_messages(instructions, evidence, window, pictures=(), hint=""):
    """The chat-completion messages. Consecutive turns from the same side are joined, since
    several members may speak before the bot answers; the transcript starts at a member's turn.
    `pictures` go with the last message, the one that brought them: DeepSeek only takes them
    in a member's message. `hint`, who the character check found, follows them there."""
    messages = [{"role": "system", "content": instructions}]
    if evidence:
        messages.append({"role": "system", "content": evidence})
    turns = list(window)
    while turns and turns[0]["role"] == "assistant":
        turns.pop(0)        # its question scrolled out of the window
    for turn in turns:
        role = "assistant" if turn["role"] == "assistant" else "user"
        if messages[-1]["role"] == role:
            messages[-1]["content"] += "\n" + turn_text(turn)
        else:
            messages.append({"role": role, "content": turn_text(turn)})
    if pictures:
        messages[-1]["content"] = with_pictures(messages[-1]["content"], pictures)
        if hint:
            messages[-1]["content"].append({"type": "text", "text": hint})
    return messages
