"""海龟汤: lateral-thinking puzzles she hosts. The puzzles, and reading the host's verdicts.

A puzzle has a 汤面, the situation players are told, and a 汤底, the truth behind it. Players ask
yes-or-no questions (/问) and she answers only 是, 不是 or 无关; when they think they have it they
guess (/猜). The commands are in modules/fun/social/module.py. Judging a question or a guess is a
call to DeepSeek that holds the truth (prompts/chat.yaml, soup_question and soup_guess). What it says back is read here and nothing else of it is used: the
group only ever sees her fixed lines from the style, so a verdict can't leak the truth.

The puzzles are in prompts/soups.yaml (or a private local/prompts/soups.yaml in its place). One
written with `set: 名字` belongs to a set of its own, opened with /海龟汤名字; the rest are what
/海龟汤 alone opens:

    python -m modules.fun.soup              # check the file
"""
import re
import sys
from dataclasses import dataclass
import yaml
from core.config import content_path
from modules.ai.memory import first_json


CODE = re.compile(r"[A-Za-z0-9_]{1,40}")
SET = re.compile(r"[0-9A-Za-z\u4e00-\u9fff]{1,12}")       # a set's name is typed straight after /海龟汤
# What follows /海龟汤 to ask for a hint or to give up: a set can't be called any of these.
HINT_WORDS = ("提示", "线索")
END_WORDS = ("结束", "放弃", "揭晓", "答案", "汤底")
MAX_SURFACE = 400       # characters of a 汤面: it goes out as one message,
MAX_TRUTH = 400         # and so does the 汤底
MAX_POINTS = 4          # a guess has to cover every key point, so more than a few can't be met
MAX_HINTS = 5
# What the host says to a question, and where the style has her lines for it (social.soup.answers).
ANSWERS = {"是": "positive", "不是": "negative", "无关": "unrelated", "是也不是": "partly", "不能回答": "invalid"}
INVALID = "invalid"     # not a yes-or-no question: it isn't answered, kept or counted
RIGHT, CLOSE, WRONG = "right", "close", "wrong"     # what she makes of a guess


@dataclass(frozen=True)
class Puzzle:
    code: str
    title: str
    surface: str        # 汤面: what the players are told
    truth: str          # 汤底: what they have to work out
    points: tuple       # the facts a guess has to cover
    hints: tuple = ()   # given one at a time, in this order
    set: str = ""       # the set it belongs to, opened with /海龟汤<set>; "" is /海龟汤 itself


def puzzles_file():
    return content_path("prompts", "soups.yaml")


def _lines(value, most):
    """A list of one-line texts from a puzzle's points or hints, or None when it isn't one."""
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > most or not all(isinstance(v, str) and v.strip() for v in value):
        return None
    return tuple(" ".join(v.split()) for v in value)


def load(path=None):
    """({code: Puzzle}, [problems]) from the puzzle file. A broken puzzle is left out and named in
    the problems; no file means no puzzles."""
    path = path or puzzles_file()
    try:
        with open(path, encoding="utf-8") as f:
            entries = yaml.safe_load(f) or []
    except FileNotFoundError:
        return {}, []
    except yaml.YAMLError as e:
        return {}, [f"soups.yaml 写得不对：{e}"]
    if not isinstance(entries, list):
        return {}, ["soups.yaml 要写成列表，每道题以「- code:」开头"]
    puzzles, problems = {}, []
    for i, entry in enumerate(entries, 1):
        where = f"第 {i} 题"
        if not isinstance(entry, dict) or not all(isinstance(entry.get(k), str) and entry[k].strip()
                                                  for k in ("code", "title", "surface", "truth")):
            problems.append(f"{where}：code、title、surface、truth 都要写")
            continue
        code = entry["code"].strip()
        where = f"{where}（{code}）"
        surface, truth = " ".join(entry["surface"].split()), " ".join(entry["truth"].split())
        points, hints = _lines(entry.get("points"), MAX_POINTS), _lines(entry.get("hints"), MAX_HINTS)
        named = str(entry["set"]).strip() if entry.get("set") is not None else ""
        if "set" in entry and (not SET.fullmatch(named) or named in HINT_WORDS + END_WORDS):
            problems.append(f"{where}：set 是接在 /海龟汤 后面的名字，只能用汉字、英文和数字，最多 12 个字，"
                            f"也不能是{'、'.join(HINT_WORDS + END_WORDS)}")
        elif not CODE.fullmatch(code):
            problems.append(f"{where}：code 只能用英文、数字和下划线，最多 40 个字符")
        elif code in puzzles:
            problems.append(f"{where}：code 和前面的重复了")
        elif len(surface) > MAX_SURFACE or len(truth) > MAX_TRUTH:
            problems.append(f"{where}：surface 最多 {MAX_SURFACE} 个字，truth 最多 {MAX_TRUTH} 个字")
        elif not points:
            problems.append(f"{where}：points 要写 1 到 {MAX_POINTS} 条关键点，每条一句话")
        elif hints is None:
            problems.append(f"{where}：hints 要写成列表，最多 {MAX_HINTS} 条，每条一句话")
        else:
            puzzles[code] = Puzzle(code, " ".join(entry["title"].split()), surface, truth, points, hints, named)
    return puzzles, problems


def sets(puzzles):
    """The names of the sets the puzzles are in, in the file's order, without the unnamed one."""
    return list(dict.fromkeys(puzzle.set for puzzle in puzzles.values() if puzzle.set))


def of_set(puzzles, name=""):
    """{code: Puzzle} of one set. /海龟汤 alone opens the puzzles that are in none; in a file where
    every puzzle is in one, it opens any of them."""
    chosen = {code: puzzle for code, puzzle in puzzles.items() if puzzle.set == name}
    return chosen or ({} if name else dict(puzzles))


def pick(puzzles, played, rng):
    """The next puzzle for a group that has had `played` (codes, oldest first): one it hasn't had; or,
    once it has had them all, any but the one it had last."""
    fresh = [puzzle for code, puzzle in puzzles.items() if code not in played]
    if not fresh:
        fresh = [puzzle for code, puzzle in puzzles.items() if code != (played[-1] if played else None)]
    return rng.choice(fresh or list(puzzles.values()))


def parse_answer(raw):
    """(which of ANSWERS' lines she says to a question, whether it hit a key point), or None when the
    host's verdict can't be read. Only a 是 can hit a key point."""
    verdict = first_json(raw)
    answer = ANSWERS.get(str(verdict.get("answer") or "").strip().strip("「」。"))
    if answer is None:
        return None
    return answer, answer == "positive" and verdict.get("key") is True


def parse_guess(raw):
    """RIGHT, CLOSE or WRONG from the host's verdict on a guess, or None when it can't be read. It
    takes a plain true to be right: anything else there isn't."""
    verdict = first_json(raw)
    if "solved" not in verdict:
        return None
    if verdict["solved"] is True:
        return RIGHT
    return CLOSE if verdict.get("close") is True else WRONG


def main(out=sys.stdout):
    puzzles, problems = load()
    for puzzle in puzzles.values():
        print(f"{puzzle.code}  {puzzle.title}  关键点 {len(puzzle.points)} · 提示 {len(puzzle.hints)}"
              f"{'  /海龟汤' + puzzle.set if puzzle.set else ''}", file=out)
    for problem in problems:
        print(f"!! {problem}", file=out)
    print(f"{len(puzzles)} puzzles ready, {len(problems)} problems.", file=out)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
