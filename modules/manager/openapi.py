"""Everything the group manager sends to, or asks of, QQ -- behind one small class.

botpy 1.2.1 predates the group-management APIs Tencent added on 2026-09-03
(member list, batch removal), and its error handling throws away the
platform's error code, which we need to tell "this bot isn't whitelisted yet"
(11253) apart from ordinary failures. So those calls go through `_call` here,
reusing botpy's HTTP session and access token. Tests replace this class with a fake.
"""
import asyncio
import itertools
from collections import OrderedDict
from botpy import logging
from botpy.http import Route


_log = logging.get_logger()

# 应用无接口访问权限: the endpoint is invite-only and this bot isn't on the list.
NO_PERMISSION = 11253
REMOVE_BATCH_SIZE = 20      # platform limit per batch_remove_members call
TRACE_HEADER = "X-Tps-trace-Id"


class OpenAPIError(Exception):
    def __init__(self, status, code, message, trace_id=None):
        super().__init__(f"HTTP {status} code={code} {message} (trace_id={trace_id})")
        self.status = status
        self.code = code
        self.message = message
        self.trace_id = trace_id

    @property
    def no_permission(self):
        return self.code == NO_PERMISSION


class QQGateway:
    def __init__(self, api):
        self.api = api
        # msg_id -> next msg_seq. QQ rejects a second reply with the same
        # (msg_id, msg_seq) pair as a duplicate (error 40054005).
        self._seq = OrderedDict()

    def _next_seq(self, msg_id):
        counter = self._seq.get(msg_id)
        if counter is None:
            counter = self._seq[msg_id] = itertools.count(1)
            while len(self._seq) > 500:
                self._seq.popitem(last=False)
        return next(counter)

    async def send(self, group_openid, content, reply_to=None):
        """Send text to a group, as a passive reply when `reply_to` is a message id.

        Passive replies only work within 5 minutes of the original message;
        past that (or on any failure) fall back to a proactive message.
        """
        if reply_to:
            try:
                return await self.api.post_group_message(
                    group_openid=group_openid, msg_type=0, content=content,
                    msg_id=reply_to, msg_seq=self._next_seq(reply_to))
            except Exception as e:
                _log.warning(f"[Manager] 被动回复失败，改为主动发送: {e}")
        return await self.api.post_group_message(group_openid=group_openid, msg_type=0, content=content)

    async def _call(self, method, path, body=None, query=None, **params):
        http = self.api._http
        await http.check_session()
        route = Route(method, path, **params)
        route.is_sandbox = http.is_sandbox
        async with http._session.request(method, route.url, headers=http._headers, json=body, params=query) as resp:
            trace_id = resp.headers.get(TRACE_HEADER)
            try:
                data = await resp.json(content_type=None)
            except Exception:
                data = await resp.text()
            if resp.status in (200, 202, 204):
                return data
            code = data.get("code", data.get("err_code")) if isinstance(data, dict) else None
            message = data.get("message", "") if isinstance(data, dict) else str(data)
            raise OpenAPIError(resp.status, code, message, trace_id)

    async def list_members(self, group_openid):
        """Every member of the group, following the cursor 30 at a time. Invite-only API."""
        members, cursor = [], ""
        while True:
            data = await self._call("GET", "/v2/groups/{group_openid}/members",
                                    query={"cursor": cursor} if cursor else None, group_openid=group_openid)
            members.extend(data.get("members") or [])
            cursor = data.get("next_cursor") or ""
            if not cursor:
                return members
            await asyncio.sleep(1.1)    # rate limit is 60 requests a minute

    async def remove_members(self, group_openid, member_openids):
        """Remove members in batches of 20. Never blacklists: removed members may re-apply.

        Raises OpenAPIError on failure. Invite-only API.
        """
        for i in range(0, len(member_openids), REMOVE_BATCH_SIZE):
            chunk = member_openids[i:i + REMOVE_BATCH_SIZE]
            await self._call("POST", "/v2/groups/{group_openid}/batch_remove_members",
                             body={"member_openids": chunk, "add_to_member_blacklist": False},
                             group_openid=group_openid)
            await asyncio.sleep(2.1)    # rate limit is 30 requests a minute

    async def probe(self, group_openid):
        """Find out which invite-only APIs this bot may use. Returns {"member_list": bool|None, "remove": bool|None}.

        Removal is probed with an empty list, which can't remove anyone: a bot
        without access gets 11253, one with access gets a validation error. If
        the platform ever validated before checking access this would report a
        false "available" -- harmless, since a real removal that hits 11253
        falls back to asking admins to remove by hand.
        """
        result = {}
        try:
            await self._call("GET", "/v2/groups/{group_openid}/members", group_openid=group_openid)
            result["member_list"] = True
        except OpenAPIError as e:
            result["member_list"] = False if e.no_permission else None
            _log.info(f"[Manager] 群成员列表接口探测: {e}")
        except Exception as e:
            result["member_list"] = None
            _log.warning(f"[Manager] 群成员列表接口探测失败: {e}")
        try:
            await self._call("POST", "/v2/groups/{group_openid}/batch_remove_members",
                             body={"member_openids": [], "add_to_member_blacklist": False},
                             group_openid=group_openid)
            result["remove"] = True
        except OpenAPIError as e:
            result["remove"] = not e.no_permission
            _log.info(f"[Manager] 移除成员接口探测: {e}")
        except Exception as e:
            result["remove"] = None
            _log.warning(f"[Manager] 移除成员接口探测失败: {e}")
        return result
