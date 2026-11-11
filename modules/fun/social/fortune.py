"""今日运势: one fortune per member per day, drawn from lists in the style file.

The draw is seeded with the group, member and date, so asking twice on the
same day gives the same answer, and tomorrow brings a new one. Every word
comes from the style (social.fortune.*), so each style can theme it. With AI
chat's fortune reading on, her comment takes in a few of the member's own
latest messages, chosen by pick_recent.
"""
import random


GOOD_PICKS = 2      # how many 宜 and 忌 items a fortune lists
RECENT_SHOWN = 10   # at most this many of a member's own messages go to the AI with their fortune
# Laughter and filler: a message with nothing else in it tells her nothing about their day.
FILLER = set("哈呵嘿嘻嗯啊哦噢喔哇额呃")


def draw(seed, levels, good, bad, comments):
    """(level, [宜 items], [忌 items], comment template) for `seed`. Pure."""
    rng = random.Random(seed)
    level = rng.choice(levels)
    picked_good = rng.sample(good, min(GOOD_PICKS, len(good)))
    rest = [item for item in bad if item not in picked_good]
    picked_bad = rng.sample(rest, min(GOOD_PICKS, len(rest)))
    return level, picked_good, picked_bad, rng.choice(comments)


def pick_recent(texts):
    """Which of a member's own latest messages, oldest first, the AI reads with their fortune: the
    latest RECENT_SHOWN, oldest first, leaving out commands (/今日运势 itself among them) and messages
    of nothing but laughter, numbers, punctuation or emoji. One said twice counts once, where it was
    said last. An empty list means no reading: they get the style's own comment. Pure."""
    picked = []
    for text in texts:
        text = text.strip()
        if text.startswith(("/", "／")) or not any(ch.isalpha() and ch not in FILLER for ch in text):
            continue
        if text in picked:
            picked.remove(text)
        picked.append(text)
    return picked[-RECENT_SHOWN:]
