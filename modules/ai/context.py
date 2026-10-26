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


# A bracketed action in a reply, like "（歪了歪头）". Short, so a long aside isn't taken for one.
_ACTION = re.compile(r"[（(]([^（）()\n]{1,20})[）)]")
# CJK ideographs, kana and full-width forms each cost about a token.
_WIDE = re.compile(r"[⺀-鿿豈-﫿＀-￯]")


def estimate_tokens(text):
    """A rough token count that knows Chinese characters cost about twice what Latin letters do."""
    wide = len(_WIDE.findall(text))
    return math.ceil(0.35 * (len(text) - wide) + 0.7 * wide)


def turn_text(turn):
    """How a stored turn reads in the transcript: members' lines carry their nickname."""
    return turn["content"] if turn["role"] == "assistant" else f"{turn['name']}：{turn['content']}"


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


def compose(instructions, evidence, policy, title):
    """(instruction text, evidence text or None). Empty sections are skipped; the policy
    explaining how to treat evidence is added only when there is some."""
    body = "\n\n".join(s.strip() for s in instructions if s and s.strip())
    facts = "\n\n".join(s.strip() for s in evidence if s and s.strip())
    if not facts:
        return body, None
    return f"{body}\n\n{policy.strip()}", f"{title}\n{facts}"


def build_messages(instructions, evidence, window):
    """The chat-completion messages. Consecutive turns from the same side are joined, since
    several members may speak before the bot answers; the transcript starts at a member's turn."""
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
    return messages
