"""A local, read-only web page: every recorded member and their latest message.

Served by the bot itself (aiohttp is already a botpy dependency) on
127.0.0.1 by default, so only the machine running the bot can open it.
Nicknames and snippets are text members typed themselves: the page inserts
them with textContent, never as HTML, and the CSV export defuses anything a
spreadsheet would run as a formula.
"""
import csv
import io
import ipaddress
import json
from datetime import datetime
from aiohttp import web
from botpy import logging


_log = logging.get_logger()

CSV_COLUMNS = (
    ("nickname", "昵称"), ("status", "状态"), ("days", "未有效发言天数"), ("latest_at_text", "最近一条消息"),
    ("latest_valid", "最近一条是否有效"), ("latest_kind", "判断"), ("last_valid_at_text", "最近有效发言"),
    ("valid_7d", "近7天有效"), ("valid_total", "累计有效"), ("first_seen_text", "首次出现"),
    ("snippet", "有效发言摘要"), ("member_openid", "member_openid"),
)

PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>成员活跃表</title>
<style>
:root { --bg:#f7f7f5; --fg:#1d1d1b; --muted:#77736d; --line:#e4e2dd; --card:#fff; --ok:#1f7a3f; --bad:#b42318; --warn:#a15c07; --accent:#0f6b80; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#191917; --fg:#f2f1ee; --muted:#a29e97; --line:#3d3b37; --card:#23221f; --ok:#5fd38d; --bad:#ff8a7a; --warn:#f2b84b; --accent:#3fb8d0; }
}
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:14px/1.5 -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; }
header { padding:20px 20px 4px; }
h1 { font-size:20px; margin:0 0 4px; }
.meta { color:var(--muted); font-size:12px; }
.bar { display:flex; flex-wrap:wrap; gap:8px; align-items:center; padding:10px 20px 0; }
.chip { border:1px solid var(--line); background:var(--card); color:var(--fg); border-radius:999px; padding:4px 12px; font:inherit; cursor:pointer; text-decoration:none; }
.chip.on { background:var(--accent); border-color:var(--accent); color:#fff; }
.count { color:var(--muted); margin-left:4px; }
.chip.on .count { color:rgba(255,255,255,.85); }
input[type=search], select { font:inherit; color:var(--fg); background:var(--card); border:1px solid var(--line); border-radius:8px; padding:5px 10px; }
input[type=search] { min-width:200px; }
.wrap { overflow-x:auto; margin:12px 20px 24px; border:1px solid var(--line); border-radius:10px; background:var(--card); }
table { border-collapse:collapse; width:100%; }
th, td { padding:8px 12px; border-bottom:1px solid var(--line); text-align:left; white-space:nowrap; vertical-align:top; }
tbody tr:last-child td { border-bottom:none; }
th { position:sticky; top:0; background:var(--card); font-weight:600; cursor:pointer; user-select:none; }
th.num { text-align:right; }
th.sorted::after { content:" ▾"; color:var(--muted); }
th.sorted.asc::after { content:" ▴"; }
td.snippet { white-space:normal; min-width:220px; max-width:360px; color:var(--muted); }
td.num { text-align:right; font-variant-numeric:tabular-nums; }
.yes { color:var(--ok); } .no { color:var(--bad); } .warn { color:var(--warn); } .muted { color:var(--muted); }
.empty { padding:32px; text-align:center; color:var(--muted); }
</style>
</head>
<body>
<header>
  <h1>成员活跃表</h1>
  <div class="meta" id="meta">加载中…</div>
</header>
<div class="bar" id="filters"></div>
<div class="bar">
  <input type="search" id="search" placeholder="搜索昵称或内容">
  <select id="group" hidden></select>
  <a class="chip" id="csv" href="#">下载 CSV</a>
</div>
<div class="wrap">
  <table><thead><tr id="head"></tr></thead><tbody id="body"></tbody></table>
  <div class="empty" id="empty" hidden>没有符合条件的成员</div>
</div>
<script>
const params = new URLSearchParams(location.search);
const state = { rows: [], sort: "days", asc: false, filter: "all", search: "", group: params.get("group") || "" };
// The long summary goes last so the numbers stay visible on narrow windows.
const COLUMNS = [
  ["nickname", "昵称"], ["status", "状态"], ["days", "未有效发言"], ["latest_at", "最近一条消息"],
  ["latest_valid", "是否有效"], ["last_valid_at", "最近有效发言"], ["valid_7d", "近 7 天有效"],
  ["valid_total", "累计有效"], ["first_seen", "首次出现"], ["snippet", "有效发言摘要"],
];
const NUMERIC = ["days", "valid_7d", "valid_total"];
const FILTERS = [
  ["all", "全部", () => true],
  ["valid", "最近一条有效", r => r.latest_valid === true],
  ["invalid", "最近一条无效", r => r.latest_valid === false],
  ["due", "超过 20 天", r => r.days !== null && r.days >= 20 && !["exempt", "leave"].includes(r.status_key)],
  ["reminded", "已提醒", r => r.status_key === "reminded"],
  ["leave", "请假中", r => r.status_key === "leave"],
  ["exempt", "豁免 / 管理员", r => r.status_key === "exempt"],
  ["gone", "已移出 / 离开", r => r.status_key === "gone"],
];

function url(path) {
  const p = new URLSearchParams();
  if (params.get("token")) p.set("token", params.get("token"));
  if (state.group) p.set("group", state.group);
  return path + "?" + p.toString();
}

function cell(text, className) {
  const td = document.createElement("td");
  td.textContent = text;
  if (className) td.className = className;
  return td;
}

const RENDER = {
  nickname: r => { const td = cell(r.nickname); td.title = r.member_openid; return td; },
  status: r => cell(r.status, { reminded: "warn", due: "no", gone: "muted" }[r.status_key] || ""),
  days: r => {
    // Members on leave or exempt aren't at risk, however long they've been quiet.
    const calm = r.days === null || ["exempt", "leave", "gone"].includes(r.status_key);
    const tone = calm ? "num muted" : r.days >= 25 ? "num no" : r.days >= 20 ? "num warn" : "num";
    return cell(r.days === null ? "—" : r.days + " 天", tone);
  },
  latest_at: r => cell(r.latest_at_text || "—"),
  latest_valid: r => r.latest_valid === null
    ? cell("—（超过 35 天）", "muted")
    : cell((r.latest_valid ? "✓ 有效" : "✗ 不算") + (r.latest_kind ? " · " + r.latest_kind : ""), r.latest_valid ? "yes" : "no"),
  last_valid_at: r => cell(r.last_valid_at_text || "还没有", r.last_valid_at_text ? "" : "muted"),
  valid_7d: r => cell(r.valid_7d, "num"),
  valid_total: r => cell(r.valid_total, "num"),
  first_seen: r => cell(r.first_seen_text),
  snippet: r => cell(r.snippet, "snippet"),
};

function renderRow(r) {
  const tr = document.createElement("tr");
  for (const [key] of COLUMNS) tr.appendChild(RENDER[key](r));
  return tr;
}

function compare(a, b) {
  const x = a[state.sort], y = b[state.sort];
  if (x === y) return 0;
  if (x === null || x === undefined || x === "") return 1;      // blanks always last
  if (y === null || y === undefined || y === "") return -1;
  const order = typeof x === "string" ? x.localeCompare(y, "zh-CN") : (x > y ? 1 : -1);
  return state.asc ? order : -order;
}

function render() {
  const head = document.getElementById("head");
  head.replaceChildren(...COLUMNS.map(([key, label]) => {
    const th = document.createElement("th");
    th.textContent = label;
    const classes = NUMERIC.includes(key) ? ["num"] : [];
    if (key === state.sort) classes.push("sorted", ...(state.asc ? ["asc"] : []));
    th.className = classes.join(" ");
    th.onclick = () => { state.asc = key === state.sort ? !state.asc : key === "nickname"; state.sort = key; render(); };
    return th;
  }));

  const filters = document.getElementById("filters");
  filters.replaceChildren(...FILTERS.map(([key, label, test]) => {
    const b = document.createElement("button");
    b.className = "chip" + (key === state.filter ? " on" : "");
    b.textContent = label;
    const n = document.createElement("span");
    n.className = "count";
    n.textContent = state.rows.filter(test).length;
    b.appendChild(n);
    b.onclick = () => { state.filter = key; render(); };
    return b;
  }));

  const test = FILTERS.find(f => f[0] === state.filter)[2];
  const q = state.search.trim().toLowerCase();
  const rows = state.rows
    .filter(test)
    .filter(r => !q || r.nickname.toLowerCase().includes(q) || r.snippet.toLowerCase().includes(q))
    .sort(compare);
  document.getElementById("body").replaceChildren(...rows.map(renderRow));
  document.getElementById("empty").hidden = rows.length > 0;
  document.getElementById("csv").href = url("members.csv");
}

async function load() {
  try {
    const res = await fetch(url("api/members"));
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    state.rows = data.rows;
    state.group = data.group;
    const select = document.getElementById("group");
    select.hidden = data.groups.length < 2;
    select.replaceChildren(...data.groups.map(g => {
      const o = document.createElement("option");
      o.value = g; o.textContent = "群 " + g.slice(0, 8) + "…"; o.selected = g === data.group;
      return o;
    }));
    document.getElementById("meta").textContent =
      `记录到 ${data.rows.length} 人 · 开始统计：${data.tracking_start || "尚未开始"} · 更新于 ${data.generated_at}（每分钟自动刷新）`;
    render();
  } catch (e) {
    document.getElementById("meta").textContent = "加载失败：" + e.message;
  }
}

document.getElementById("search").oninput = e => { state.search = e.target.value; render(); };
document.getElementById("group").onchange = e => { state.group = e.target.value; load(); };
load();
setInterval(load, 60000);
</script>
</body>
</html>
"""


LOCAL_NAMES = ("127.0.0.1", "localhost", "::1")
FORWARDING_HEADERS = ("X-Forwarded-For", "X-Forwarded-Host", "Forwarded", "X-Real-IP", "CF-Connecting-IP")


def _hostname(host_header):
    if host_header.startswith("["):
        return host_header[1:host_header.find("]")]
    return host_header.split(":")[0]


def _spreadsheet_safe(value):
    """Keep Excel/Numbers from running a nickname like "=HYPERLINK(...)" as a formula."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


class Dashboard:
    def __init__(self, manager, host="127.0.0.1", port=8765, token=""):
        self.manager = manager
        self.host = host
        self.port = port
        self.token = str(token or "")
        self.runner = None

    @classmethod
    def from_config(cls, manager, cfg):
        cfg = cfg or {}
        return cls(manager, host=cfg.get("host", "127.0.0.1"), port=int(cfg.get("port", 8765)),
                   token=cfg.get("token", ""))

    def app(self):
        app = web.Application()
        app.router.add_get("/", self.page)
        app.router.add_get("/api/members", self.members_json)
        app.router.add_get("/members.csv", self.members_csv)
        return app

    def _is_local(self):
        try:
            return ipaddress.ip_address(self.host).is_loopback
        except ValueError:
            return self.host == "localhost"

    async def start(self):
        """Start serving; returns the URL, or None if it refused to start."""
        if not self._is_local() and not self.token:
            _log.warning("[Dashboard] 成员活跃表要监听非本机地址时，必须在 config.yaml 里设置 token，已跳过启动。")
            return None
        self.runner = web.AppRunner(self.app(), access_log=None)
        await self.runner.setup()
        try:
            await web.TCPSite(self.runner, self.host, self.port).start()
        except OSError as e:
            _log.warning(f"[Dashboard] 端口 {self.port} 无法使用（{e}），成员活跃表未启动。")
            await self.runner.cleanup()
            self.runner = None
            return None
        shown_host = "127.0.0.1" if self.host in ("0.0.0.0", "::") else self.host
        url = f"http://{shown_host}:{self.port}/"
        # The token stays out of the log: logs get copied around, and update.sh prints them.
        _log.info(f"[Dashboard] 成员活跃表：{url}" + ("?token=…（token 见 config.yaml）" if self.token else ""))
        return url + (f"?token={self.token}" if self.token else "")

    def _check(self, request):
        if self.token:
            if request.query.get("token") != self.token:
                raise web.HTTPForbidden(text="需要正确的 token")
            return
        # No token: this machine only. Tunnel tools (cpolar, 花生壳, ngrok,
        # Cloudflare Tunnel...) also connect from 127.0.0.1, but they carry the
        # public host name or forwarding headers -- refuse those.
        forwarded = any(name in request.headers for name in FORWARDING_HEADERS)
        if forwarded or _hostname(request.host or "") not in LOCAL_NAMES:
            raise web.HTTPForbidden(text="要从别的电脑访问，请先在 config.yaml 的 manager.dashboard 里设置 token")

    def _group(self, request):
        groups = self.manager.settings.groups
        group = request.query.get("group") or (groups[0] if groups else "")
        if group not in groups:
            raise web.HTTPNotFound(text="这个群没有开启群管理")
        return group

    def _rows(self, group):
        # Status words and reasons in the table follow that group's style.
        with self.manager.styles.scope(group):
            return self.manager.member_table(group)

    async def page(self, request):
        self._check(request)
        return web.Response(text=PAGE, content_type="text/html")

    async def members_json(self, request):
        self._check(request)
        group = self._group(request)
        g = self.manager.store.group(group)
        tracking_start = g["tracking_start"] if g else None
        body = {
            "group": group,
            "groups": self.manager.settings.groups,
            "generated_at": self.manager.clock().astimezone(self.manager.tz).strftime("%m-%d %H:%M"),
            "tracking_start": self.manager._date(tracking_start) if tracking_start else None,
            "rows": self._rows(group),
        }
        return web.json_response(body, dumps=lambda o: json.dumps(o, ensure_ascii=False))

    async def members_csv(self, request):
        self._check(request)
        group = self._group(request)
        out = io.StringIO()
        out.write("﻿")     # BOM, so Excel opens the Chinese text as UTF-8
        writer = csv.writer(out)
        writer.writerow([label for _, label in CSV_COLUMNS])
        for row in self._rows(group):
            values = dict(row, latest_valid={True: "有效", False: "不算", None: ""}[row["latest_valid"]])
            writer.writerow([_spreadsheet_safe(values[key]) for key, _ in CSV_COLUMNS])
        filename = f"members-{datetime.now().strftime('%Y%m%d-%H%M')}.csv"
        return web.Response(text=out.getvalue(), content_type="text/csv", charset="utf-8",
                            headers={"Content-Disposition": f'attachment; filename="{filename}"'})
