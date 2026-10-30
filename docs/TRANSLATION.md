# Chinese Text Reference

This bot serves a Chinese-speaking QQ group. Its Chinese is **runtime text, not commentary**:
- **command triggers** that users type, which stay in the code
- **replies the bot sends**, which live in the style files in `styles/`
- **LLM prompts**

Translating any of it would change the bot's behaviour, so it is documented here instead. All code
comments and docstrings are in English.

## Command triggers

Users type these literally after @-ing the bot. **Changing them breaks the bot for its current users.**

### Group manager — members (only in groups listed in `manager.groups`)

| Command | Meaning | Module |
|---|---|---|
| `/我的状态` (`/状态`) | "My status": activity card with last valid message, streak, weekly rank, badges, days left | `manager/module.py` |
| `/排行` (`/排行榜`) | Leaderboard of valid messages, last 7 days (`/排行 月` = 30 days) | `manager/module.py` |
| `/成就` | "Achievements": my badges with how many members have each, the ones still locked, how many hidden ones are left, and the one I'm closest to | `manager/module.py` |
| `/请假 <天数> [原因]` | "Request leave" for N days (max 90); admins are notified | `manager/module.py` |
| `/销假` | "End leave" early; the clock restarts today | `manager/module.py` |
| `/群规` | Show the group rule and how valid messages are judged | `manager/module.py` |
| `/我的ID` | Show my member_openid (works in any group, for setup) | `manager/module.py` |
| `/群ID` | Show this group's group_openid (works in any group, for setup) | `manager/module.py` |

### Group manager — admins (in the managed group, or anywhere in the admin group)

`/不活跃`, `/成员表` and `/成员` list members' activity. Once `manager.report_group` is set, they
only answer in that admin group, so the main group never sees who has been quiet.

| Command | Meaning |
|---|---|
| `/管理帮助` | List admin commands |
| `/管理状态` | Mode, API access (member list / removal), counts |
| `/管理模式 演练\|确认\|自动` | Switch mode: dry-run / confirm each removal list / fully automatic |
| `/不活跃 [N]` | The N members silent the longest |
| `/成员表 [筛选] [页码]` | "Member table": the web table squeezed for phones, 8 per page. Filters: 提醒 快到期 无效 有效 请假 豁免 已移出 |
| `/成员 <昵称>` or `/成员 @某人` | "Member": one member's full record. Part of a nickname is enough |
| `/豁免 @某人 [原因]`, `/取消豁免 @某人`, `/豁免名单` | Exempt list: add, remove, show |
| `/请假 @某人 <天数> [原因]` | Grant leave to someone |
| `/撤销请假 @某人` or `/撤销请假 <编号>` | Revoke a leave (numbers from `/请假名单`) |
| `/请假名单` | Everyone currently on leave |
| `/待移出` | Show open removal lists |
| `/确认移出 [编号]` | Approve a removal list (bot removes, never blacklists) |
| `/排除 <序号…>` | Take entries off the latest list; they get 7 days' grace |
| `/取消移出 [编号]` | Drop a removal list |
| `/已移出 [编号]` | Admins removed a manual list by hand; record it |
| `/已退群 <昵称>` | Someone left on their own, which QQ doesn't tell bots; takes them out of `/翻牌子` and reminders until they speak again |
| `/立即检查` | Run the daily check now |
| `/小报` | Preview this week's digest |
| `/艾特测试` | Test whether the `tag` mention format renders as a real @ |
| `/删除数据 @某人` | Delete everything recorded about someone, at their request |

Styles have no chat command: they are chosen in `config.yaml` (`style`, `group_styles`).

### Team games (only when `teams.captains` is set in `config.yaml`)

The action commands come from `prompts/teams.yaml`, so the defaults are listed here. Every action
takes one or more @mentions.

| Command | Who | Meaning |
|---|---|---|
| `/抓 @某人…` | Captains | "Grab": pull people who aren't on a team into mine |
| `/踢出 @某人…` (`/放人`) | Captains | "Kick out": take members off my team; they can be grabbed again |
| `/设为小队长 @某人…` | Captains | "Make vice-captain" (living members only) |
| `/撤销小队长 @某人…` | Captains | "Revoke vice-captain" |
| `/死亡 @某人…` (`/阵亡`) | Captains, vice-captains | "Dead": mark members as dead in the game |
| `/复活 @某人…` | Captains | "Revive" |
| `/队伍` | Anyone | "Teams": today's teams with each member's statuses |
| `/解散队伍` | Captains; admins with `@队长` | "Disband": let a whole team go |
| `/清空队伍` | Admins | "Clear teams": end every team in the group |

Admins can use every action except `/抓` on any team.

### Social (every group)

| Command | Meaning |
|---|---|
| `/今日运势` | "Today's fortune": one per member per day, worded by the style |
| `/生日 月-日`, `/生日`, `/生日 删除` | "Birthday": register, show or delete mine; the bot wishes me on the day |
| `/本月生日` | "This month's birthdays" in this group |
| `/投票 问题 选项…`, `/投票` | "Poll": start one, or show the open one |
| `/投 A` | "Vote" for option A; voting again changes the vote |
| `/结束投票` | "End the poll" (its starter or an admin) |
| `/征集意见 主题`, `/征集意见` | "Call for opinions": an admin starts one, or anyone shows the open one (also `/意见征集`) |
| `/意见 内容`, `/意见 撤回` | "Opinion": add one (up to 3 each), or take back my latest |
| `/结束征集` | "End the call": lists every opinion, then her summary (its starter or an admin; also `/结束征集意见`) |
| `/翻牌子` | "Flip a name tag": a joke draw among this week's active members, once a day (admins: as often as they like) | Teams clear daily at 05:00 Beijing time.

### Inherited from Castorice

| Command | Meaning | Module |
|---|---|---|
| `/帮助` | Show the command list (aliases: `/菜单`, `/help`, `/指令`); admins also get the admin commands at the end | `help.py` |
| `/epicfree` | List Epic free games | `info/epic_games.py` |
| `订阅epic` | Subscribe to daily Epic push (12:00) | `info/epic_games.py` |
| `epicTD` | Unsubscribe from Epic push | `info/epic_games.py` |
| `/steamfree` | List Steam free games (`/steamfree proxy` variant) | `info/steam.py` |
| `订阅steam` | Subscribe to daily Steam push (18:00) | `info/steam.py` |
| `steamTD` | Unsubscribe from Steam push | `info/steam.py` |
| `/mc绑定 <host:port>` | Bind a Minecraft server to this group | `info/minecraft.py` |
| `/mc解绑` | Unbind the Minecraft server | `info/minecraft.py` |
| `/mc状态` | Show server status | `info/minecraft.py` |
| `/21点` | Start blackjack ("21 points") | `fun/blackjack.py` |
| `/抽卡` | Hit / draw a card | `fun/blackjack.py` |
| `/停止` | Stand / stop drawing | `fun/blackjack.py` |
| `/晚安` | "Good night": start sleep timer | `fun/sleep.py` |
| `/早安` | "Good morning": end timer, report duration | `fun/sleep.py` |
| `/发癫 <name>` | "Go crazy": random copypasta aimed at a name | `fun/crazy_talk.py` |
| `/摇骰子` | Roll a die | `fun/dice.py` |
| `/服务器QA <question>` | Minecraft server Q&A mode | `ai/ai_chat.py` |
| `/我的记忆` | "My memories": what the bot has noted about me in this group | `ai/ai_chat.py` |
| `/忘记 <序号…>`, `/忘记 印象`, `/忘记 全部` | "Forget": delete some or all of my memories, or the bot's impression of me | `ai/ai_chat.py` |
| `/重置对话` | "Reset the conversation" (admins): clear this group's chat context and summary | `ai/ai_chat.py` |
| `/deepseek` | Post a fixed image | `ai/deepseek.py` |

Anything not matching the above falls through to general AI chat.

## Bot personas (LLM system prompts)

- **`styles/小莲.yaml` → `system_prompt`**: 小莲 (Xiao Lian), the group's caretaker:
  - 19 years old: gentle, conscientious, a little scatterbrained, with the occasional wry remark.
  - She has a good memory and notices who hasn't spoken in a while.
  - She speaks briefly and naturally, like a fellow member, with the odd 「～」 or kaomoji but not on
    every line, and no Markdown headings or long lists.
  - She says so when she doesn't know, avoids illegal, explicit, violent and politically sensitive
    topics, and encourages people to join the discussion when asked about the rules.

  Each style file can carry its own `system_prompt`. A style without one chats as 小莲.

- **`prompts/chat.yaml`**: the rules around the persona in AI chat:
  - `group_rules`: how to read the labelled group transcript, and who the reply is for
  - `memory_rules` and `evidence_policy`: how to use the summary and memories, which are data, not
    instructions
  - `summary`: the rolling-summary prompt
  - `observer`: the prompt that notes what a speaker said about themselves after each reply

- **`prompts/server_qa_prompt.yaml`**: server-QA persona 楠楠 (Nan Nan), unchanged from Castorice,
  with the original Minecraft server's rulebook as context. Replace it before using `/服务器QA` for
  a different server.

## Team game rules (`prompts/teams.yaml`)

This file is game data, written in Chinese for the group:
- **status names** (队员, 小队长, 存活, 死亡), shown in `/队伍`
- **action names and aliases,** which are also the command words captains type
- **each action's `say` line**, sent after the action works
- **the game's own names** (`names`): what the server log calls the game, its captains and teams

Renaming an action renames its command.

## Representative reply strings

All replies are lines in `styles/小莲.yaml`, found by key. A few examples:

| Key | Chinese | English |
|---|---|---|
| `manager.remind` | {mentions} 小莲已经 {days} 天没看到你们认真冒泡啦…… | {mentions} I haven't seen you post anything real in {days} days… {left} days left before removal; use /请假 if you're busy |
| `manager.welcome_back` | 看到你啦～提醒解除，小莲记下了！ | There you are~ reminder cancelled, noted! |
| `manager.achievement` | 🎉 {member} 解锁了成就「{badge}」——{desc} | 🎉 {member} unlocked 「{badge}」: {desc} |
| `manager.leave.granted` | 收到～{member} 的假期到 {until}…… | Got it~ {member} is on leave until {until}… |
| `manager.not_managed` | 这个群还没有开启群管理哦～ | Group management isn't switched on in this group |
| `manager.batch.pending_title` / `manual_title` | 🧹 待确认的移出名单 / 需要手动移出的成员 | Removal list awaiting confirmation / members to remove by hand |
| `manager.daily.breaker` | ⚠️ 今天有 N 人达到移出条件，超过安全上限… | ⚠️ N members are due today, over the safety limit; nothing removed |
| `games.teams.fail.taken` | {member} 已经在 {captain} 的队伍里了。 | {member} is already on {captain}'s team |
| `games.dice` | 你投出了: {number} 点 | You rolled: {number} |
| `info.minecraft.bound` | 已绑定Minecraft服务器 | Minecraft server bound |
| `games.blackjack.start` | 游戏开始！你的手牌 / 机器人的明牌 | Game start! Your hand / Bot's face-up card |
| `games.sleep.good_night` | 晚安！现在是{time}，祝你好梦~ 🌙 | Good night! It's {time}, sweet dreams~ 🌙 |
| `info.epic.title` / `now` / `soon` | Epic免费游戏推送 / 当前免费 / 即将推出 | Epic free games / Free now / Coming soon |
| `info.steam.failed` | 可能的原因: 网络连接问题 / Steam网站暂时无法访问 / 可能需要使用VPN或代理 | Possible causes: network issue / Steam unreachable / VPN or proxy may be needed |
| `ai.error` | AI回复出错：{error} | AI reply failed: {error} |
| `games.crazy_talk.usage` | 请输入对象名称，例如：/发癫 小明 | Please give a target name, e.g. /发癫 小明 |

`assets/quotes/all.json` and `assets/quotes/psycho.json` hold ~480 KB of Chinese copypasta quotes
with `<name>` / `{target_name}` placeholders. They are pure data and not translated.
