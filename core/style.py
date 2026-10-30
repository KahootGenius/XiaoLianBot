"""Styles: every line the bot says, kept in one YAML file per character.

styles/小莲.yaml is the complete default. Any other file in styles/ --
styles/桃子.yaml, say -- may replace some or all of its lines. Whatever a
style leaves out, or writes with a placeholder the code never fills, falls
back to 小莲's line, so a half-finished style never breaks a reply.

Code asks for a line by key, e.g. t("manager.status.title", member=name), and
gets it in the style of the group it is talking to: StyleBook.scope(group)
sets that for everything said while handling one message or one group's
scheduled job.

Which style each group uses is set only in config.yaml on the machine running
the bot (`style` for every group, `group_styles` for exceptions), never from
chat. Changes apply on the next restart.

A line can also have versions for particular people, written next to it as
key@audience: help.admin@admin is what admins see in place of help.admin. The
scope says who is being answered; "admin" is built in, and config.yaml's
`audiences` names any others. A version a style leaves out comes from the
default, like any other line, and everyone else gets the line itself.

    python -m core.style check 桃子     # list problems in styles/桃子.yaml
"""
import contextlib
import contextvars
import os
import random
import string
import sys
import yaml
from botpy import logging
from core.config import LOCAL_DIR, load_config, root_path, use_local


_log = logging.get_logger()

STYLES_DIR = root_path("styles")
LOCAL_STYLES_DIR = os.path.join(LOCAL_DIR, "styles")
DEFAULT_STYLE = "小莲"
# The audience that needs no setup: whoever the group manager counts as an admin.
ADMIN = "admin"


class _Formatter(string.Formatter):
    """str.format limited to plain {names}, so a style file can't reach into objects."""

    def get_field(self, field_name, args, kwargs):
        if not field_name.isidentifier():
            raise ValueError(f"占位符只能是简单的名字：{{{field_name}}}")
        return super().get_field(field_name, args, kwargs)


_FORMATTER = _Formatter()


def placeholders(template):
    """The {names} a template uses. Raises ValueError on broken braces."""
    return {field.split("!")[0].split(":")[0]
            for _, field, _, _ in string.Formatter().parse(template) if field}


def _leaves(node, prefix=""):
    """(dotted key, value) for every line in a style tree; a list of variants is one line."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _leaves(value, f"{prefix}.{key}" if prefix else str(key))
    else:
        yield prefix, node


def split_version(key):
    """("help.admin", "admin") for "help.admin@admin"; (key, None) for a line itself."""
    head, dot, last = key.rpartition(".")
    if "@" not in last:
        return key, None
    line, audience = last.split("@", 1)
    return f"{head}{dot}{line}", audience


def _find(config, path):
    """What config.yaml has at a dotted path such as teams.captains, or None."""
    node = config
    for part in path.strip().split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _versions(key):
    """The keys to try for `key`, in order: its versions for whoever's being answered, then itself."""
    return [f"{key}@{audience}" for audience in _audiences.get()] + [key]


class Style:
    def __init__(self, file_name, data, base=None):
        self.file_name = file_name
        self.data = data or {}
        self.base = base            # the default style; None for the default itself
        self.name = str(self.data.get("name") or file_name)

    @property
    def system_prompt(self):
        for version in _versions("system_prompt"):
            value = self.lookup(version)
            if value is not None:
                return str(value).strip()
        return ""

    def own(self, key):
        """This file's own value for a dotted key, or None."""
        node = self.data
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node

    def lookup(self, key):
        value = self.own(key)
        if value is None and self.base is not None:
            return self.base.lookup(key)
        return value

    def _render(self, template, kwargs):
        return _FORMATTER.format(str(template), name=self.name, **kwargs)

    def say(self, key, **kwargs):
        for version in _versions(key):
            text = self._say(version, kwargs)
            if text is not None:
                return text
        raise KeyError(f"styles/{DEFAULT_STYLE}.yaml 缺少 {key}")

    def _say(self, key, kwargs):
        """One version of a line, or None when neither this style nor the default has it."""
        value = self.own(key)
        if isinstance(value, list):
            value = random.choice(value) if value else None
        if value is not None:
            try:
                return self._render(value, kwargs)
            except (KeyError, ValueError, IndexError) as e:
                if self.base is None:
                    raise
                _log.warning(f"[Style] {self.file_name}.yaml 的 {key} 用不了（{e}），改用{self.base.file_name}的说法")
        if self.base is None:
            return None
        # Fall back to the default's wording, still speaking as this style.
        fallback = self.base.lookup(key)
        if isinstance(fallback, list):
            fallback = random.choice(fallback) if fallback else None
        return None if fallback is None else self._render(fallback, kwargs)


def _read_yaml(path):
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} 的最外层应该是「键: 值」的形式")
    return data


class StyleBook:
    """All style files, plus which style each group uses."""

    def __init__(self, directory=STYLES_DIR, local_directory=None):
        self.directory = directory
        # Private styles (local/styles/) win over shared ones with the same name. A private
        # 小莲.yaml only needs the lines it changes: the rest come from the shared one.
        self.local_directory = local_directory or (LOCAL_STYLES_DIR if use_local() else None)
        self.shared_base = None
        self.default_name = DEFAULT_STYLE
        self.styles = {}
        self.groups = {}        # group_openid -> style name, from config.yaml's group_styles
        self.audiences = {}     # audience -> member_openids, from config.yaml's audiences
        self.configured = False
        self.reload()

    def configure(self, config):
        """Take the style choices from config.yaml: `style` for every group, `group_styles` for exceptions."""
        config = config or {}
        wanted = str(config.get("style") or DEFAULT_STYLE)
        if wanted not in self.styles:
            _log.warning(f"[Style] config.yaml 里的 style「{wanted}」在 styles/ 里找不到，改用{DEFAULT_STYLE}")
            wanted = DEFAULT_STYLE
        self.default_name = wanted
        per_group = config.get("group_styles") or {}
        if not isinstance(per_group, dict):
            _log.warning("[Style] config.yaml 的 group_styles 应该写成「群ID: 风格名」的形式，已忽略")
            per_group = {}
        self.groups = {}
        for group, name in per_group.items():
            if str(name) not in self.styles:
                _log.warning(f"[Style] config.yaml 的 group_styles 里，群 {group} 的风格「{name}」"
                             f"在 styles/ 里找不到，这个群改用默认风格")
                continue
            self.groups[str(group)] = str(name)
        self.audiences = self._read_audiences(config)
        self.configured = True
        for name in self.styles:
            for problem in self._audience_problems(name):
                _log.warning(f"[Style] {name}.yaml：{problem}")

    @staticmethod
    def _read_audiences(config):
        """config.yaml's audiences: {name: [member_openid, ...]}, the people some lines have versions for.
        In place of a list, a name can give where one is kept elsewhere in config.yaml, like teams.captains."""
        raw = config.get("audiences")
        if not raw:
            return {}
        if not isinstance(raw, dict):
            _log.warning("[Style] config.yaml 的 audiences 应该写成「名字: [成员ID, ...]」的形式，已忽略")
            return {}
        audiences = {}
        for name, members in raw.items():
            name = str(name).strip()
            if not name or any(c in name for c in ".@ ") or name == ADMIN:
                _log.warning(f"[Style] config.yaml 的 audiences 里的「{name}」不能用：名字里不能有 . @ 空格，"
                             f"{ADMIN} 是自带的（管理员），不用写")
                continue
            if isinstance(members, str):
                members = _find(config, members)
            if not isinstance(members, list):
                _log.warning(f"[Style] config.yaml 的 audiences 里，{name} 后面要写成员ID的列表，"
                             f"或者 config.yaml 里另一个列表的位置（比如 teams.captains），已忽略")
                continue
            audiences[name] = {str(m) for m in members}
        return audiences

    def audiences_of(self, member):
        """The audiences in config.yaml that list this member, in the order they're written."""
        return [name for name, members in self.audiences.items() if member in members]

    def reload(self):
        """Re-read every file in styles/. Returns {style: [problems]} for the ones with problems."""
        base = self.shared_base = Style(DEFAULT_STYLE,
                                        _read_yaml(os.path.join(self.directory, f"{DEFAULT_STYLE}.yaml")))
        files = {}          # style name -> path; private files replace shared ones
        for folder in (self.directory, self.local_directory):
            if folder and os.path.isdir(folder):
                for file in sorted(os.listdir(folder)):
                    stem, ext = os.path.splitext(file)
                    if ext in (".yaml", ".yml") and not stem.startswith("_") and (folder != self.directory
                                                                                  or stem != DEFAULT_STYLE):
                        files[stem] = os.path.join(folder, file)
        broken = {}
        if DEFAULT_STYLE in files:
            try:
                base = Style(DEFAULT_STYLE, _read_yaml(files.pop(DEFAULT_STYLE)), base=self.shared_base)
            except Exception as e:
                broken[DEFAULT_STYLE] = [f"local/styles/{DEFAULT_STYLE}.yaml 读不了，改用共享的版本：{e}"]
        styles = {DEFAULT_STYLE: base}
        for stem, path in files.items():
            try:
                styles[stem] = Style(stem, _read_yaml(path), base=base)
            except Exception as e:
                broken[stem] = [f"文件读不了：{e}"]
        self.styles = styles
        problems = {name: self.check(name) for name in styles}
        problems = {name: found for name, found in problems.items() if found}
        problems.update(broken)
        for name, found in problems.items():
            for problem in found:
                _log.warning(f"[Style] {name}.yaml：{problem}")
        return problems

    def names(self):
        return list(self.styles)

    def default(self):
        return self.styles.get(self.default_name) or self.styles[DEFAULT_STYLE]

    def for_group(self, group):
        return self.styles.get(self.groups.get(group)) or self.default()

    @contextlib.contextmanager
    def scope(self, group, audiences=()):
        """Everything said inside this block speaks in `group`'s style, to someone in `audiences`
        (most particular first): their versions of a line win over the line itself."""
        token = _current.set(self.for_group(group))
        audience_token = _audiences.set(tuple(audiences))
        try:
            yield
        finally:
            _audiences.reset(audience_token)
            _current.reset(token)

    def check(self, name):
        """Problems in one style file, judged against the shared default. Written for the file's editor."""
        style, base = self.styles[name], self.shared_base
        if style is base:
            return []
        problems = self._audience_problems(name) if self.configured else []
        for key, value in _leaves(style.data):
            if key == "name":
                continue
            line = split_version(key)[0]
            if base.own(line) is None:
                what = "这一项" if line == key else f" {line} 这一句"
                problems.append(f"{key}：{DEFAULT_STYLE}.yaml 里没有{what}，程序不会用到它（键名写错了？）")
                continue
            # Every version of a line is filled from the same call, so any of the default's names will do.
            allowed = {"name"}
            for base_value in self._versions_in(base, line):
                for variant in base_value if isinstance(base_value, list) else [base_value]:
                    allowed |= placeholders(str(variant))
            for variant in value if isinstance(value, list) else [value]:
                try:
                    unknown = placeholders(str(variant)) - allowed
                except ValueError as e:
                    problems.append(f"{key}：花括号写得不对（{e}），这句会改用{DEFAULT_STYLE}的说法")
                    continue
                if unknown:
                    names = "、".join("{" + u + "}" for u in sorted(unknown))
                    problems.append(f"{key}：程序不会填写 {names}，这句会改用{DEFAULT_STYLE}的说法"
                                    f"（这里能用的有：{'、'.join('{' + a + '}' for a in sorted(allowed))}）")
        return problems

    @staticmethod
    def _versions_in(style, line):
        """The style's own values for a line and for each of its key@audience versions."""
        head, _, last = line.rpartition(".")
        section = style.own(head) if head else style.data
        if not isinstance(section, dict):
            return []
        return [value for key, value in section.items() if key == last or str(key).startswith(f"{last}@")]

    def _audience_problems(self, name):
        """Versions in a style written for an audience nobody is in: they would never be said."""
        known = {ADMIN, *self.audiences}
        problems = []
        for key, _ in _leaves(self.styles[name].data):
            audience = split_version(key)[1]
            if audience is not None and audience not in known:
                problems.append(f"{key}：没有叫「{audience}」的群友类别，这句不会用到（现有：{'、'.join(sorted(known))}。"
                                f"{ADMIN} 是管理员，其他类别写在 config.yaml 的 audiences 里）")
        return problems

    def missing(self, name):
        """Keys the style leaves to the default -- not a problem, just not customised yet."""
        style, base = self.styles[name], self.shared_base
        return [] if style is base else [key for key, _ in _leaves(base.data) if style.own(key) is None]


_current = contextvars.ContextVar("style", default=None)
_audiences = contextvars.ContextVar("audiences", default=())
STYLES = StyleBook()


def current():
    return _current.get() or STYLES.default()


def t(key, **kwargs):
    """The line `key` in the current style, with its {placeholders} filled in."""
    return current().say(key, **kwargs)


def render(template, **kwargs):
    """Fill in a line kept outside the style files, such as an action's `say` in
    prompts/teams.yaml. {name} is the current style's name; a list means pick one at random."""
    if isinstance(template, (list, tuple)):
        template = random.choice(template)
    return current()._render(template, kwargs)


def join(items):
    """A list of names joined the current style's way (、 by default)."""
    return t("common.separator").join(items)


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "check":
        raise SystemExit("用法：python -m core.style check 风格名")
    name = sys.argv[2]
    if name not in STYLES.styles:
        raise SystemExit(f"styles/ 里没有 {name}.yaml。现有：{'、'.join(STYLES.names())}")
    # config.yaml's audiences, so versions written for anyone else are reported too.
    STYLES.audiences = StyleBook._read_audiences(load_config())
    STYLES.configured = True
    found = STYLES.check(name)
    print("\n".join(found) if found else f"{name}.yaml 没有问题。")
    left = STYLES.missing(name)
    if left:
        print(f"另有 {len(left)} 句没写，会沿用{DEFAULT_STYLE}的说法，例如：{'、'.join(left[:5])}")
