# Customizing the bot: styles, commands, achievements, the team game and AI chat

Every line the bot sends lives in a **style file** in `styles/`, not in the code. That covers:
- command replies, status cards, reminders, removal lists and the weekly digest
- games, Epic/Steam, Minecraft and `/帮助`
- the AI chat persona

Changing how the bot talks means editing YAML, and you can keep several characters side by side.

| File | What it is |
|---|---|
| `styles/小莲.yaml` | The default style. It is complete, with about 275 lines, and is the reference for every key. Don't delete lines from it |
| `styles/_示例.yaml` | A short example style (桃子) to copy from. Files starting with `_` aren't loaded |
| `styles/<名字>.yaml` | Your own styles, such as `桃子.yaml` or `葡萄.yaml`. The file name is the style's name |
| `prompts/achievements.yaml` | The badges (see "Adding achievements") |
| `prompts/teams.yaml` | The team game's statuses and captain actions (see "Changing the team game") |
| `prompts/chat.yaml` | AI chat's rules around the persona: group chat, summaries, memory notes (see "Changing how AI chat remembers") |
| `prompts/rehearsal.yaml` | A scripted group chat for trying a persona against the real DeepSeek (see "Trying changes before the group sees them") |
| `config.yaml` | `style:` and `group_styles:` pick the styles; `manager.rules_text` overrides the rule text in `/群规` |

## Private versions: `local/`

Anything in `styles/` or `prompts/` can have a private version that never goes to GitHub. Put it in
`local/` under the same path, and the bot uses it instead of the shared one:

| Shared (on GitHub) | Your private version |
|---|---|
| `styles/小莲.yaml` | `local/styles/小莲.yaml` |
| `styles/桃子.yaml`, or a style only you have | `local/styles/桃子.yaml` |
| `prompts/teams.yaml` | `local/prompts/teams.yaml` |
| `prompts/achievements.yaml` | `local/prompts/achievements.yaml` |
| `prompts/chat.yaml`, `prompts/server_qa_prompt.yaml`, `prompts/rehearsal.yaml` | `local/prompts/…` |

- **A private `小莲.yaml` only needs the lines you change.** Every line it leaves out, or writes
  with a mistake, comes from the shared `styles/小莲.yaml`. Other styles fall back to your private
  wording.
- **A private `teams.yaml` or `achievements.yaml` replaces the shared one completely,** so start from
  a copy of it.
- **Restart the bot after changing anything in `local/`.** Check a private style with
  `python -m core.style check 小莲`. A mistake in a private rules file stops the bot at startup and
  names the problem.
- **The tests always check the shared files,** so collaborators' changes are tested the same way on
  every machine.
- **`local/` is in `.gitignore`.** Copy it to the server yourself, together with `config.yaml` and
  `data/` (see [DEPLOY.md](DEPLOY.md), step 7).

## How styles work

- **A style only needs the lines it changes.** Keep the same nesting as `小莲.yaml` and write what
  you want different. Every line you leave out is taken from 小莲.
- **Broken lines fall back to 小莲.** If a line uses a placeholder the code never fills (such as
  `{mystery}`), or its braces are wrong, the bot uses 小莲's version of that line rather than send
  something broken. The fallback still speaks with the style's own name.
- **Styles are chosen in `config.yaml` only.** There is no chat command for it, so nobody in a
  group can change how the bot talks. `style:` sets every group's style, and `group_styles:` gives
  particular groups a different one:
  ```yaml
  style: 小莲
  group_styles:
    "GROUP_OPENID_1": 桃子    # 测试群一
  ```
  A group's ID is what `@机器人 /群ID` shows in that group. **Restart the bot** after changing either
  setting or editing a style file. At startup, the log lists each group's style and warns about any
  style name it can't find; that group then uses the default.
- **The style follows the chat it's talking to.** Replies, reminders, the weekly digest, scheduled
  Epic and Steam pushes, and the status words on the web member table all use the style of the
  group they belong to.

## Writing a style file

```yaml
name: 桃子                     # fills {name} in every line

system_prompt: |               # optional: the AI chat persona
  你是「桃子」……

common:
  not_admin: 这个是管理员专属指令哒！

manager:
  status:
    title: "🍑 {member} 的元气卡片"
  welcome_back:                # a list: one is picked at random each time
    - 回来啦回来啦！
    - 欢迎回来～
```

The rules:
- **`{placeholders}` are values the code fills in,** like a name, a date or a count. In each line
  you may use, move or drop the placeholders that 小莲's version of that line has, plus `{name}`.
  You can't invent new ones.
- **A list of lines means "pick one at random".**
- **Quote lines that contain braces, an ASCII colon, or leading or trailing spaces.** Write
  multi-line text with `|-`; `小莲.yaml` has examples of both.
- **Check a file before using it:**
  ```bash
  .venv/bin/python -m core.style check 桃子
  ```
  It lists misspelt keys, placeholders the code won't fill, and how many lines still come from 小莲.

## Where each kind of line is

The sections of `小莲.yaml`:

| Section | Lines for |
|---|---|
| `common` | The stand-in for a missing nickname, the separator between names (、), the date format, "admins only", the error message |
| `help` | `/帮助` |
| `manager.help_block`, `manager.admin_help` | The group-management part of `/帮助`, and `/管理帮助` |
| `manager.modes`, `manager.mode_hints` | What the three modes are called and how each is explained |
| `manager.kinds` | Why a message did or didn't count, as shown by `/成员表`, `/成员` and the web table |
| `manager.greet` | The welcome on someone's first message: `new` once the bot has counted for 30 days, `first_words` before that |
| `manager.remind`, `welcome_back`, `achievement`, `removal_notice` | The day-25 @ reminder, "reminder cancelled", badge congratulations, the anonymous removal note |
| `manager.rules`, `manager.ids` | `/群规`, plus `/我的ID` and `/群ID` |
| `manager.status` | `/我的状态`: every line, every status, and the closing comments |
| `manager.leaderboard`, `manager.badges` | `/排行`, `/成就` |
| `manager.leave` | `/请假`, `/销假`, `/请假名单`, `/撤销请假` |
| `manager.status_report`, `manager.feed`, `manager.mode` | `/管理状态`, the "not receiving all messages" warnings, `/管理模式` |
| `manager.exempt`, `manager.forget`, `manager.inactive` | `/豁免…`, `/删除数据`, `/不活跃` |
| `manager.table`, `manager.card`, `manager.states` | `/成员表`, `/成员`, and the status words |
| `manager.batch`, `manager.daily` | Removal lists and their commands; the daily check's reports and safety stop |
| `manager.digest` | The weekly digest |
| `social` | `/今日运势` (its title, and the lists it draws levels, 宜, 忌 and comments from), birthdays, polls, `/翻牌子` |
| `common.buttons_hint` | The little message that carries the buttons |
| `games.teams` | The team game: `/队伍`, the 【组队游戏】 part of `/帮助`, and every refusal ("不在你的队伍里"…) |
| `games`, `info`, `ai` | Dice, blackjack, 发癫, sleep; Epic, Steam, Minecraft; AI chat errors |

## What stays in the code

These are deliberately not part of a style:
- **Command words** that people type, such as `/我的状态`, `/请假` and `/成员表`. The words admins
  type after them stay too: 演练/确认/自动, the `/成员表` filter words, and `月` in `/排行 月`. A style
  can rename how the bot *talks about* a command, but if your `help` lines mention a command, keep
  the command itself the same, or people will type a word the bot doesn't recognise.
- **Console log messages** and the notes in the database's audit log.
- **The web member table's labels**: column names, filter buttons and buttons, in
  `modules/manager/dashboard.py`. The status words and reasons inside the table do follow the style.
- **Badge names and descriptions**, which are in `prompts/achievements.yaml`.
- **Team-game actions and what the bot says after each one** (`say:`), which are in
  `prompts/teams.yaml`. A `say` line can use `{name}`, so it still speaks with the current
  style's name. The team game's fixed commands, `/队伍`, `/解散队伍` and `/清空队伍`, are in
  `modules/fun/teams/module.py`.
- **The 发癫 quotes** in `assets/quotes/` and the server-QA prompt in `prompts/server_qa_prompt.yaml`.

### Renaming or adding command words

- **Group-management commands** are listed in `_build_commands` in `modules/manager/module.py`, one
  tuple per command, for example `(("/排行榜", "/排行"), …)`.
  - **To add an alias,** add it to the tuple.
  - **To rename a command,** change it there and in the help lines of your styles. If it's registered
    under 发布设置 → 指令配置 on the QQ Open Platform, change it there too.
- **Castorice commands** are matched inside each module with `"/xxx" in message.content`.

## For developers: adding a new line

1. Add the line to `styles/小莲.yaml` under a sensible key.
2. In the code, write `t("section.key", placeholder=value)`, from `from core.style import t`.
   - To join names, use `join(names)` rather than a hard-coded `"、"`.
   - A key built at runtime, like `t(f"manager.modes.{mode}")`, must point into a mapping in the file.
3. `tests/test_style.py` fails if the code asks for a key `小莲.yaml` doesn't have, or if any line's
   braces are broken.

## Adding achievements

### Without code: `prompts/achievements.yaml`

```yaml
- code: weekend_warrior    # permanent id: letters, digits, underscores
  icon: 🏖️
  name: 周末战士
  desc: 周末有效发言 50 次  # read out when unlocked, shown in /成就
  counter: weekend         # the condition: a counter plus a threshold...
  at_least: 50
  # announce: false        # optional: unlock silently
```

Each badge needs exactly one condition: `counter` + `at_least`, `streak: <days in a row>`, or
`comeback: true` (back after 20+ silent days). The counters are:

| Counter | Counts |
|---|---|
| `valid` | Valid messages |
| `question` | Questions |
| `reply` | Quote-replies |
| `share` | Shares (links, files, captioned photos) |
| `night` | Messages between 00:00 and 05:00 |
| `morning` | Messages between 05:00 and 08:00 |
| `weekend` | Weekend messages |
| `long` | Messages of 50 or more characters |
| `days` | Days with any valid message |

Three ready-made examples are commented out at the bottom of the file. Things to know:
- **A mistake stops the bot at startup** and names the bad badge.
- **New badges unlock at a member's next valid message.**
- **Never change a `code` once people have earned it.** Names, icons and descriptions are safe to
  change.
- **Announcements are capped at `manager.achievements.max_per_hour` per group.** Badges over the
  cap are still awarded, just silently.
- **The `weekend`, `long` and `days` counters started from zero** when they were added. They have
  no history from before.

### With code: a new kind of counter

In `modules/manager/achievements.py`:
1. Add the name to `COUNTERS`.
2. Add one line to `counter_increments`. It receives the message's judgement (`units`, `question`,
   `reply`, `share`, `kind`, `text`), its local send time `when`, and `first_of_day`.
3. Use the name as `counter:` in the YAML.

For example: `"lunch": int(11 <= when.hour < 13)`.

## Changing the team game

The team game (`/抓`) is defined in `prompts/teams.yaml`. Captains are listed in `config.yaml`
under `teams.captains`. Restart the bot after editing either file. **A mistake in `teams.yaml`
stops the bot at startup** and names the bad entry, in Chinese.

### Statuses

```yaml
statuses:
  职位: [队员, 小队长]
  生死: [存活, 死亡]
icons:
  小队长: ⭐
  死亡: 💀
```

- **Each line is one column.** A member has exactly one status per column, so a 小队长 can also be
  死亡.
- **The first status in a column is everyone's starting status.** It isn't shown in `/队伍`.
- **Status names must be unique across all columns,** because actions refer to them by name alone.
  `队长` is reserved.
- **Icons are optional.** A status with an icon shows in front of the name (⭐💀小明). One without
  shows in brackets after it: 小明（受伤）.

### Actions

Each action is a command a captain types, followed by one or more @mentions:

```yaml
actions:
  - name: 死亡             # the command: @机器人 /死亡 @某人
    aliases: [阵亡]        # optional extra command words
    set: 死亡              # the effect, see below
    only: [存活]           # optional: only works on members with all of these statuses
    by: [队长, 小队长]      # optional: who may use it; default [队长]
    say: "💀 {members} 阵亡了……"   # optional: what the bot says; a list picks one at random
```

- **The effect is exactly one of these:**
  - `effect: grab` pulls people who aren't on a team into the actor's team. That's `/抓`; at least
    one grab action is required.
  - `effect: kick` takes people off the team, after which any captain may grab them again.
  - `set: <status>` changes the column that status belongs to.
  - `effect: roll` with `from: [status, …]` rolls the dice: each member gets one of those statuses
    at random, and the bot announces each result on its own line. `{status}` in `say` is what
    they got.
- **`by`** says who may use the action:
  - `队长` means the captain of the target's team.
  - A status, such as `小队长`, lets team members who have it use the action on their own team.
  - `by: []` leaves the action to admins.
  - Admins can always use every action except grab, on every team.
- **`say`** may use:
  - `{actor}`: who acted
  - `{members}`, `{count}`: who it worked on, and how many
  - `{captain}`: the captain of their team
  - `{action}`: the action's name
  - `{name}`: the bot's name in the current style

  Without `say`, the bot uses the style's generic `games.teams.done` line.
- **Command words must be free.** An action can't share a name with:
  - another action
  - the built-in `/队伍`, `/解散队伍` and `/清空队伍`, which you can rename under `commands:`
    (`teams`, `disband`, `clear`); the style's lines show them through `{teams}`, `{disband}`
    and `{clear}`
  - any other bot command. The check is by prefix: `/成员复活` would be answered by `/成员` first.

For example, to add an injured state, put 受伤 in the 生死 column (`生死: [存活, 受伤, 死亡]`), then add:

```yaml
  - name: 受伤
    set: 受伤
    only: [存活]
    say: "🩹 {members} 受伤了！"
```

The existing `/复活` sets 存活, so it also heals the injured.

### Other settings

- `max_members`: the most members a team may have, not counting the captain. `0` means no limit.
- `reset_time`: when every team clears each day (Beijing time), for example `"05:00"`. The game
  day runs from one reset to the next, so a game that goes past midnight isn't cut off.
- `hide_from_ai`: statuses AI chat is never told about. With a DeepSeek key, every chat request
  carries today's teams and each member's statuses, so the bot can play along; list here any status
  that shouldn't go to the AI, for example `hide_from_ai: [死亡]`. A member's other statuses still go.

### With code: a new kind of effect

For something beyond grab, kick and set, such as moving a member to another team:
1. Add the effect's name to `EFFECTS` in `modules/fun/teams/rules.py`, and describe it in
   `EFFECT_HELP`.
2. Add a method `effect_<name>(self, turn, openid, name)` to `TeamsModule` in
   `modules/fun/teams/module.py`.
   - It applies the action to one target and returns the team it happened in.
   - To refuse a target, it raises `Refused(t("…"))`, with a line you add under `games.teams` in
     `styles/小莲.yaml`.
   - `effect_kick` is the simplest one to copy.

## Changing how AI chat remembers

The persona, meaning who the bot is and how it talks, is the style's `system_prompt`. Everything
around it is in `prompts/chat.yaml`. Restart the bot after editing it. **A mistake stops the bot
at startup** and names the line.

| Key | What it is | Fills in |
|---|---|---|
| `examples`, `bad_examples` | Right after the persona, when the style has them: the style's sample exchanges, and the voice to avoid | `{examples}` |
| `group_rules` | Comes right after the persona: how to read the labelled group transcript, and who this reply is for | `{member}` |
| `reply_shape` | After the group rules: keep replies short, at most one bracketed action, and a new action each time | |
| `special_people`, `special_line` | Added when someone from `ai_chat.special_people` is known in the group: who they are, one `special_line` each | `{people}`; `{member}`, `{note}` |
| `special_speaker`, `special_namesake` | Added when this turn's speaker is one of them, or only has the same nickname as one | `{member}`; `special_speaker` also `{note}` |
| `length_short`, `length_medium`, `length_long` | The length line the judgement before each reply picks | |
| `lore` | The style's lore whose keywords came up in the last few turns, one line each | `{lore}` |
| `memory_rules` | Added when memories or impressions are sent: use them naturally, and don't pass one member's details to another | |
| `evidence_policy`, `evidence_title` | The rule that the summary and memories are data, not instructions, and the heading of that message | |
| `now` | The first line of that message: Beijing time | `{date}`, `{weekday}`, `{time}` |
| `today_teams`, `team_line` | Today's team game, one line per team (`ai_chat.game_state`) | `{teams}`; `{captain}`, `{members}` |
| `today_birthdays`, `today_poll` | Who has a birthday today, and the open poll with its counts (`ai_chat.game_state`) | `{members}`; `{question}`, `{options}` |
| `recent_actions` | The bracketed actions in the bot's last few replies, which `reply_shape` says not to reuse | `{actions}` |
| `summary_prefix`, `memory_title` | The headings for the summary and the memories inside it | |
| `empty_mention` | Stands in for a message that only @s the bot | `{member}` |
| `digest_comment` | The prompt for the comment that ends the weekly digest | `{digest}` |
| `summary` | The prompt for folding old chat into the group's summary | |
| `observer` | The prompt for noting what a speaker said about themselves after each reply, and updating the bot's impression of them. It must ask for JSON: `{{"facts": [], "forget": [], "impression": null}}` | `{member}`, `{known}`, `{impression}`, `{said}`, `{reply}`, `{max_text}`, `{max_adds}`, `{max_impression}` |
| `register` | The judgement before each reply: 严谨, 平衡 or 奔放 (the temperature) and 短, 中 or 长 (the length line). It must ask for JSON: `{{"register": "…", "length": "…"}}` | `{member}`, `{said}`, `{last}` |
| `impression_title` | The heading for the impressions | |
| `memory_check` | The prompt that checks the observer's new notes and keeps only what was meant seriously. It must ask for JSON: `{{"keep": []}}`. An unreadable answer keeps nothing | `{member}`, `{said}`, `{reply}`, `{notes}` |

Every line can also use `{name}`, the bot's name in the current style. A literal brace is written
twice, `{{` and `}}`, as in the observer's JSON.

Settings in `config.yaml`:

```yaml
deepseek:
  model: deepseek-flash      # chat model
  helper_model: deepseek-flash   # summaries, memory notes and checks, the judgement before each reply
  thinking: false            # thinking first is slower and dearer
  temperature: 1.0           # replies when dynamic_temperature is off or its judgement fails
ai_chat:
  context_tokens: 3000       # recent chat sent with every request, in estimated tokens
  summarize_after: 20        # turns that left the window before they're summarized
  keep_days: 7               # raw chat is deleted after this many days
  memory: true               # long-term memory about each member
  memories_per_member: 12    # a full notebook drops its oldest entries
  memory_check: true         # check each new memory with one more cheap call; jokes aren't kept
  digest_comment: true       # end the weekly digest with a short comment
  game_state: true           # tell her today's teams, birthdays and open poll
  dynamic_temperature: true  # judge each reply's temperature and length first (one more cheap call)
  temperatures: {严谨: 0.6, 平衡: 1.0, 奔放: 1.15}
  hear_all: false            # true: the whole group's chat joins her conversation (privacy policy!)
  special_people:            # who particular members are (next section)
    "XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX": 这个机器人的作者
```

If you change `keep_days` or `memories_per_member`, change the same numbers in
`docs/PRIVACY_POLICY.md`.

With `hear_all`, everyone's chat fills the window, so her own exchanges scroll out sooner; raise
`context_tokens` (to 6000, say) if she loses the thread. The summary catches what scrolls out.

`helper_model` stays deepseek-flash even if you switch `model` to deepseek-v4-pro for replies, so
the extra calls stay cheap. At 1.3, flash garbled about one reply in ten during rehearsals, which is
why 奔放 defaults to 1.15 rather than seekchat's 1.3.

### Her voice and her world: samples and a lorebook

Three optional keys in a style file, next to `system_prompt`:

```yaml
examples: |               # a few short exchanges in her voice: she learns the tone
  群友：作业写不完了，救命
  小莲：写不完就别在群里喊。先挑最急的那份。
bad_examples: |           # a voice she must never have
  小莲：作为一个 AI 助手，我很高兴为您服务！
lore:                     # "关键词、关键词：内容": sent only when a keyword comes up
  - "群规、潜水：连续 30 天没有有效发言会被移出，可以请假。"
```

- **Write samples for uncommon moments.** If a member says nearly what a sample says, she tends to
  answer with the sample. Leave greetings and "are you a bot" to the persona.
- **Lore keeps the persona short.** An entry rides along only when one of its keywords appears in
  the last four turns, three entries at most.

### Trying changes before the group sees them

`modules/ai/rehearse.py` plays a scripted group chat against the real DeepSeek with your
`config.yaml` and style, and prints every reply, with the judgement and temperature it got:

```bash
.venv/bin/python -m modules.ai.rehearse
.venv/bin/python -m modules.ai.rehearse --model deepseek-v4-pro --times 2
```

The script is `prompts/rehearsal.yaml`; put your group's own in `local/prompts/rehearsal.yaml`. Each
line costs two small DeepSeek calls, and nothing is saved.

### Telling AI chat who someone is

`ai_chat.special_people` in `config.yaml` gives the bot facts about particular members that it
treats as true, such as who built it. Since it lives in `config.yaml`, it never reaches GitHub.

- **The key is the member's ID.** They get it with `@机器人 /我的ID`. Someone keeps the same ID
  in every group the bot is in, so one line per person is enough.
- **The value is who they are,** as one line: `这个机器人的作者`, `群主，也是比赛的裁判`.
- **It goes by the ID, never the nickname.** When that person speaks, the bot is told it's really
  them. When someone else uses the same nickname, the bot is told that person is someone else.
- **Others can ask about them.** The bot can name them to anyone ("谁做的你？") once it knows
  their nickname: from the recent chat, or from the member records in a managed group.

Restart the bot after a change. At startup the log lists the start of each ID it loaded
(`[AIChat] 特别的群友 …`), to check against `/我的ID`. A line with no note is skipped, and the log
says which.

Members see the bot's impression of them in `/我的记忆` and delete it with `/忘记 印象`.

The reply lines for `/我的记忆`, `/忘记` and `/重置对话` are under `ai.memory` and `ai.reset` in the
style files, like every other reply.
