"""今日运势: one fortune per member per day, drawn from lists in the style file.

The draw is seeded with the group, member and date, so asking twice on the
same day gives the same answer, and tomorrow brings a new one. Every word
comes from the style (social.fortune.*), so each style can theme it.
"""
import random


GOOD_PICKS = 2      # how many 宜 and 忌 items a fortune lists


def draw(seed, levels, good, bad, comments):
    """(level, [宜 items], [忌 items], comment template) for `seed`. Pure."""
    rng = random.Random(seed)
    level = rng.choice(levels)
    picked_good = rng.sample(good, min(GOOD_PICKS, len(good)))
    rest = [item for item in bad if item not in picked_good]
    picked_bad = rng.sample(rest, min(GOOD_PICKS, len(rest)))
    return level, picked_good, picked_bad, rng.choice(comments)
