# 小莲 · QQ Group Bot

A QQ group bot built on the official `botpy` SDK, cloned from Castorice and reorganized. Its
default voice is **小莲**, the group's gentle, slightly scatterbrained little caretaker. Everything
the bot says lives in a style file, [`styles/小莲.yaml`](styles/小莲.yaml), so you can write other
characters, such as 桃子 or 葡萄, and pick one per group in `config.yaml`.

On top of Castorice's games and utilities, 小莲 enforces the group's inactivity rule so admins
don't have to:

> 连续30天无有效发言，且未请假/未处于豁免名单，将被移出群聊。有效发言指实质性讨论、提问、回答、分享，
> 不包括纯表情、签到、复制粘贴。第25天会@提醒一次；移出前不另行警告。因考试、工作、健康等原因可向管理员请假，
> 单次最长90天。被移出后可随时申请重新加入。

## Features

### 🧹 Group manager
- **Reads every message** in the managed group and judges 有效发言. Discussion, questions, answers,
  links and files count. Stickers, check-ins, 复读, 接龙 and chatting with the bot don't.
  The judgement is rule-based and deliberately lenient. Every threshold is in `config.yaml`.
- **Newcomers are welcomed** the first time they speak. QQ doesn't tell official bots when
  someone joins, so the first message is the earliest sign. During the first 30 days of counting,
  the greeting is worded so it also fits an old member who simply hadn't spoken yet.
- **Day 25:** one combined @ reminder. **Day 30**, and at least 5 days after the reminder: removal.
  If the bot was offline on someone's day 25, their removal waits until they've had the full notice.
- **Three modes**, switchable from chat with `/管理模式`:
  - 演练: reports only
  - 确认: an admin approves each day's list with one reply
  - 自动: removes automatically
- **Leave and exemptions:**
  - Members request leave themselves (`/请假 30 考试`, at most 90 days); admins are notified.
  - Admins grant or revoke leave and manage the exempt list.
  - A member who rejoins starts counting from scratch.
- **Safety:**
  - Starts counting only once every group message actually arrives. With @-messages alone,
    everyone would look silent. If that access later disappears, reminders and removals pause and
    admins are told.
  - Never removes the owner, admins, exempt members, or anyone on leave.
  - Removes at most 15 people a day.
  - Stops and alerts admins if a suspicious number of members are due at once.
  - Never blacklists.
- **Works with or without Tencent's invite-only APIs.** The member list and batch removal
  (`/v2/groups/{group_openid}/members`, `/batch_remove_members`) are 内邀 for now.
  - **Without them:** 小莲 tracks everyone who posts and hands admins a list to remove by hand. People
    who never post at all are invisible to her.
  - **With them:** she also tracks lurkers and removes members herself. She probes daily and starts
    using the APIs as soon as access is granted.

### 📋 Member table
While the bot runs, open **http://127.0.0.1:8765/** on the same machine. You'll see a read-only
table of every recorded member, with:
- their latest message: when it was sent, and whether it counted. If it didn't, the table says
  why (表情, 签到, 复读, 太短…)
- their latest valid message, with its 30-character snippet
- days without valid speech, and their status (已提醒, 请假, 豁免…)
- valid messages in the last 7 days, and in total

You can filter, search, sort by any column and download a CSV. The table refreshes every minute.

The page only answers this machine by default. Members' message text is never stored, so a
message that didn't count shows only its time and why.

**For other admins, in QQ.** `/成员表` gives the same table, squeezed to 8 members per phone
screen. You can add a filter or page number, for example `/成员表 快到期` or `/成员表 2`.
`/成员 昵称` shows one member's full record. Use them in the admins-only group (`report_group`):
once that group exists, these commands won't answer in the main group, so it never sees who's been
quiet.

**For other admins, in a browser.** Your Mac has to be reachable from the internet first. You can
do that with a tunnel app (cpolar or 花生壳 in mainland China; Cloudflare Tunnel or ngrok
elsewhere), or a private network every admin joins, such as Tailscale. Before you do either, set a
long random `manager.dashboard.token` and share the link with it, for example
`https://…/?token=…`. Without a token the page refuses anything that isn't really this machine,
tunnels included.

### 🎉 Fun
- `/我的状态`: activity card showing your last valid message, streak, weekly rank, achievements and
  days left.
- `/排行`: the week's (or `/排行 月` the month's) leaderboard.
- `/成就`: 11 badges, such as 七日之约, 热心解答, 夜猫子 and 好久不见. They're announced in the
  group, at most 3 per hour.
- **Weekly digest (群聊小报):** posted every Sunday at 21:00, with message counts, the busiest hour,
  top talkers, newcomers and new badges.

### 💬 AI chat
@小莲 to chat with her in character. This needs a DeepSeek key. The memory design follows
seekchat (`Desktop/ai`):
- **She remembers the conversation.**
  - In each group, everyone who @s her shares one thread. Each line carries the speaker's nickname,
    so she can follow a discussion between several people.
  - The newest chat that fits `ai_chat.context_tokens` goes with every request.
  - Older chat is folded into a rolling summary by a cheap second call.
- **She remembers people.** After each reply, another cheap call notes what the speaker said about
  themselves: their plans, situation and likes. She keeps at most 12 short entries per member per
  group. Members see theirs with `/我的记忆` and delete them with `/忘记 序号` or `/忘记 全部`.
- **She knows who's who.** `ai_chat.special_people` in `config.yaml` tells her facts about
  particular members, such as who built her. She recognises them by their ID, so someone who copies
  their nickname can't pass for them.
- **Nobody can plant instructions through memory.**
  - Summary and memories travel in a separate message marked as data, not instructions.
  - Only facts members state about themselves are kept, and nothing that looks like a phone number,
    ID, account or password.
- **Privacy:**
  - Only messages that @ her, and her replies, are stored, for 7 days.
  - Groups never share a thread or memories.
  - Admins can wipe a group's conversation with `/重置对话`. `/删除数据 @某人` also deletes that
    person's chat and memories.
- **Model:** `deepseek-flash`, with thinking off by default (`deepseek.model`, `deepseek.thinking`).
- **Prompts:** the persona is the style's `system_prompt`. The rules for group chat, summaries and
  memory notes are in [`prompts/chat.yaml`](prompts/chat.yaml).

### 🏳️ Team games (`/抓`)
For the group's games, where a few captains pick teams:
- **Captains grab members.** A captain sends `@小莲 /抓 @某人` (several @ at once is fine) to pull
  anyone who isn't on a team yet into theirs. Everyone is on at most one team.
- **Captains decide what happens next.** They act on their own members with `/踢出`, `/设为小队长`,
  `/撤销小队长`, `/死亡` and `/复活`. Grabbed members can't leave on their own.
- **Admins act as referees.** They can use any action on any team, dissolve a team
  (`/解散队伍 @队长`) or clear everything (`/清空队伍`).
- **Anyone can look.** `/队伍` lists today's teams, with icons for statuses (⭐小队长 💀死亡).
- **Teams last one game day.** They clear at 05:00, so a game running past midnight survives. They
  also survive a restart.
- **Captains are listed in `config.yaml`** (`teams.captains`). A captain who isn't leading a team
  today can be grabbed like anyone else.
- **Everything about the game is one YAML file,** [`prompts/teams.yaml`](prompts/teams.yaml):
  - the statuses a member can have
  - every captain action: its command, who may use it, who it applies to, and what the bot says
  - team size and reset time

  Adding a status such as 受伤 or an action such as /复活 takes a few lines and no code. An
  action can also roll the dice (`effect: roll`) for a random status or position.

### 🎲 Social
- **`/今日运势`:** one fortune per member per day. Every word, including the lists it draws
  from, is in the style file, so each style themes it its own way.
- **Birthdays:** members opt in with `/生日 10-25`. On the day, her reply to their first message
  wishes them a happy birthday. `/本月生日` lists the month's birthdays.
- **Polls:** `/投票 问题 选项1 选项2 …`, then `/投 A`; `/结束投票` closes it. There's one open poll
  per group, results show who voted for what, and polls close themselves after 7 days.
- **`/翻牌子`:** a joke lucky draw among the week's active members, weighted by how much they
  posted; once a day each. @ a few people to draw among them instead.
- **The weekly digest** ends with a short in-character comment written by DeepSeek from the
  digest's numbers.
- **Buttons:** `/帮助`, `/今日运势`, `/我的状态`, the team list and polls offer tap-to-send buttons.
  QQ shows them only once Tencent switches custom buttons on for the bot; until then replies
  are plain text.

### Inherited from Castorice
Epic and Steam free-game pushes, Minecraft server status, blackjack, dice, 发癫 quotes, and
sleep tracking. See [docs/TRANSLATION.md](docs/TRANSLATION.md) for every command.

## Project layout

```
main.py                 entry point: client, event routing, command chain
core/                   config loading; style.py loads the style files
modules/
  ai/                   AI chat: ai_chat.py runs it, context.py builds each request,
                        memory.py reads the memory notes, store.py keeps data/chat.db; /deepseek
  fun/                  blackjack, dice, 发癫, sleep
  info/                 Epic, Steam, Minecraft
  fun/teams/            the team game: rules.py reads prompts/teams.yaml, roster.py keeps
                        today's teams (data/teams.json), module.py runs the commands
  manager/              the group manager
    classifier.py       is this message 有效发言?
    rules.py            the 25/30-day rule as pure functions
    store.py            SQLite (data/manager.db)
    module.py           intake, commands, daily check, removal flow, digest
    dashboard.py        the member table web page
    events.py           teaches botpy about every-message events
    openapi.py          member-list / removal APIs, capability probing
  help.py
styles/小莲.yaml         every line the bot says (the complete default style)
styles/_示例.yaml        an example to start a new style from
prompts/achievements.yaml  the badges: add or change them without code
prompts/teams.yaml      the team game: statuses and captain actions, without code
prompts/chat.yaml       AI chat's rules around the persona: group chat, summary, memory notes
assets/quotes/          发癫 quote data
deploy/                 systemd services (bot, tunnel), update script, tunnel link and fixed address
docs/                   privacy policy, self-test report, command reference, customizing guide,
                        server guide (DEPLOY.md)
tests/                  unit tests and simulated month-long runs
```

## Setup

1. **Install**
   ```bash
   python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
   ```
   The `botpy` import comes from the PyPI package **`qq-botpy`**. Installing `botpy` gets you an
   unrelated package.

2. **Configure**
   ```bash
   cp config.yaml.example config.yaml
   ```
   Fill in `appid` and `secret`. This is the same bot as Castorice, so reuse its credentials.

3. **QQ Open Platform:** these steps happen on the platform, not in code.
   - In the console, under 群聊管理能力, tick the capabilities 小莲 may request, such as 移除群成员.
   - In the group itself, the **group owner** has to authorize the bot:
     - set the bot's message scope to **receive all messages**
     - allow proactive messages, so reminders and the digest can be posted
     - grant the management capabilities
   - Make the bot a group admin.
   - Bots made by individual developers can only join groups where their creator is the owner or an
     admin.

4. **Turn the manager on**
   - @小莲 `/群ID` in the group, and put the result in `manager.groups`.
   - Each admin sends @小莲 `/我的ID`; put the results in `manager.admins`.
   - Optionally add the bot to an admins-only group and put that group's ID in
     `manager.report_group`.
   - For team games, each captain sends @小莲 `/我的ID`; put the results in `teams.captains`.

5. **Run:** `python main.py`. Run only **one** process per AppID, so stop the old Castorice copy
   first.

6. **Go live gradually**
   - Stay in 演练 for 2–4 weeks and compare its reports with your own sense of who's active.
   - Try `/艾特测试`. If it shows a real @, set `mention_format: tag`.
   - Then switch with `/管理模式 确认`, and later `/管理模式 自动`.

### Keep it running
The bot only sees messages while it's online, so a machine that sleeps or switches off will miss
some. The 5-day notice protects members from wrongful removal: anyone wrongly reminded just has to
post. Still, an always-on host, such as a small Linux server, is better than a laptop.

[docs/DEPLOY.md](docs/DEPLOY.md) sets one up step by step:
- a systemd service ([deploy/xiaolian.service](deploy/xiaolian.service)) that starts the bot at boot
  and restarts it after a crash
- [deploy/update.sh](deploy/update.sh), which updates it with one command: pull from GitHub, run
  the tests, restart, and roll back if the tests fail
- a tunnel service ([deploy/xiaolian-tunnel.service](deploy/xiaolian-tunnel.service)) for other
  admins, and [deploy/tunnel-link.sh](deploy/tunnel-link.sh) to print its current link
- [deploy/tunnel-fixed.sh](deploy/tunnel-fixed.sh), which gives that link a fixed address on your
  own domain, so it stops changing

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -t .
```

The tests cover:
- the 有效发言 classifier, with real QQ markup
- the 25/30-day rule, including leave, late reminders and newcomers
- styles:
  - every key the code asks for exists, every placeholder in the default is one the code fills,
    and the default reproduces the original text
  - partial styles fall back to 小莲, and broken lines are caught
  - per-group styles come from `config.yaml` only
- the team game: game nights with grabs, actions, referees, the daily reset and restarts, plus
  every mistake `prompts/teams.yaml` can contain
- AI chat, against a fake DeepSeek: the shared labelled thread, memory notes and deletion, the
  rolling summary, retention, and deletion racing a summary in flight
- botpy event parsing
- full simulated months in each mode, with and without API access

## Customizing

[docs/CUSTOMIZING.md](docs/CUSTOMIZING.md) covers:
- **writing a style:** copy `styles/_示例.yaml`, change the lines you want, and let the rest fall
  back to 小莲
- **private versions** of styles and rules in `local/`, which never goes to GitHub
- **choosing styles per group** in `config.yaml`, and checking a file with
  `python -m core.style check 名字`
- **renaming commands**
- **adding achievements:** most new badges are a few lines in `prompts/achievements.yaml`
- **changing the team game:** new statuses and captain actions in `prompts/teams.yaml`
- **changing how AI chat remembers:** `prompts/chat.yaml` and the `ai_chat` settings

## Privacy

The manager stores activity metadata, not chat logs. Details and retention periods are in
[docs/PRIVACY_POLICY.md](docs/PRIVACY_POLICY.md). Update that file whenever behaviour changes: the
platform's reviewers compare it with what the bot actually does.

## A note on language
The bot serves a Chinese-speaking group, so commands, replies and prompts are in Simplified Chinese.
Code comments and docstrings are in English.

## License
[MIT](https://choosealicense.com/licenses/mit/)
