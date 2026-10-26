"""Teach botpy 1.2.1 about every-message group events, and keep the fields it drops.

With "接收群内全部消息" switched on by the group owner, QQ sends
GROUP_MESSAGE_CREATE for every group message (same intent bit as @-messages,
1 << 25). botpy has no parser for it and logs "_parser unknown event". Its
GroupMessage also keeps only the sender's member_openid, while the platform
also sends their nickname, the bot flag, structured @mentions, message_type
(103 = quote reply) and attachment details -- all of which the manager needs.
"""
import re
from botpy.connection import ConnectionState
from botpy.message import GroupMessage


MENTION_MARK_RE = re.compile(r'<@!?([^>\s]+)>')


class RichGroupMessage(GroupMessage):
    """A GroupMessage that keeps the raw payload.

    `content` is rewritten so other members' @mentions read as "@昵称" and the
    bot's own mention disappears, which keeps the existing substring-matching
    command modules working unchanged. `raw_content` is what QQ actually sent.
    """
    __slots__ = ("raw", "event_type")

    # The bot's own ids (robot id and appid), filled in by main.py on_ready, so
    # its @mention can be recognised inside GROUP_MESSAGE_CREATE content.
    self_ids = set()
    # The bot's QQ name, also from on_ready. "@小蓮女神" typed or pasted as plain text,
    # not picked from QQ's member list, arrives as text rather than as a mention, and
    # only through GROUP_MESSAGE_CREATE. At the start of a message it still counts.
    self_names = set()

    def __init__(self, api, event_id, data, event_type):
        super().__init__(api, event_id, data)
        self.raw = data
        self.event_type = event_type
        self.content = self._readable_content()

    @property
    def author_raw(self):
        return self.raw.get("author") or {}

    @property
    def member_openid(self):
        return self.author_raw.get("member_openid")

    @property
    def nickname(self):
        return self.author_raw.get("username") or self.author_raw.get("nickname")

    @property
    def is_bot(self):
        return bool(self.author_raw.get("bot"))

    @property
    def role(self):
        # Not documented on message events; picked up if the platform sends it.
        return self.author_raw.get("member_role") or self.author_raw.get("role")

    @property
    def raw_content(self):
        return self.raw.get("content") or ""

    @property
    def message_type(self):
        return self.raw.get("message_type")

    @property
    def attachments_raw(self):
        return self.raw.get("attachments") or []

    @property
    def mention_list(self):
        return self.raw.get("mentions") or []

    def _mention_is_me(self, mention):
        if mention.get("is_you"):
            return True
        ids = {mention.get(k) for k in ("id", "member_openid", "user_openid")}
        return bool(ids & self.self_ids)

    def _after_typed_name(self, text):
        """`text` after a leading "@<bot name>" typed as plain text, or None if it doesn't start so."""
        text = text.lstrip()
        for name in self.self_names:
            if name and text.startswith(f"@{name}"):
                return text[len(name) + 1:].strip()
        return None

    @property
    def mentions_bot(self):
        if self.event_type == "GROUP_AT_MESSAGE_CREATE":
            return True
        if any(self._mention_is_me(m) for m in self.mention_list if m.get("scope") != "all"):
            return True
        if any(mark in self.self_ids for mark in MENTION_MARK_RE.findall(self.raw_content)):
            return True
        return self._after_typed_name(self.raw_content) is not None

    @property
    def mentioned_members(self):
        """[(member_openid, nickname)] of the people @-ed in this message, excluding the bot."""
        found = []
        for m in self.mention_list:
            if m.get("scope") == "all" or self._mention_is_me(m) or m.get("bot"):
                continue
            openid = m.get("member_openid") or m.get("id")
            if openid:
                found.append((openid, m.get("nickname") or m.get("username")))
        return found

    def _readable_content(self):
        names = {}
        for m in self.mention_list:
            for key in ("id", "member_openid", "user_openid"):
                if m.get(key):
                    names[m[key]] = None if self._mention_is_me(m) else (m.get("nickname") or m.get("username"))

        def replace(match):
            target = match.group(1)
            if target in self.self_ids or (target in names and names[target] is None):
                return ""
            return f"@{names.get(target) or '某人'}"

        text = MENTION_MARK_RE.sub(replace, self.raw_content).strip()
        after = self._after_typed_name(text)
        return text if after is None else after


def install():
    """Route both group message events through RichGroupMessage.

    botpy builds its event table from ConnectionState's parse_* methods when a
    client connects, so this must run before the client starts.
    """
    def parse_group_at_message_create(self, payload):
        message = RichGroupMessage(self.api, payload.get("id"), payload.get("d", {}), "GROUP_AT_MESSAGE_CREATE")
        self._dispatch("group_at_message_create", message)

    def parse_group_message_create(self, payload):
        message = RichGroupMessage(self.api, payload.get("id"), payload.get("d", {}), "GROUP_MESSAGE_CREATE")
        self._dispatch("group_message_create", message)

    ConnectionState.parse_group_at_message_create = parse_group_at_message_create
    ConnectionState.parse_group_message_create = parse_group_message_create
