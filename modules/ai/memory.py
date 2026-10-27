"""Long-term memory about each member, modelled loosely on how people remember.

- Encoding is selective. After each reply a cheap call -- the observer -- picks out
  what the speaker said about themselves and scores how much each thing matters,
  1 to 10, as Generative Agents does (Park et al., 2023). Everyday trivia, below
  ENCODE_AT_LEAST, is never kept, the way an ordinary breakfast isn't; what matters
  emotionally is what consolidates (McGaugh, 2004).
- What's kept lands first as a recent note (kind "episode"): fast, specific and
  short-lived, the hippocampal side of complementary learning systems (McClelland,
  McNaughton & O'Reilly, 1995).
- Consolidation, like sleep, folds recent notes and older memories into a few
  general facts: the gist outlives the details (fuzzy-trace theory, Reyna &
  Brainerd, 1995; sleep builds schemata, Lewis & Durrant, 2011). It runs every
  night, or early once someone's recent notes add up to CONSOLIDATE_AT, like
  Generative Agents' reflection.
- Facts fade unless they come up again: a little every week (Ebbinghaus; Anderson
  & Schooler, 1991), and each time a topic returns it holds longer (the spacing
  effect). Facts that define a person (CORE) don't fade. A member keeps at most
  ai_chat.memories_per_member facts; the weakest go first.

Another cheap call still checks each new note against the exchange and drops
anything said as a joke, as banter or in a game. Memories belong to one member in
one group, so nothing said in one group reaches another, and a member can see and
delete their own with /我的记忆 and /忘记.

Pure logic here; storage is in store.py and the wiring in ai_chat.py.
"""
import json
import re


MAX_MEMORY_TEXT = 60        # characters per entry; longer facts are cut
MAX_IMPRESSION_TEXT = 50    # the bot's one-line impression of a member
MAX_ADDS_PER_TURN = 2
OBSERVER_TEXT_BUDGET = 600  # characters of each side of the exchange the observer sees
ENCODE_AT_LEAST = 4         # importance below this is never kept: everyday trivia
DEFAULT_IMPORTANCE = 5      # for a note the observer didn't score
CORE = 10                   # facts this important never fade: what defines a person
CONSOLIDATE_AT = 20         # recent notes worth this much in all are consolidated at once
WEEKLY_DECAY = 0.5          # strength a fact loses each week the topic doesn't come up
REPETITION_BONUS = 2        # strength it gains each time the topic comes up again...
REPETITIONS_COUNTED = 3     # ...counted up to this many times
FORGET_BELOW = 3            # a fact weaker than this is forgotten
WEEK = 7 * 86400

# A safety net under the observer's own instructions: never keep anything
# that looks like a phone, ID or account number, an email address, or a password.
_SENSITIVE = re.compile(r"\d{6,}|[\w.+-]+@[\w-]+\.[\w.]+|身份证|密码|银行卡|手机号|电话号")


def excerpt(text, budget=OBSERVER_TEXT_BUDGET):
    text = text.strip()
    if len(text) <= budget:
        return text
    half = (budget - 10) // 2
    return f"{text[:half]}\n…（中间省略）…\n{text[-half:]}"


def numbered(texts, empty):
    return "\n".join(f"{i}. {text}" for i, text in enumerate(texts, 1)) if texts else empty


def looks_sensitive(text):
    return bool(_SENSITIVE.search(text))


def first_json(raw):
    """The first {...} in a model's answer as a dict, or {} when there's none to read."""
    match = re.search(r"\{.*\}", raw or "", re.S)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def importance_of(value):
    """A 1-10 importance from whatever the model wrote, DEFAULT_IMPORTANCE when unreadable."""
    try:
        return max(1, min(10, int(round(float(value)))))
    except (TypeError, ValueError):
        return DEFAULT_IMPORTANCE


def strength(importance, repetitions, since, now):
    """How firmly a long-term fact holds at `now`: its importance, plus a bonus for each time the
    topic came up again, minus what it lost every week since it last did. Core facts don't fade."""
    if importance >= CORE:
        return float(importance)
    weeks = max(0, now - since) / WEEK
    return importance + REPETITION_BONUS * min(repetitions, REPETITIONS_COUNTED) - WEEKLY_DECAY * weeks


def fact_strength(row, now):
    """strength() of a stored fact."""
    return strength(row["importance"], row["reinforced"], row["reinforced_at"] or row["created_at"], now)


def _numbers(values, count):
    """The valid 1-based numbers among `values`, sorted, each once."""
    found = set()
    for n in values if isinstance(values, list) else []:
        try:
            n = int(n)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= count:
            found.add(n)
    return sorted(found)


def _notes(items, taken, limit):
    """[(text, importance)] from a list of {"text", "importance"} (or plain strings): long enough to
    matter, not sensitive, nothing in `taken`, at most `limit`."""
    notes = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, str):
            text, importance = item, DEFAULT_IMPORTANCE
        elif isinstance(item, dict) and isinstance(item.get("text"), str):
            text, importance = item["text"], importance_of(item.get("importance"))
        else:
            continue
        text = text.strip()[:MAX_MEMORY_TEXT]
        if (text and importance >= ENCODE_AT_LEAST and text not in taken
                and text not in [t for t, _ in notes] and not looks_sensitive(text)):
            notes.append((text, importance))
        if len(notes) >= limit:
            break
    return notes


def parse_observation(raw, known, max_adds=MAX_ADDS_PER_TURN):
    """(new notes [(text, importance)], numbers to reinforce, numbers to forget) from the observer.

    Tolerant: reads the first {...} in the text, and each field degrades to
    "nothing" on its own. `known` are the memory texts it was shown, numbered
    from 1. Notes that matter too little to keep are dropped here.
    """
    data = first_json(raw)
    notes = _notes(data.get("notes", data.get("facts")), known, max_adds)    # "facts": the older shape
    forget = _numbers(data.get("forget"), len(known))
    reinforce = [n for n in _numbers(data.get("reinforce"), len(known)) if n not in forget]
    return notes, reinforce, forget


def parse_consolidation(raw, count, limit):
    """(new facts [(text, importance)], rewrites [(n, text)], numbers to forget) from consolidation.
    `count` is how many long-term facts it was shown, `limit` how many it may hold."""
    data = first_json(raw)
    forget = _numbers(data.get("forget"), count)
    updates = []
    for item in data.get("update") if isinstance(data.get("update"), list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            continue
        n, text = _numbers([item.get("n")], count), item["text"].strip()[:MAX_MEMORY_TEXT]
        if n and n[0] not in forget and text and not looks_sensitive(text):
            updates.append((n[0], text))
    return _notes(data.get("add"), [], limit), updates, forget


def parse_impression(raw):
    """The observer's updated impression of the speaker, or None to keep the old one."""
    text = first_json(raw).get("impression")
    if not isinstance(text, str):
        return None
    text = text.strip()[:MAX_IMPRESSION_TEXT]
    return text if text and text.lower() not in ("null", "none") and not looks_sensitive(text) else None


def parse_check(raw, count):
    """1-based numbers of the candidate notes the check kept, out of `count`.
    Anything unreadable keeps none: better a missed note than a joke taken as fact."""
    keep = first_json(raw).get("keep")
    if not isinstance(keep, list):
        return set()
    numbers = set()
    for n in keep:
        try:
            n = int(n)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= count:
            numbers.add(n)
    return numbers


def evidence(title, members):
    """The memory block for a request: `members` is [(nickname, [entry texts])]."""
    blocks = [f"{name}：\n" + "\n".join(f"- {text}" for text in texts) for name, texts in members if texts]
    return f"{title}\n" + "\n".join(blocks) if blocks else ""


def impressions(title, members):
    """The impressions block: `members` is [(nickname, impression or None)]."""
    lines = [f"- {name}：{text}" for name, text in members if text]
    return f"{title}\n" + "\n".join(lines) if lines else ""
