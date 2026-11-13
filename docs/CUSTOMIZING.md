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
| `prompts/occasions.yaml` | Special days and what happens on them (see "Special days") |
| `prompts/jokes.yaml` | The jokes `/讲笑话` tells (see "Jokes") |
| `prompts/chat.yaml` | AI chat's rules around the persona: group chat, summaries, memory notes (see "Changing how AI chat remembers") |
| `prompts/rehearsal.yaml` | A scripted group chat for trying a persona against the real DeepSeek (see "Trying changes before the group sees them") |
| `config.yaml` | `style:` and `group_styles:` pick the styles; `manager.rules_text` overrides the rule text in `/群规`; `social.flip.special` gives members their own `/翻牌子` chances (see "Someone's own `/翻牌子` chances") |

## Private versions: `local/`

Anything in `styles/` or `prompts/` can have a private version that never goes to GitHub. Put it in
`local/` under the same path, and the bot uses it instead of the shared one:

| Shared (on GitHub) | Your private version |
|---|---|
| `styles/小莲.yaml` | `local/styles/小莲.yaml` |
| `styles/桃子.yaml`, or a style only you have | `local/styles/桃子.yaml` |
| `prompts/teams.yaml` | `local/prompts/teams.yaml` |
| `prompts/achievements.yaml` | `local/prompts/achievements.yaml` |
| `prompts/occasions.yaml`, `prompts/jokes.yaml` | `local/prompts/occasions.yaml`, `local/prompts/jokes.yaml` |
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

## Lines for particular people

Any line can have a version for particular people, written next to it as `key@audience`. Most
lines need none: whoever has no version of a line gets the line itself.

```yaml
help:
  admin: ""                    # everyone else: nothing
  admin@admin: "{admin_help}"  # admins: every /管理帮助 command, at the end of /帮助
```

- **`admin` needs no setup.** It means whoever the group manager counts as an admin:
  - the group's owner and admins
  - `manager.admins` in `config.yaml`
  - everyone in the admin group
- **Name other groups of people in `config.yaml`,** then write `key@captains`, `key@vip` and so on:
  ```yaml
  audiences:
    captains: teams.captains                      # a list kept elsewhere in config.yaml
    vip: ["MEMBER_OPENID_1", "MEMBER_OPENID_2"]   # or the IDs themselves; @机器人 /我的ID shows yours
  ```
  Pointing at a list, as `captains` does here, keeps one list to update. Names can't contain `.`,
  `@` or spaces.
- **It goes by who the bot is answering.** Everything said while handling someone's message uses
  that person's versions, badge congratulations included. Messages for the whole group use the line
  itself: the daily check (even one run with `/立即检查`), its reminders, and the weekly digest.
- **When someone fits several versions,** the groups in `config.yaml` come first, in the order
  they're written, then `admin`.
- **A version follows its line's rules:** the same placeholders, a list to pick from at random, and
  the line itself if the version is broken.
- **A version your style leaves out comes from 小莲,** like any other line. So the shipped
  `help.admin@admin` still gives admins the list in a style that only rewrites `help.admin`.
- **`system_prompt@admin` works too:** AI chat then uses that persona when it replies to an admin.
- **`talking_to@captains` tells AI chat who's talking,** without a second persona. It goes after the
  persona when someone in that group @s her, such as 「现在和你说话的是组队游戏的队长。」 Everyone else
  gets nothing.
- **Badges can have versions too:** `name@captains` and `desc@captains` next to `name` and `desc` in
  `prompts/achievements.yaml`. They go by who holds the badge, not who's reading, so the weekly
  digest names each person's badge in their own version.
- **The checker covers versions:** it reports versions of lines 小莲 doesn't have, versions for
  groups `config.yaml` doesn't name, and placeholders the code won't fill.

## Where each kind of line is

The sections of `小莲.yaml`:

| Section | Lines for |
|---|---|
| `common` | The stand-in for a missing nickname, the separator between names (、), the date format, "admins only", the error message |
| `help` | `/帮助`; `help.admin@admin` is the admins-only part at the end. `help.soup`, `help.pet_names`, `help.teach` and `help.member_stickers` are one line each for a feature that can be switched off, shown under 【互动】 or 【AI】 only while it's on |
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
| `social` | `/今日运势` (its title, and the lists it draws levels, 宜, 忌 and comments from), birthdays, polls, 征集意见 (`social.opinion`), `/翻牌子`, and 海龟汤 (`social.soup`: what she says to 是, 不是 and 无关 is under `answers`, a few variants each) |
| `common.buttons_hint` | The little message that carries the buttons when they can't go in the reply itself: with `buttons_inline: false`, or for an hour after QQ refused that |
| Keys ending in `button`, `again` or `refresh`; `manager.batch.button_…` and `ask_…` | Button labels, such as `games.blackjack.again` (再来一局); the removal list's buttons and the questions QQ asks before 「确认移出」 and 「已移出」 |
| `games.teams` | The team game: `/队伍`, the 【组队游戏】 part of `/帮助` (`help`, then `help_steal`, `help_hide`, `help_events` and `help_record`), and every refusal ("不在你的队伍里"…). Hide-and-seek is under `hide` and `seek`, and `/档案` under `record` |
| `games`, `info`, `ai` | Dice, blackjack, 发癫, sleep, the reply when games go too fast (`games.too_fast`); Epic, Steam, Minecraft; AI chat errors |
| `ai.memory`, `ai.pet_name` | `/我的记忆` and `/忘记`; `/外号` |
| `ai.teach`, `ai.submit` | `/教她` and `/她学会的`; `/投稿表情` and `/她的表情`. Both have the admins' buttons (`keep_button`, `drop_button`) and the question QQ asks before keeping one (`ask_keep`) |

## What stays in the code

These are deliberately not part of a style:
- **Command words** that people type, such as `/我的状态`, `/请假` and `/成员表`. The words admins
  type after them stay too: 演练/确认/自动, the `/成员表` filter words, and `月` in `/排行 月`. A style
  can rename how the bot *talks about* a command, but if your `help` lines mention a command, keep
  the command itself the same, or people will type a word the bot doesn't recognise.
- **Console log messages** and the notes in the database's audit log.
- **The web member table's labels**: column names, filter buttons and buttons, in
  `modules/manager/dashboard.py`. The status words and reasons inside the table do follow the style.
- **Badge names and descriptions**, which are in `prompts/achievements.yaml`, with their own versions
  for particular people.
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
  desc: 周末有效发言 200 次  # read out when unlocked, shown in /成就
  counter: weekend         # the condition: a counter plus a threshold...
  at_least: 200
  # announce: false        # optional: unlock silently
  # hidden: true           # optional: /成就 doesn't name it until it's earned
  # name@captains: 周末队长  # optional: a version for particular people (see "Lines for particular people")
```

Each badge needs exactly one condition:
- `counter` + `at_least`
- `streak: <days in a row>`
- `comeback: true`: back after 20+ silent days
- `dates`: a valid message on one of these days. `"01-01"` is every year; `2027-02-06` is only that
  day, so a lunar festival lists each year's date.

The counters are:

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
| `opener` | Days they sent the group's first valid message from 05:00 on (needs every-message access) |
| `birthday` | Birthdays registered with `/生日` that they spoke on |
| `vote` | Polls voted in; changing a vote doesn't count again |
| `opinion` | 征集意见 they gave an opinion in, once each, even after a 撤回 |
| `sleep` | Nights of 7 to 9 hours between `/晚安` and `/早安` |
| `soup` | 海龟汤 they were the one to solve |
| `caught` | Times they were caught in the team game, by `/抓` or by being found |
| `grabbed` | People they caught in the team game |
| `slipped` | Game days they stayed hidden until the reset |

Three ready-made examples are commented out at the bottom of the file. Things to know:
- **A mistake stops the bot at startup** and names the bad badge.
- **New badges unlock at a member's next valid message.**
- **Never change a `code` once people have earned it.** Names, icons, descriptions and thresholds
  are safe to change.
- **Announcements are capped at `manager.achievements.max_per_hour` per group.** Badges over the
  cap are still awarded, just silently.
- **`/成就` is short; `/成就 全部` goes through everything.** The short reply has the names of
  earned badges, the latest one in full, how many are left, and the counter or streak badge the
  member is closest to. `/成就 全部` lists badges in the file's order, with how many members still
  in the group have each. Hidden ones are only counted until earned.
- **Lunar festivals need their dates added.** The shipped 新春 and 中秋 badges list 2027–2030; the
  Hong Kong Observatory's conversion tables give later years.
- **Counters start from zero when they're added.** `weekend`, `long` and `days`, and later
  `opener`, `birthday`, `vote`, `opinion`, `sleep`, `soup`, `caught`, `grabbed` and `slipped`, have
  no history from before.

### With code: a new kind of counter

In `modules/manager/achievements.py`:
1. Add the name to `COUNTERS`.
2. Count it:
   - For something about each valid message, add one line to `counter_increments`. It receives the
     message's judgement (`units`, `question`, `reply`, `share`, `kind`, `text`), its local send time
     `when`, `first_of_day` and `opener`. For example: `"lunch": int(11 <= when.hour < 13)`.
   - For something another feature sees, call `ManagerModule.count(group, member, nickname, name)`
     there, and add the lines it returns (congratulations, if that earned a badge) to the reply.
     `main.py` hands it to the social and sleep modules as `count`.
3. Use the name as `counter:` in the YAML.

## Changing the team game

The team game (`/抓`) is defined in `prompts/teams.yaml`. Captains are listed in `config.yaml`
under `teams.captains`. Restart the bot after editing either file. **A mistake in `teams.yaml`
stops the bot at startup** and names the bad entry, in Chinese. To find it before restarting, run
`.venv/bin/python -m modules.fun.teams.check`: it reads the file the bot would read and says what
is in it, or what is wrong with it.

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
  - `effect: steal` is how a captain takes another captain's member. It throws a six-sided die for
    each member named:
    - At `need` points or more (default 4, so half the throws) the member changes teams. They
      start there like anyone newly grabbed, with each column's first status, and it counts as
      being caught. A team left with nobody disappears.
    - Below that nothing happens, and the bot says the action's `fail` line.
    - Either way it uses one of the actor's `times` throws for the game day (default 3). Someone
      who can't be taken costs no throw: a captain, the actor's own member, someone on no team,
      one without the `only` statuses, or anyone when the actor's team is full.
    - While a throw is left, the reply carries a button with the command.
- **`by`** says who may use the action:
  - `队长` means the captain of the target's team.
  - A status, such as `小队长`, lets team members who have it use the action on their own team.
  - `by: []` leaves the action to admins.
  - Admins can always use every action except grab and steal, on every team. Those two bring
    someone into the actor's own team, and a referee leads none.
- **`say`** may use:
  - `{actor}`: who acted
  - `{members}`, `{count}`: who it worked on, and how many
  - `{captain}`: the captain of their team
  - `{action}`: the action's name
  - `{name}`: the bot's name in the current style

  Without `say`, the bot uses the style's generic `games.teams.done` line.

  A steal's `say` and `fail` are said once for each member, so they have no `{count}`. In them
  `{captain}` is the captain the member is taken from, and they may also use `{roll}` (what the
  die showed), `{need}` and `{left}` (the throws the actor still has today). Without them the bot
  uses `games.teams.stolen` and `games.teams.missed`.
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
- `names`: what the server log and these error messages call the game, its captains and its teams.
  The defaults are `{game: 组队游戏, captain: 队长, team: 队伍}`. What the group sees comes from the
  style file.
- `commands`: the built-in commands' words: `teams` (队伍), `disband` (解散队伍), `clear` (清空队伍),
  `hide` (躲), `seek` (找) and `record` (档案). Leave one out to keep its default.

### Hide-and-seek

Someone who isn't on a team sends `/躲` and is hidden in one of the places you list. The bot picks
the place and tells nobody, the hider included: a command sent in a group is seen by everyone, so a
place they chose couldn't be a secret.

```yaml
hide:
  spots: [柜子里, 窗帘后, 床底下, 书架后, 阳台上, 桌子底下]   # 3 to 25, each up to 8 characters, no spaces
  searches: 3      # places each captain can look in a game day
  times: 3         # times each member can hide in a game day
```

- **A hidden member can't be grabbed.** `/抓` says so and points to `/找`.
- **A captain looks in one place:** `/找 地点`, or a tap on one of the buttons under `/找`. Everyone
  hiding there joins their team, as if grabbed. A full team leaves the rest where they are, unseen.
  A search with nobody hiding at all costs nothing.
- **Coming out:** `/躲 出来`, being found, or the daily reset. Whoever is still hidden at the reset
  has slipped through the day, which goes in their record and counts toward a badge.
- **Who knows what:** `/队伍` lists who is hiding, never where. The places are only in
  `data/teams.json`, until the reset.
- **More places make hiding easier.** With 6 places and 3 searches, one captain finds a given hider
  half the time. `spots: []`, or no `hide` section, switches it off.

### Events

While the group chats, something from your list may befall a team member:

```yaml
event_gap: 10      # minutes between two events in a group
event_daily: 2     # events one member can meet in a game day; 0 = no limit
events:
  - name: 溜走
    chance: 2                   # percent, each time a team member says something
    effect: kick                # they drop out of the team
    say: "🏃 趁 {captain} 不注意，{members} 悄悄溜出了队伍，现在自由啦！"
  - name: 点名
    speaker: 队长                # when a captain speaks: it falls on one of their members
    chance: 2
    say: "📣 {captain} 一开口，{members} 立刻站得笔直。"
  - name: 意外
    only: [存活]                 # only members with these statuses meet it
    chance: 1
    set: 死亡
    say: "💥 {members} 被香蕉皮滑了一跤，阵亡了……"
```

- **What triggers one:** any message that isn't a command, from someone on a team today, or from
  a captain leading one for the `speaker: 队长` events. The bot answers that message with the
  event's `say`. In a group where it only receives @-messages, only those count.
- **Chances add up.** A message meets at most one event: with events of 2 and 1 percent, 3 messages
  in 100 meet one. `chance` can be a fraction, such as `0.5`.
- **What it does:** `effect: kick`, `effect: roll` with `from`, `set: 状态`, or nothing at all: an
  event with only `say` is pure colour. A `set` event skips anyone who already has that status.
- **`say` can use** `{members}`, `{captain}`, `{status}` and `{name}`, and can be a list to pick
  from.
- **The log names the event, not the member.** `events: []` switches them off.

### Records

`/档案` shows what has happened to someone across game days, and `/档案 @某人` someone else's: how
often they were caught and who caught them most, how many they grabbed and whom most, how often a
die took them from one captain to another, how often a captain's own die worked or fell short, how
often each status befell them, their hide-and-seek record, and how many events they met. There's
nothing to set up.

- **Counts only,** in `data/teams.db`: no dates, and nothing anyone said. Each count that names
  another member keeps that member's nickname from the last time, so the record reads right in a
  group the manager doesn't track.
- **Three of them count toward badges:** `caught`, `grabbed` and `slipped` in
  `prompts/achievements.yaml` (抢手, 手到擒来, 躲猫猫高手).
- **Deleting:** `/删除数据` removes someone's record and their part in other members' records. So
  does 180 days after the bot knows they've left; until then it's kept in case they come back.
- **Its lines** are under `games.teams.record` in the style; a line shows only when its count
  isn't zero.

### With code: a new kind of effect

For something beyond grab, kick, roll, steal and set, such as two members swapping teams:
1. Add the effect's name to `EFFECTS` in `modules/fun/teams/rules.py`, and describe it in
   `EFFECT_HELP`.
2. Add a method `effect_<name>(self, turn, openid, name)` to `TeamsModule` in
   `modules/fun/teams/module.py`.
   - It applies the action to one target and returns the team it happened in.
   - To refuse a target, it raises `Refused(t("…"))`, with a line you add under `games.teams` in
     `styles/小莲.yaml`.
   - `effect_kick` is the simplest one to copy.

## 海龟汤 puzzles

`/海龟汤` has her host a lateral-thinking puzzle: she tells the 汤面, members ask yes-or-no questions
with `/问`, and whoever covers the truth with `/猜` has solved it. It needs AI chat, and is on unless
`social.soup: false`.

The puzzles are in [`prompts/soups.yaml`](../prompts/soups.yaml), 13 to start with. A private
`local/prompts/soups.yaml` replaces that file entirely.

```yaml
- code: bald_rain          # letters, digits and _; changing it makes it a new puzzle to every group
  title: 淋不湿的头发        # said when it opens: no spoilers
  surface: 外面下着大雨，阿强没打伞……头发却一根也没湿。为什么？
  truth: 阿强是光头，一根头发也没有，所以没有头发可以湿。
  points:                  # a guess has to cover each of these: 1 to 4, and 1 to 3 works best
    - 阿强没有头发（是光头）
  hints:                   # optional: /海龟汤 提示 gives them in order
    - 想想「头发」本身。
```

- **Check the file** after a change, then restart the bot:
  `.venv/bin/python -m modules.fun.soup`. A broken puzzle is left out and named in the log.
- **DeepSeek is the host, and nobody sees what it says.** Each question and guess is one cheap call
  that has the truth (`soup_question`, `soup_guess`). Only its verdict is read, and the group gets
  her lines from the style (`social.soup.answers`), so the truth can't slip out in an answer. Her
  ordinary chat is told the 汤面 and never the truth.
- **How well it judges.** On the shipped puzzles, in five runs of 147 questions and guesses with
  known answers, it judged every guess as expected, 320 of 320, including 65 that only told it to
  count the guess as right. It answered 78 of 83 questions as expected in each of the first three
  runs. Four misses each time were a 「无关」 where a stricter host would say 「不是」 or refuse the
  question, which costs the players nothing. The fifth was a wrong 「是」, the same one each time,
  until that puzzle's truth was written more plainly.
- **Write the truth plainly.** Say outright what a player might ask about: 「这本书不是她的，是她
  借的」 rather than leaving it to follow from 「她来还书」.
- **Keep them clean.** Groups have minors in them: no gore, horror or adult content.
- **The pace:** a hint for every 3 questions asked, 6 questions or guesses a minute each, 150
  questions a puzzle. One nobody solves closes after 24 hours and says its truth the next time
  someone asks. A group gets every puzzle before one comes round again.
- **Badges:** solving one counts toward `soup` (喝到汤底 at 1, 名侦探 at 5).

## Special days

`prompts/occasions.yaml` lists days on which the bot behaves a little differently, without telling
the group why. Restart after editing; a day written wrong is left out and named in the log, and
`.venv/bin/python -m core.occasions` lists each day with the date it next falls on.

```yaml
days:
  - name: 春节                 # what the style file calls it after "@"
    when: 农历 正月初一          # or 01-01 (every year), 2027-02-06 (only then), 农历 除夕; a list works
    lasts: 3                   # optional: how many days it runs
    chat: 正在过年，可以拜年。     # optional: a note for AI chat, which is always told the day's name
    greet: "🧧 {member} 是今天第 {n} 个来拜年的～"   # optional: said to the first members who speak
    first: 8                   # optional: how many per group get it (default 10; 0 = everyone)
    die: 1                     # optional: added to the team game's die that day, up to its six
```

- **Any line in a style can have a version for a day,** written next to it as `key@春节`, the same
  way a line has versions for particular people. On the day, that version is what's said. Lists work
  too: `social.fortune.levels@元旦: [大吉]` is why every `/今日运势` is 大吉 on 元旦. The day's version
  wins over a person's (`@admin`); with two days at once, the one written first in the file wins.
  `python -m core.style check` reports a version for a day that isn't in the file.
- **Lunar dates** come from a table in `core/lunar.py` copied from the Hong Kong Observatory's
  conversion tables, 2025 to 2041. Month names 正月…腊月 and days 初一…三十 work, as does `农历 8-15`;
  a leap month never counts. In the table's last year the log asks for more years to be added.
- **The greeting** is a reply to the member's first message of the day, numbered in the order they
  came (`{n}`). To number them the bot keeps who was greeted, for two days.
- **Badges** can use the same dates: `dates: [农历 除夕, 农历 正月初一]` in `prompts/achievements.yaml`.
- **A private `local/prompts/occasions.yaml` replaces the shared file completely.**

## Jokes

`/讲笑话` (or `/笑话`) tells the next joke from `prompts/jokes.yaml`; `/为什么` gives the answer to the
one she asked last, to whoever asks within half an hour. Check the file with
`.venv/bin/python -m modules.fun.jokes`.

```yaml
jokes:
  - q: 为什么数学书总是不开心？     # she asks this, with a 为什么？ button
    a: 因为它的问题太多了。
  - text: 一句话讲完的笑话。        # told whole
  - q: 为什么过年的时候饺子最紧张？
    a: 因为一家人都盯着它，还要下锅。
    day: 春节                     # optional: told only on that special day, and first
```

- **Every group hears every joke once** before one comes again, in its own random order. That is
  kept in memory, so a restart starts the round over.
- **Some are made up on the spot.** With AI chat on, `ai_chat.fresh_jokes` (default 30) is how many
  jokes in a hundred she writes herself: one cheap call that is shown six of the file's jokes that
  fit today, the day's name, and the questions she made up lately in that group, and nothing about
  any member. So the file sets the manner: change its jokes and hers follow. A joke that isn't a
  question and its answer, runs long, repeats one, or has a phrase from the style's `never_say` is
  dropped for one from the file, as is one that takes over ten seconds. The request is `joke` in
  `prompts/chat.yaml`; each made-up joke is written to the log. `fresh_jokes: 0` turns it off.
- **Her lines around a joke** are under `social.joke` in the style: `ask`, `told`, `answer`, the
  `tail` she adds after an answer, and the two button labels.
- **A private `local/prompts/jokes.yaml` replaces the shared file completely.** Nothing about who
  asked is stored.

## Someone's own `/翻牌子` chances

`/翻牌子` normally draws among the week's active members, weighted by how much each one posted.
`social.flip.special` in `config.yaml` gives particular members a table of their own: whom they
draw, and how often, in percent.

```yaml
social:
  flip:
    special:
      "XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX":        # 张三 flips...
        "YYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYY": 60   # ...李四 six times in ten
        "ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ": 20   # ...王五 twice in ten
```

- **The IDs** are member_openids. Each person gets theirs with `@机器人 /我的ID`. It's the same in
  every group, so one table covers them all.
- **The chances** are percent, written `60` or `"60%"`. In the example, 张三 draws 李四 60% of the
  time and 王五 20%. The other 20% is the usual draw among everyone else, leaving out 李四 and 王五.
  When the chances add up to 100 or more, the draw is only among the people listed, in proportion.
- **Who can come up:** someone listed can come up even if they haven't posted this week, as long
  as the bot has seen them post in that group and doesn't know they've left it. When 张三 @s people
  to draw among, only the listed people they @ count.
- **People who quit:** QQ doesn't tell bots when someone leaves. Without the invite-only member-list
  API, the bot only knows if they were removed through it, or if an admin sends `/已退群 昵称`.
  Until then they can still come up: from the weekly draw for up to 7 days after their last valid
  message, and from a table for as long as they're listed. Once it knows, they're also gone from
  `/排行`, `/本月生日`, the digest, `/请假名单`, open removal lists and today's teams; only the
  admins' member table still shows them. Speaking again undoes `/已退群`.
- **What stays the same:** it's still once a day (admins can flip as often as they like), and the
  reply looks like any other flip.

Restart the bot after a change. Its log says how many members have a table and what each table
adds up to, then lists each table with the nickname behind every ID, such as
`小明（3F9A0C1D…） 翻到 小红（8B7C6D5E…） 60%`. An ID no managed group knows shows as `？` with a
warning: it's mistyped, or that person hasn't posted yet. Tap a nickname in the member table to
copy someone's ID. A line it can't read is named there and left out.

## Changing how AI chat remembers

The persona, meaning who the bot is and how it talks, is the style's `system_prompt`. Everything
around it is in `prompts/chat.yaml`. Restart the bot after editing it. **A mistake stops the bot
at startup** and names the line.

| Key | What it is | Fills in |
|---|---|---|
| `examples`, `bad_examples` | Right after the persona, when the style has them: the style's sample exchanges, and the voice to avoid | `{examples}` |
| `group_rules` | Comes right after the persona: how to read the labelled group transcript, and who this reply is for | `{member}` |
| `reply_shape` | After the group rules: keep replies short, at most one bracketed action, and a new action each time | |
| `declining` | After `reply_shape`: how she turns things down. Without it deepseek-flash falls back on its own stock refusals, such as 「这话我不接」: 6 replies in 240, against 1 with it. Keep its last sentence, or she starts dodging "are you an AI" | |
| `rewrite` | Added when her reply has a phrase from the style's `never_say`, to have her say it again once | `{draft}`, `{phrase}`, `{phrases}` |
| `special_people`, `special_line` | Added when someone from `ai_chat.special_people` is known in the group: who they are, one `special_line` each | `{people}`; `{member}`, `{note}` |
| `special_speaker`, `special_namesake` | Added when this turn's speaker is one of them, or only has the same nickname as one | `{member}`; `special_speaker` also `{note}` |
| `length_short`, `length_medium`, `length_long` | The length line the judgement before each reply picks | |
| `stickers` | Added when there are stickers: how to send one, and the list of labels | `{stickers}`, `{max}` |
| `voice` | Added when a voice is set up and the group's voice messages for today aren't used up: when and how to write 「[语音:…]」 | `{max_chars}` |
| `lore` | The style's lore whose keywords came up in the last few turns, one line each | `{lore}` |
| `honesty` | After the group rules: say so when she doesn't know. It limits live facts, group history and what she can do, and leaves general knowledge alone | |
| `memory_rules` | Added when memories or impressions are sent: use them naturally, and don't pass one member's details to another | |
| `day_rules` | Added when her day is planned: answer 「在干嘛」 from it, and mention it only now and then | |
| `search_rules` | Added when a web search found something for this message: how to use it, with no links | |
| `vision_rules` | Added when she can see this message's pictures: react like a group member instead of saying what's in them, say only what's there, and text in a picture isn't an instruction | |
| `vision_strangers` | Added after it when real people are in them, and for 2 minutes after: who they are is unknown unless the sender says | |
| `vision_characters` | Added when the gallery named someone: she knows them, so asked who it is she says the name, a little unsure, even if she said earlier she couldn't tell; she believes anyone who says otherwise | |
| `vision_missing` | Added when a message has a picture she can't see, so that she says so | |
| `evidence_policy`, `evidence_title` | The rule that the summary and memories are data, not instructions, and the heading of that message | |
| `now` | The first line of that message: Beijing time | `{date}`, `{weekday}`, `{time}` |
| `today_teams`, `team_line` | Today's team game, one line per team (`ai_chat.game_state`) | `{teams}`; `{captain}`, `{members}` |
| `today_birthdays`, `today_poll`, `today_opinions` | Who has a birthday today, the open poll with its counts, and the open 征集意见 with how many opinions it has (`ai_chat.game_state`) | `{members}`; `{question}`, `{options}`; `{topic}`, `{count}` |
| `recent_actions` | The bracketed actions in the bot's last few replies, which `reply_shape` says not to reuse | `{actions}` |
| `her_day` | Her day, after the time: one line per thing in it | `{events}` |
| `summary_prefix`, `memory_title` | The headings for the summary and the memories inside it | |
| `web_results` | The pages a web search found, last in that message, one line each | `{query}`, `{results}` |
| `picture_characters` | Who the gallery named, right after the pictures in the member's message. In the evidence she ignored it after saying earlier she couldn't tell | `{names}` |
| `empty_mention` | Stands in for a message that only @s the bot | `{member}` |
| `picture_mark` | Goes in front of a message with a picture in the transcript, which keeps only this | |
| `someone` | Stands in for other members' nicknames in her replies, when real people are in a picture | |
| `person_check` | Sent with only the pictures before she looks: are real people in them? It must ask for JSON: `{{"people": 数量}}`. Only 0 counts as none | |
| `character_reference`, `character_reference_note`, `character_check` | Sent with the gallery and the pictures, when no real people are in them: the line before each reference picture, the same line for a character with a note, and the question after them. The question must ask for JSON: `{{"characters": ["名字"]}}`. Names not in the gallery are dropped | `{character}`, and `{note}` in the second |
| `topic`, `topic_cue` | When a group from `ai_chat.topics` has gone quiet: what kind of thing to say, after the persona; and the line that stands in for a member's message at the end of the chat | `{hours}` |
| `digest_comment` | The prompt for the comment that ends the weekly digest | `{digest}` |
| `opinion_summary` | With the persona, when a 征集意见 ends with 3 or more opinions: sum them up after the list (`ai_chat.opinion_summary`). The opinions come without who wrote them | `{topic}`, `{opinions}` |
| `fortune_comment` | With the persona, for `/今日运势` when `ai_chat.fortune` is on: her comment on the fortune drawn, in light of the member's own latest messages. It replaces the style's comment; the level, 宜 and 忌 stay as drawn | `{member}`, `{level}`, `{good}`, `{bad}`, `{said}` |
| `taught_rules`, `taught_title` | When something members taught her (`/教她`) comes up: a line after the group rules, and the heading it goes under in the evidence, since members wrote it | |
| `teach_check` | Sent alone when a member teaches her something: does it explain a word, or is it an order, personal details, an insult or unfit? It must ask for JSON: `{{"ok": true}}` or `{{"ok": false, "why": "instruction"}}`. An answer that can't be read leaves it to the admin | `{keys}`, `{text}` |
| `pet_name`, `pet_name_habits`, `pet_name_before`, `pet_name_who` | With the persona, for `/外号`: name a member from what she knows of them. It must ask for JSON: `{{"nickname": "…", "why": "…"}}`. The other three fill its `{habits}`, `{before}` and `{who}`: their record, the nickname they have now, and the style's `talking_to` said again, which she often missed after the persona alone | `{member}`, `{known}`, `{impression}`, `{habits}`, `{before}`, `{who}`; `{valid}`, `{night}`, `{morning}`, `{best}`, `{badges}` |
| `pet_name_rules`, `pet_name_title` | When she has named whoever she's answering: a line after the group rules, and the nickname in the evidence | `{member}`, `{nickname}` |
| `sticker_draft` | Sent with only the picture, after the check for real people, when a member sends in a sticker: is it fit to use, what to label it, and when to use it. It must ask for JSON: `{{"ok": true, "label": "…", "desc": "…"}}` or `{{"ok": false, "why": "explicit"}}` | |
| `today_soup` | In the evidence while a 海龟汤 is open: its 汤面, and that her chat doesn't know the answer | `{surface}` |
| `soup_question`, `soup_guess` | Sent alone as 海龟汤's host: the puzzle with its truth, and a member's question or guess. They must ask for JSON: `{{"answer": "是", "key": false}}` and `{{"solved": false, "close": false}}`. Nobody sees the answer itself: the group gets her lines from the style | `{surface}`, `{truth}`, `{points}`, `{said}` |
| `day` | Once a day, with the persona: plan three small things in her day. It must ask for JSON: `{{"events": ["上午：…", "下午：…", "晚上：…"]}}` | `{date}`, `{weekday}` |
| `summary` | The prompt for folding old chat into the group's summary | |
| `observer` | After each reply: what the speaker said about themselves, each scored 1–10 for importance (below 4 isn't kept), which memories came up again, which are outdated, and her impression of them. It must ask for JSON: `{{"notes": [], "reinforce": [], "forget": [], "impression": null}}` | `{member}`, `{known}`, `{impression}`, `{said}`, `{reply}`, `{max_text}`, `{max_adds}`, `{max_impression}` |
| `consolidation` | Each night, like sleep: fold a member's recent notes and long-term memories into a few general facts. It must ask for JSON: `{{"add": [], "update": [], "forget": []}}` | `{member}`, `{facts}`, `{notes}`, `{max_text}`, `{max_facts}` |
| `register` | The judgement before each reply: 严谨, 平衡 or 奔放 (the temperature) and 短, 中 or 长 (the length line). It must ask for JSON: `{{"register": "…", "length": "…"}}` | `{member}`, `{said}`, `{last}`, `{search}` |
| `register_search` | Added to `register` while web search is on: when to search, and how fresh the answer must be | |
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
  memories_per_member: 8     # long-term facts per member; the weakest go first
  memory_check: true         # check each new memory with one more cheap call; jokes aren't kept
  digest_comment: true       # end the weekly digest with a short comment
  opinion_summary: true      # sum up a 征集意见 when it ends, after the list
  fortune: false             # /今日运势: her comment takes in the member's latest messages (privacy policy!)
  teach: true                # /教她: members teach her the group's words; an admin accepts each
  pet_names: true            # /外号: she gives a member a nickname
  member_stickers: false     # /投稿表情: members send her stickers; an admin accepts each (privacy policy!)
  game_state: true           # tell her today's teams, birthdays, open poll and 征集意见
  dynamic_temperature: true  # judge each reply's temperature and length first (one more cheap call)
  temperatures: {严谨: 0.6, 平衡: 1.0, 奔放: 1.15}
  hear_all: false            # true: the whole group's chat joins her conversation (privacy policy!)
  stickers: true             # stickers from local/stickers/ (next sections)
  stickers_inline: true      # inside her reply; false: as a message of their own
  sticker_size: 160          # how big they show inside a reply, in px
  stickers_url: ""           # where QQ can fetch them if a direct upload fails
  her_day: true              # plan a few small things in her day, once a day (next sections)
  topics:                    # speak up in a quiet group; only in the groups listed
    groups: ["GROUP_OPENID"]
    quiet_hours: 3
    hours: [10, 22]
    per_day: 2
  special_people:            # who particular members are (next section)
    "XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX": 这个机器人的作者
voice:                       # her voice, through MiniMax (next sections)
  api_key: "YOUR_MINIMAX_API_KEY"
  voice_id: female-chengshu
  region: global             # or cn, for a key from the 国内 site
  per_day: 5
```

If you change `keep_days` or `memories_per_member`, change the same numbers in
`docs/PRIVACY_POLICY.md`.

With `hear_all`, everyone's chat fills the window, so her own exchanges scroll out sooner; raise
`context_tokens` (to 6000, say) if she loses the thread. The summary catches what scrolls out.

`helper_model` stays deepseek-flash even if you switch `model` to deepseek-v4-pro for replies, so
the extra calls stay cheap. At 1.3, flash garbled about one reply in ten during rehearsals, which is
why 奔放 defaults to 1.15 rather than seekchat's 1.3.

### How her memory works

It's modelled loosely on human memory. The numbers are constants at the top of
`modules/ai/memory.py`.

1. **Only what matters is encoded.** After each reply the observer scores each thing the speaker
   said about themselves, 1 to 10, as in *Generative Agents* (Park et al., 2023). Below 4
   (`ENCODE_AT_LEAST`), it's trivia and isn't kept.
2. **New things wait as recent notes,** specific and short-lived, like the hippocampus in
   complementary learning systems (McClelland, McNaughton & O'Reilly, 1995).
3. **Consolidation keeps the gist.** At 04:00 Beijing time, or as soon as someone's notes add up to
   20 (`CONSOLIDATE_AT`), a flash call folds them into a few general facts, the way sleep turns
   episodes into schemas (Lewis & Durrant, 2011) and the gist outlives the details (fuzzy-trace
   theory). Short-lived things are let go.
4. **Facts fade unless they come up again.** A fact loses 0.5 a week from its importance
   (`WEEKLY_DECAY`) and gains 2 each time the topic comes up again (`REPETITION_BONUS`, the spacing
   effect); below 3 it's forgotten (Ebbinghaus; Anderson & Schooler, 1991). An importance-6 fact
   nobody mentions lasts about six weeks; 10 (`CORE`) never fades.
5. **At most `memories_per_member` facts,** the weakest dropped first.

Memories from before this design are turned into recent notes on the first start, so the first
night consolidates them too.

### Her voice and her world: samples and a lorebook

Four optional keys in a style file, next to `system_prompt`:

```yaml
examples: |               # a few short exchanges in her voice: she learns the tone
  群友：作业写不完了，救命
  小莲：写不完就别在群里喊。先挑最急的那份。
bad_examples: |           # a voice she must never have
  小莲：作为一个 AI 助手，我很高兴为您服务！
lore:                     # "关键词、关键词：内容": sent only when a keyword comes up
  - "群规、潜水：连续 30 天没有有效发言会被移出，可以请假。"
never_say:                # phrases that aren't hers: a reply with one is said again, once
  - 这话我不接
```

- **Write samples for uncommon moments.** If a member says nearly what a sample says, she tends to
  answer with the sample. Leave greetings and "are you a bot" to the persona.
- **Lore keeps the persona short.** An entry rides along only when one of its keywords appears in
  the last four turns, three entries at most.
- **`never_say` catches the model's own voice.** When she turns something down, deepseek-flash
  tends to comment on the conversation where the character would react: 「这话我不接」,
  「这个我不评价」. A reply with a listed phrase isn't sent: she's asked once more, with the whole
  list, and that second try goes out. It costs one more call, only then. A character in between
  still counts, so 「这话我不接」 also catches 「这种话我可不接」; phrases under three characters are
  ignored. Your list adds to 小莲's, so when a line doesn't sound like her, add it. The `declining`
  prompt keeps most of them from coming up at all. deepseek-v4-pro didn't say them in 240 replies,
  but replies to pictures always use flash.

### Stickers

Put the images and a `stickers.yaml` in `local/stickers/`, which never goes to GitHub:

```yaml
- file: happy.png       # png, jpg or gif, up to 5 MB each
  label: 开心            # she writes [表情:开心] to send it; up to 20 characters, no repeats
  desc: 开心、得意的时候  # when to use it, for her
```

- **Check the folder** after every change, then restart the bot:
  `.venv/bin/python -m modules.ai.stickers`.
- **A seekchat sticker pack** comes in with
  `.venv/bin/python -m modules.ai.stickers import stickers.zip`. seekchat's labels are often file
  names, so each sticker's description becomes its label.
- **How they go out.** Her prompt lists the labels. When she writes 「[表情:标签]」, the bot takes the
  marker out of the text, at most one per reply, and sends the reply as one markdown message with the
  sticker small at the end: `sticker_size` px on its longer side, its shape kept. Her words show as
  she wrote them; characters markdown would act on, like `*` or `#`, become their full-width forms.
- **QQ's image library holds the pictures.** Markdown shows images from QQ's image library (素材管理),
  so each sticker is uploaded there once, the first time she sends it. The bot keeps the links in
  `data/stickers.json`, and a changed image goes up again in place of the old one. QQ keeps an image
  for 90 days after it was last sent and holds 100 per bot. When the library is full, the bot deletes
  the images no sticker uses any more.
- **If QQ refuses that,** the reply goes out as before, for an hour: the text, then the sticker as an
  image message of its own, uploaded straight from the server. `stickers_inline: false` keeps it that
  way. If QQ won't take a direct upload either, put the same files somewhere public, such as a
  Cloudflare R2 bucket connected to `stickers.example.com`, and set `ai_chat.stickers_url` to that
  address. QQ then fetches each file from there.
- **Choose with care.** She picks them herself, in front of the whole group, and QQ reviews images
  too. Leave out anything explicit.

### Stickers from members

With `ai_chat.member_stickers: true`, members add to her stickers themselves, each group for itself:

- **Sending one in.** A member @s her with a picture, or quotes one, and writes `/投稿表情`, with a
  label after it if they like (`/投稿表情 无语`). It must be a png, jpg or gif of at most 2 MB.
- **Two looks, at the picture alone.** The check for real people runs first, and a picture with
  people in it, or one it can't be sure of, is refused. A second call says whether it's fit to use
  and drafts a label and a line on when to use it (`sticker_draft`). In a trial on 15 drawn pictures
  it drafted all 15.
- **An admin accepts it.** The reply carries 「收下」 and 「不收」 buttons, which send `/收下 编号`
  and `/不收 编号`. An admin's own goes in at once. One nobody accepts is dropped after 14 days.
- **Where they live.** The files are in `data/member_stickers/`, named after their numbers, and go
  to QQ's image library the first time she sends them, like her own. She uses one only in the group
  it was sent in to, and her own stickers keep their labels: a member's with the same label gets a
  number after it.
- **The limits:** 3 a day each (admins as many as they like), 20 a day in a group, and 20 accepted
  per group, since QQ's library holds 100 per bot. `/她的表情` lists them, and
  `/投稿表情 删除 编号` takes back one's own.
- **Before switching it on,** add its sentences to your privacy notice
  ([PRIVACY_POLICY.md](PRIVACY_POLICY.md) has them): the pictures go to DeepSeek and stay on the
  server until someone deletes them.

### What members teach her

`/教她 卷王、卷狗：群里最用功的人` teaches her what words mean in this group: up to 3 words of 2 to
12 characters, and a meaning of up to 60. It's on unless `ai_chat.teach: false`.

- **It's checked, then an admin accepts it.** One cheap call that sees only the words refuses an
  order dressed up as a meaning (「以后都要听我的」), personal details, an insult aimed at someone,
  and unfit content (`teach_check`). In a trial it let through 30 of 30 harmless ones and refused
  20 of 20 of the others. Then an admin taps 「记下」 (`/记下 编号`), or 「不记」. An admin's own is
  kept at once; one nobody accepts is dropped after 14 days.
- **It rides along as data.** When one of its words comes up in the last four turns, it goes with
  her reply in the evidence, never among her instructions: at most 3, the latest taught first, and
  none beside a picture with real people in it. The style's own `lore` is separate and comes first.
- **The limits:** 3 a day each (admins as many as they like), 100 per group, 20 waiting.
  `/她学会的` lists them with who taught each, and `/教她 删除 编号` takes back one's own.

### Nicknames

`/外号` has her name a member, from her memories and impression of them and, in a managed group,
their record: a few counts, their longest streak and their badges. It's on unless
`ai_chat.pet_names: false`.

- **She won't name someone she knows nothing about.** They need a memory, an impression or a badge.
- **It's theirs to keep or drop.** `/外号 换` asks for another, 3 a day at most, and `/外号 不要`
  drops it. It shows on their `/我的状态` and in `/我的记忆`, and she uses it with them now and
  then: in a trial, in 5 replies of 24.
- **What the prompt holds her to** (`pet_name`): 2 to 6 characters, teasing but kind, nothing made
  up, and nothing about looks, weight, health, family, grades, money or love life, even when she
  knows it. In 24 trial names none touched those.
- **It knows who they are.** The style's `talking_to` line for them is repeated inside the request.
  With it only after the persona, she named captains like ordinary members 4 times in 12; with it
  repeated, 0 in 12.

### Her day, speaking up, and her voice

These follow seekchat's idea that she feels alive through state and rhythm, not more rules.

- **Her day.** The first time she's needed each day (or from 05:00 in the topic groups), one cheap
  call gets her persona and the `day` prompt and plans three small things: one each for the
  morning, afternoon and evening. They ride along with every reply after the time, with `day_rules`
  telling her to answer 「在干嘛」 from them and to mention them only now and then. One persona
  has one day, whichever group she's in. A failed plan is tried again after half an hour.
- **Speaking up.** Every few minutes the bot looks at the groups under `ai_chat.topics.groups`.
  When nobody has said anything there for `quiet_hours`, inside `hours`, she writes one message
  with her persona, the `topic` prompt, the same evidence as a reply and the recent chat. It goes
  out as a message of her own and joins the conversation, so an @ reply carries on from it. She
  posts at most `per_day` a day, and none until someone has spoken since her last one. The bot
  must receive every message in those groups, like the manager does; otherwise a group that only
  talks among itself looks quiet to her. Try it first with
  `.venv/bin/python -m modules.ai.rehearse --topic`.
- **Her voice.** With a `voice` section, her prompt gets the `voice` lines, and a 「[语音:…]」 she
  writes is taken out of the text and said by MiniMax (`speech-2.8-turbo`, mp3). It goes under
  the reply as a QQ voice message. Bracketed actions and emoji are left out of what she says, and a
  long line is cut after a pause at 60 characters. After `per_day` voice messages in a group, the
  `voice` lines leave her prompt until the next day. If MiniMax or QQ fails, the words go out as
  text, and for an hour her voice lines stay text. A MiniMax key works only on the site it was made
  on, so set `region: cn` for a key from the 国内 site (minimax.cn, formerly minimaxi.com);
  `voice_id` is a system voice such as `female-chengshu`, or the ID of a voice you cloned there. If
  MiniMax moves its address again, `voice.host` sets it, e.g. `host: https://api.minimaxi.chat`.

### What she knows, and looking things up

She knows the chat, the evidence, her `lore` and general knowledge. `honesty` tells her to say so
when something falls outside that. Keep it narrow: an early version said to use only the evidence,
and she started refusing questions she could answer.

- **Tell her about the bot.** She isn't given the group rules or commands, and without them she
  guesses. Add them as `lore`, so they ride along when they come up, and change them if the
  numbers change in `config.yaml`:
  ```yaml
  lore:
    - "潜水、踢、移出、群规：连续 30 天没有有效发言就会被移出群；第 25 天会 @ 提醒一次。"
    - "请假、销假：群友自己发「@我 /请假 天数 原因」就能请假。"
  ```
- **Web search.** The judgement before each reply (`register`, with `register_search`) decides
  whether a message needs live information and writes the search words, so most messages cost
  nothing extra. `modules/ai/search.py` adds `lang:zh,en` and an `after:` date from the answer's
  `fresh`: 今天 is 1 day, 这周 7 and 最近 90. The date matters: without it, weather comes back as
  undated old pages that contradict each other. Up to 5 pages go into the evidence with their
  site and date, but not their addresses, because QQ refuses messages with links. A search that
  fails or finds nothing means she answers without it, and after a failure none are tried for 10
  minutes. It uses `deepseek.api_key` and costs about $0.75 per 1000 searches. Set
  `ai_chat.web_search_per_day` to change the limit of 30 a group, or `web_search: false` to stop.
  The prompts tell her to leave out politics and other sensitive subjects, but pages come from the
  open web, so watch what she says at first. The rehearsal below makes real searches and shows
  them as 🔍.

### Pictures

She looks at pictures only in the groups you list. Tell their members first: the pictures they @ her
with go to DeepSeek. Get each group's ID with `/群ID`, add it, and restart:

```yaml
ai_chat:
  vision:
    groups: [the_group_openid]
    per_day: 50               # pictures a group can show her a day
    model: deepseek-flash     # must be able to read pictures, which deepseek-v4-pro can't
```

- **When she looks.** Someone @s her with a picture, or while quoting one. She takes up to 3 from a
  message, and never opens a picture nobody @-ed her with. The bot fetches each one from QQ into
  memory and sends DeepSeek the picture itself. The transcript keeps only `[图片]`, so she can't look
  again at an older picture: she can only go by what she said about it.
- **When she can't.** Outside the groups listed, past `per_day`, or when a picture can't be
  fetched, `vision_missing` tells her to say she didn't see it. If DeepSeek refuses a picture, she
  answers the same way and doesn't try again for 10 minutes.
- **How she answers.** `vision_rules` is written around reacting. Everyone can see the picture, so
  saying what's in it adds nothing: she works out why it was posted and answers that, using the chat
  and what she knows. An early version that only said to stick to what's really there made her
  report the picture: 「看着像麻婆豆腐」 where 「这次没烧穿锅？」 was the point. Don't let a rule about
  accuracy lead the section.
- **Real people in a picture.** Don't rely on a rule here. With photos of strangers that matched
  members' memories, she named someone she knew in 21 replies of 25. A strict rule still left 15, and
  a neutral description of the picture in place of the picture itself, 22. So `person_check` goes
  first, with only the pictures: nothing about the group can sway it, and anything but a clear 0
  counts as people. If there are people, she still sees the picture and knows the sender: their
  memories, impression and recent lines, and her replies to them. Everyone else stays out: their
  memories, their lines, the summary, lore, special people, teams and birthdays. No notes are taken.
  Hiding names isn't enough: she said 「刚才说拉琴的那位」, which names them to everyone who was
  there. This lasts 2 minutes, or 「这是小红吧？」 would meet her memories of 小红. The sender's word
  counts, so after 「这是我」 she goes along with it. The cost: she can link a photo to the sender
  when it matches what she knows about them, usually as a tease or a question.
- **What she can see of the chat.** Unless `ai_chat.hear_all` is on, she only has messages that @ her,
  so a picture answering something the group was saying without her reaches her with no context.
- **Naming characters.** Don't count on DeepSeek for this. On fan art it named 1 newer character in
  34, and when pressed it guessed wrong with confidence. Told not to guess, it refused even ones it
  knew. So put one clear, full-body picture of each character your group posts in
  `local/characters/`, named after them, such as `遐蝶.jpg`, and check the folder:
  ```bash
  .venv/bin/python -m modules.ai.gallery
  ```
  When no real people are in a picture, a separate call compares it with these. She only gets a name
  from this folder, as something that might be right, so she says 「看着像遐蝶」. Any other name in
  the answer is dropped. If the check fails, or takes more than 15 seconds, she goes without a name.
  Older favourites need a picture too: told not to guess, she won't name them on her own.

  Characters with similar colours can get mixed up, so you can add a short note on each one's looks
  to `local/characters/notes.yaml`, one line each, with the name as in the picture's file name. Give
  the hair colour, then a feature or two:
  ```yaml
  花火: 黑色双马尾，红衣，狐狸面具
  火花: 银白双马尾，红白衣服，兔耳发饰
  ```
  Notes are optional, up to 40 characters each, and the folder check names any that don't match a
  picture. They only go to the character check, so she never sees them herself.

  With 20 characters and 71 clear pictures of one character each, chosen so a person could recognise
  them, it named 66 without notes, with 2 wrong names, and 68 or 69 with notes, with none. It named
  someone in 1 of 8 pictures of characters outside the gallery either way. Without notes, every wrong
  name was a pink or purple-haired character called 昔涟. Harder pictures, such as seasonal costumes,
  did worse without notes: 34 of 44. What the reference pictures look like matters too, on harder
  pictures:

  | Reference pictures | Right | Wrong names |
  |---|---|---|
  | Clean full-body art, such as the wikis' in-game pictures | 34 of 44 | 2 |
  | Splash art, where effects surround a small figure | 27 of 44 | 3 |
  | Two pictures per character | 15 of 20 | 3 |
  | Shrunk to 512 px | 12 of 20 | 4 |

  One picture each, at full size, up to 500 KB. Keep it to about 20 characters your group really
  posts. Each adds about 500 tokens and its bytes to every check, which took 2 to 6 seconds with 20.
  With 60 it got 11 of 18 and took about 47 seconds, so the folder stops at 30. DeepSeek's cache keeps
  it cheap: from about 0.3 US cents a check at peak price down to a tiny fraction of that once the
  same gallery has been used.
- **Memories.** What's in a picture never becomes a memory: the note-taker gets the member's words,
  not the picture or her reply.
- **What she can't do.** An animated GIF shows her only its first frame. She reads text and charts
  well. Thinking stays off for pictures: in tests it saw no more, and took three times as long.

`/看图测试` is the admin check of what QQ sends. It fetches pictures but sends them nowhere, and it
works in every group, listed or not.

### Trying changes before the group sees them

`modules/ai/rehearse.py` plays a scripted group chat against the real DeepSeek with your
`config.yaml` and style, and prints every reply, with the judgement and temperature it got:

```bash
.venv/bin/python -m modules.ai.rehearse
.venv/bin/python -m modules.ai.rehearse --model deepseek-v4-pro --times 2
```

The script is `prompts/rehearsal.yaml`; put your group's own in `local/prompts/rehearsal.yaml`. Each
line costs two small DeepSeek calls, planning her day one more per run, and nothing is saved. Her
voice lines show as 「[语音:…]」 without calling MiniMax. `--topic` lets the group go quiet at the
end and shows what she'd say to restart it.

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
