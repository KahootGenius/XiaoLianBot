"""Long-term memory about each member, adapted from seekchat's memory.ts and perception.ts.

After the bot replies, a second, cheap call -- the observer -- reads that one
exchange and picks out what the speaker said about themselves that's worth
keeping: their situation, plans, likes, promises. It can also mark entries
that are now outdated. Memories belong to one member in one group, so
nothing said in one group reaches another, and a member can see and delete
their own with /我的记忆 and /忘记.

Pure logic here; storage is in store.py and the wiring in ai_chat.py.
"""
import json
import re


MAX_MEMORY_TEXT = 60        # characters per entry; longer facts are cut
MAX_ADDS_PER_TURN = 2
OBSERVER_TEXT_BUDGET = 600  # characters of each side of the exchange the observer sees

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


def parse_observation(raw, known, max_adds=MAX_ADDS_PER_TURN):
    """(facts to add, 1-based numbers of entries to forget) from the observer's answer.

    Tolerant: reads the first {...} in the text, and each field degrades to
    "nothing" on its own. `known` are the entry texts the observer was shown.
    """
    facts, forget = [], []
    match = re.search(r"\{.*\}", raw or "", re.S)
    if not match:
        return facts, forget
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return facts, forget
    if not isinstance(data, dict):
        return facts, forget
    listed = lambda key: data.get(key) if isinstance(data.get(key), list) else []
    for fact in listed("facts"):
        if not isinstance(fact, str):
            continue
        fact = fact.strip()[:MAX_MEMORY_TEXT]
        if fact and fact not in known and fact not in facts and not looks_sensitive(fact):
            facts.append(fact)
        if len(facts) >= max_adds:
            break
    for n in listed("forget"):
        try:
            n = int(n)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= len(known) and n not in forget:
            forget.append(n)
    return facts, forget


def evidence(title, members):
    """The memory block for a request: `members` is [(nickname, [entry texts])]."""
    blocks = [f"{name}：\n" + "\n".join(f"- {text}" for text in texts) for name, texts in members if texts]
    return f"{title}\n" + "\n".join(blocks) if blocks else ""
