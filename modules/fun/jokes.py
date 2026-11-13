"""讲笑话: the jokes she tells, and which one comes next.

The jokes are in prompts/jokes.yaml (or a private local/prompts/jokes.yaml in its place). Most are
a question and its answer: she asks, and the answer waits until someone asks 为什么. One written as
`text` is told whole. A joke with `day` belongs to one of the special days in occasions.yaml: it is
only told on that day, and that day it comes before the others. The commands are in
modules/fun/social/module.py; nothing about who asked is kept.

With AI chat on, some of her jokes are made up on the spot (config.yaml, ai_chat.fresh_jokes): a
few of the file's jokes go to DeepSeek as examples of the manner, and `fresh` reads what comes
back. One that can't be used just means a joke from the file.

    python -m modules.fun.jokes             # check the file
"""
import sys
from dataclasses import dataclass
import yaml
from core.config import content_path


MAX_LENGTH = 200        # characters of a question, an answer or a whole joke
FRESH_QUESTION = (5, 60)        # ...and of one she makes up: least and most, for the question
FRESH_ANSWER = (2, 80)          # and for its answer


@dataclass(frozen=True)
class Joke:
    question: str       # the setup; the whole joke when there's no answer
    answer: str = ""
    day: str = ""       # the special day it belongs to, if any


def jokes_file():
    return content_path("prompts", "jokes.yaml")


def _text(entry, key, where):
    value = entry.get(key)
    if value is None:
        return ""
    text = " ".join(str(value).split())
    if not text or len(text) > MAX_LENGTH:
        raise ValueError(f"{where}：{key} 不能是空的，也不能超过 {MAX_LENGTH} 个字")
    return text


def load(path=None):
    """([Joke], [problems]) from the joke file. A joke written wrong is left out and named in the
    problems; no file means no jokes."""
    path = path or jokes_file()
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return [], []
    except (OSError, yaml.YAMLError) as e:
        return [], [f"jokes.yaml 读不了：{e}"]
    entries = data.get("jokes") if isinstance(data, dict) else None
    if entries is None:
        return [], []
    if not isinstance(entries, list):
        return [], ["jokes.yaml 的 jokes 要写成列表，每个笑话以「- q:」或「- text:」开头"]
    found, problems = [], []
    for i, entry in enumerate(entries, 1):
        where = f"jokes.yaml 第 {i} 个笑话"
        try:
            if not isinstance(entry, dict):
                raise ValueError(f"{where}要写成 q 和 a（一问一答），或者 text（一句话讲完）")
            question, answer, whole = _text(entry, "q", where), _text(entry, "a", where), _text(entry, "text", where)
            if bool(question and answer) == bool(whole) or bool(question) != bool(answer):
                raise ValueError(f"{where}要写成 q 和 a（一问一答），或者 text（一句话讲完），只能写其中一种")
            joke = Joke(question or whole, answer, str(entry.get("day") or "").strip())
            if any(joke.question == other.question for other in found):
                raise ValueError(f"{where}和前面的重复了")
            found.append(joke)
        except ValueError as e:
            problems.append(str(e))
    return found, problems


def _same(text):
    """A question without its spaces and punctuation, to tell it from one already told."""
    return "".join(ch for ch in text if ch.isalnum())


def fresh(verdict, known):
    """The joke DeepSeek wrote, from the JSON it answered with ({"q": …, "a": …}), or None when it
    can't be told: not a question and its answer, too short or too long, more than one line, or
    the same question as one in `known`. Pure."""
    question, answer = verdict.get("q"), verdict.get("a")
    if not isinstance(question, str) or not isinstance(answer, str) or "\n" in question.strip() + answer.strip():
        return None
    question, answer = " ".join(question.split()), " ".join(answer.split())
    if not (FRESH_QUESTION[0] <= len(question) <= FRESH_QUESTION[1] and FRESH_ANSWER[0] <= len(answer) <= FRESH_ANSWER[1]):
        return None
    if not question.endswith(("？", "?")) or "@" in question + answer:
        return None
    if _same(question) in {_same(other) for other in known}:
        return None
    return Joke(question, answer)


class Teller:
    """Which joke each group hears next: every joke once, picked at random among those it hasn't
    heard this round, before any comes again."""

    def __init__(self, jokes, rng):
        self.jokes = tuple(jokes)
        self.rng = rng
        self.heard = {}     # group -> the jokes it has heard this round, as indexes
        self.last = {}      # group -> the one it heard last

    def next(self, group, days=()):
        """The group's next joke, or None when there's none to tell. `days` are the names of today's
        special days: a joke that belongs to one is told only then, and before the rest."""
        fits = [i for i, joke in enumerate(self.jokes) if not joke.day or joke.day in days]
        if not fits:
            return None
        heard = self.heard.setdefault(group, set())
        fresh = [i for i in fits if i not in heard]
        if not fresh:                       # a new round, which doesn't open with the one just told
            heard.clear()
            fresh = [i for i in fits if i != self.last.get(group)] or fits
        told = self.rng.choice([i for i in fresh if self.jokes[i].day] or fresh)
        heard.add(told)
        self.last[group] = told
        return self.jokes[told]


def main(out=sys.stdout, path=None, days=None):
    found, problems = load(path)
    for joke in found:
        print(f"{'【' + joke.day + '】' if joke.day else ''}{joke.question}{' —— ' + joke.answer if joke.answer else ''}", file=out)
    if days is not None:
        problems = problems + [f"「{joke.question[:12]}…」的 day「{joke.day}」不是 occasions.yaml 里的日子，这个笑话永远讲不到"
                               for joke in found if joke.day and joke.day not in days]
    for problem in problems:
        print(f"!! {problem}", file=out)
    print(f"{len(found)} jokes ready, {len(problems)} problems.", file=out)
    return 1 if problems else 0


if __name__ == "__main__":
    from core.occasions import load as load_days
    sys.exit(main(days={day.name for day in load_days()[0]}))
