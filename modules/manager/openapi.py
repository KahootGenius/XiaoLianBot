"""Everything the group manager sends to, or asks of, QQ -- behind one small class.

botpy 1.2.1 predates the group-management APIs Tencent added on 2026-09-03
(member list, batch removal), and its error handling throws away the
platform's error code, which we need to tell "this bot isn't whitelisted yet"
(11253) apart from ordinary failures. So those calls go through `_call` here,
reusing botpy's HTTP session and access token. So do images (stickers), whose
direct upload botpy doesn't know, the image library (素材管理, 2026-10-21) and
markdown. Tests replace this class with a fake.
"""
import asyncio
import base64
import hashlib
import itertools
import os
import time
from collections import OrderedDict
import aiohttp
from botpy import logging
from botpy.http import Route
from core.style import t


_log = logging.get_logger()

# 应用无接口访问权限: the endpoint is invite-only and this bot isn't on the list.
NO_PERMISSION = 11253
REMOVE_BATCH_SIZE = 20      # platform limit per batch_remove_members call
TRACE_HEADER = "X-Tps-trace-Id"
# After QQ refuses buttons (a custom layout needs Tencent to switch it on for the
# bot), replies go out as plain text only for this long before trying again.
BUTTON_RETRY_SECONDS = 6 * 3600
IMAGE = 1                   # file_type of an image upload
# Where QQ's API has lived since 2026-08-10. botpy still calls the old address, which serves the older
# APIs; newer ones (the image library, markdown's force_verify_image_resource) go here.
API_DOMAIN = "api.bot.qq.com"
LIBRARY_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
                 ".webp": "image/webp"}


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
    def __init__(self, api, buttons=True):
        self.api = api
        self.buttons = buttons              # config.yaml: buttons
        self.buttons_off_until = 0.0
        # msg_id -> next msg_seq. QQ rejects a second reply with the same
        # (msg_id, msg_seq) pair as a duplicate (error 40054005).
        self._seq = OrderedDict()
        # (group, file, size, mtime) -> (file_info, when it expires or 0 for never): an image is
        # uploaded once and reused until QQ says the upload has expired.
        self._uploads = {}

    def _next_seq(self, msg_id):
        counter = self._seq.get(msg_id)
        if counter is None:
            counter = self._seq[msg_id] = itertools.count(1)
            while len(self._seq) > 500:
                self._seq.popitem(last=False)
        return next(counter)

    async def send(self, group_openid, content, reply_to=None, buttons=None):
        """Send text to a group, as a passive reply when `reply_to` is a message id.

        Passive replies only work within 5 minutes of the original message;
        past that (or on any failure) fall back to a proactive message.
        `buttons` -- rows of (label, command) -- go under a reply; see send_buttons.
        """
        if reply_to:
            try:
                result = await self.api.post_group_message(
                    group_openid=group_openid, msg_type=0, content=content,
                    msg_id=reply_to, msg_seq=self._next_seq(reply_to))
                if buttons:
                    await self.send_buttons(group_openid, reply_to, buttons)
                return result
            except Exception as e:
                _log.warning(f"[Manager] 被动回复失败，改为主动发送: {e}")
        try:
            return await self.api.post_group_message(group_openid=group_openid, msg_type=0, content=content)
        except Exception as e:
            if reply_to:
                await self._tell_failed(group_openid, reply_to, e)
            raise

    async def _tell_failed(self, group_openid, reply_to, error):
        """Neither the reply nor a proactive message got through, usually because QQ refused
        the text itself. A short notice beats silence; the caller still gets the error to log."""
        _log.warning(f"[Manager] 回复没能发出，告诉发消息的人: {error}")
        try:
            await self.api.post_group_message(group_openid=group_openid, msg_type=0, content=t("common.send_failed"),
                                              msg_id=reply_to, msg_seq=self._next_seq(reply_to))
        except Exception as e:
            _log.warning(f"[Manager] 连提示也没发出去: {e}")

    async def send_image(self, group_openid, path, reply_to=None, url=None):
        """An image, as a passive reply when `reply_to` is a message id. It's uploaded straight
        from this machine; if QQ won't take it that way and `url` is given, QQ fetches it from
        there instead. Raises OpenAPIError when it can't be sent."""
        file_info = await self._upload_image(group_openid, path, url)
        body = {"msg_type": 7, "media": {"file_info": file_info}}
        if reply_to:
            body.update(msg_id=reply_to, msg_seq=self._next_seq(reply_to))
        return await self._call("POST", "/v2/groups/{group_openid}/messages", body=body, group_openid=group_openid)

    async def _upload_image(self, group_openid, path, url=None):
        stat = os.stat(path)
        key = (group_openid, path, stat.st_size, stat.st_mtime)
        cached = self._uploads.get(key)
        if cached and (cached[1] == 0 or cached[1] > time.time() + 60):
            return cached[0]
        try:
            with open(path, "rb") as f:
                data = base64.b64encode(f.read()).decode("ascii")
            result = await self._call("POST", "/v2/groups/{group_openid}/files", body={
                "file_type": IMAGE, "file_data": data, "file_name": os.path.basename(path), "srv_send_msg": False,
            }, group_openid=group_openid)
        except Exception as e:
            if not url:
                raise
            _log.warning(f"[Stickers] 直接上传没成功，改让 QQ 从链接取: {e}")
            result = await self._call("POST", "/v2/groups/{group_openid}/files", body={
                "file_type": IMAGE, "url": url, "srv_send_msg": False}, group_openid=group_openid)
        ttl = int(result.get("ttl") or 0)
        self._uploads[key] = (result["file_info"], 0 if ttl <= 0 else time.time() + ttl)
        return result["file_info"]

    async def send_markdown(self, group_openid, content, reply_to=None):
        """Markdown, as a passive reply when `reply_to` is a message id. If an image in it won't load,
        QQ refuses the whole message rather than sending it without (force_verify_image_resource),
        so the caller can fall back. Raises OpenAPIError."""
        body = {"msg_type": 2, "markdown": {"content": content, "force_verify_image_resource": True}}
        if reply_to:
            body.update(msg_id=reply_to, msg_seq=self._next_seq(reply_to))
        return await self._call("POST", "/v2/groups/{group_openid}/messages", body=body, domain=API_DOMAIN,
                                group_openid=group_openid)

    async def upload_library_image(self, path):
        """Put an image in the bot's image library (素材管理), where markdown can show it at any size.
        Returns (image_id, its qqbot-media:// link, how many seconds it lasts unsent). The library
        holds 100 images per bot; delete_library_image frees a place."""
        with open(path, "rb") as f:
            data = f.read()
        kind = LIBRARY_TYPES[os.path.splitext(path)[1].lower()]
        md5 = hashlib.md5(data).hexdigest()
        prepared = await self._call("POST", "/v2/media-images/prepare", domain=API_DOMAIN, body={
            "file_name": os.path.basename(path), "file_size": len(data), "content_type": kind,
            "md5": md5, "sha1": hashlib.sha1(data).hexdigest()})
        etag = await self._put(prepared["presigned_url"], data, kind)
        done = await self._call("POST", "/v2/media-images/complete", domain=API_DOMAIN,
                                body={"upload_id": prepared["upload_id"], "md5": etag or md5})
        return done["image_id"], done["url"], int(done.get("ttl") or 0)

    @staticmethod
    async def _put(url, data, kind):
        """The bytes go straight to Tencent's storage at a signed address, without the bot's token.
        Returns the ETag, which completes the upload."""
        async with aiohttp.ClientSession() as session:
            async with session.put(url, data=data, headers={"Content-Type": kind}) as resp:
                if resp.status not in (200, 204):
                    raise OpenAPIError(resp.status, None, (await resp.text())[:200])
                return resp.headers.get("ETag", "").strip('"')

    async def list_library_images(self):
        """Everything in the bot's image library, following the cursor."""
        images, cursor = [], ""
        while True:
            data = await self._call("GET", "/v2/media-images", query={"cursor": cursor} if cursor else None,
                                    domain=API_DOMAIN)
            images.extend(data.get("images") or [])
            cursor = data.get("next_cursor") or ""
            if not cursor:
                return images

    async def delete_library_image(self, image_id):
        await self._call("DELETE", "/v2/media-images/{image_id}/delete", domain=API_DOMAIN, image_id=image_id)

    @staticmethod
    def keyboard(rows):
        """QQ's custom keyboard for rows of (label, command) or (label, command, question). Tapping a
        button sends "@bot command" for the member; a command ending in a space is only typed in, for
        them to finish (e.g. "/抓 "). With a question (40 characters at most), QQ asks it first and
        the button is red, for the ones that change something for good."""
        buttons, n = [], itertools.count(1)
        for row in rows[:5]:
            line = []
            for label, command, *question in row[:5]:
                action = {"type": 2, "permission": {"type": 2}, "data": command.strip(),
                          "enter": not command.endswith(" "), "unsupport_tips": t("common.buttons_unsupported")}
                if question:
                    action["modal"] = {"content": question[0][:40]}
                line.append({"id": str(next(n)), "action": action,
                             "render_data": {"label": label, "visited_label": label, "style": 3 if question else 1}})
            buttons.append({"buttons": line})
        return {"content": {"rows": buttons}}

    async def send_buttons(self, group_openid, reply_to, rows):
        """Tap-to-send buttons, as a small Markdown message of their own under a reply.

        The reply itself has already gone out as plain text, so if QQ refuses the
        buttons -- custom layouts need Tencent to switch them on for the bot --
        nothing is lost: the bot stops trying for a while and keeps to text.
        """
        if not self.buttons or time.monotonic() < self.buttons_off_until:
            return
        try:
            await self.api.post_group_message(
                group_openid=group_openid, msg_type=2, markdown={"content": t("common.buttons_hint")},
                keyboard=self.keyboard(rows), msg_id=reply_to, msg_seq=self._next_seq(reply_to))
        except Exception as e:
            self.buttons_off_until = time.monotonic() + BUTTON_RETRY_SECONDS
            _log.info(f"[Buttons] QQ 没有接受按钮（{e}），{BUTTON_RETRY_SECONDS // 3600} 小时内只发文字")

    async def _call(self, method, path, body=None, query=None, domain=None, **params):
        http = self.api._http
        await http.check_session()
        route = Route(method, path, **params)
        route.is_sandbox = http.is_sandbox
        url = route.url.replace(Route.DOMAIN, domain, 1) if domain and not http.is_sandbox else route.url
        async with http._session.request(method, url, headers=http._headers, json=body, params=query) as resp:
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
