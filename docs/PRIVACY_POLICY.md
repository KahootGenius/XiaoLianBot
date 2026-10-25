# 隐私政策 / Privacy Policy

> **提交说明**：以下「中文正文」为提交至 QQ 开放平台的隐私政策文本。
> 文末「English reference」仅供开发者阅读，**不属于提交内容**。
>
> 本文档依据机器人代码的实际行为撰写。**若功能变更，必须同步更新本文。**
> 审核方会对照实际行为核验，描述与实现不符是常见的驳回原因。

---

## 中文正文

**生效日期：** ____年__月__日
**开发者联系方式：** ____________________

本机器人（「小莲」）为 QQ 群提供娱乐、资讯和群管理功能。其中，**群管理功能只在群管理员主动开启、并经群主授权「接收群内全部消息」的群里生效**；在其他群里，本机器人只处理 @ 本机器人的消息。

### 一、我们收集哪些信息

本机器人仅收集为实现功能所必需的最少信息：

1. **群组标识（group_openid）**
   用于：记录本群绑定的 Minecraft 服务器地址、Epic/Steam 推送订阅状态、21点游戏状态，以及识别开启了群管理的群。
   该标识由 QQ 平台生成，不包含群号或真实身份信息。

2. **成员标识（member_openid）与 QQ 昵称**
   用于：睡眠时长统计；组队游戏中识别队长和队员；在开启群管理的群中，用于活跃统计、第 25 天的 @ 提醒、状态卡片、排行榜、成就和每周小报。
   成员标识由 QQ 平台生成，**每个机器人各不相同**，无法用于跨应用识别用户；昵称由 QQ 平台随消息一同下发。

3. **群消息**
   - **未开启群管理的群**：仅处理用户主动 @ 本机器人的消息，**不读取、不存储**群内其他聊天记录。
   - @ 本机器人与它聊天的消息，按下面第 6 项保存。
   - **开启群管理的群**：本机器人会读取群内的每一条消息，**仅用于判断是否属于群规所说的「有效发言」**。我们**只保存以下元数据**，不保存聊天记录：
     - 消息 ID、发送者标识、发送时间；
     - 这条消息是否计为有效发言，以及判断类别（例如「表情」「签到」「复读」）；
     - 每位成员**最近一次有效发言的前 30 个字**，仅用于成员通过 `/我的状态` 查看自己的记录（群管理员可在配置中关闭）。

4. **群管理记录**（仅开启群管理的群）
   有效发言计数与连续天数、成就、请假记录（起止时间和成员自己填写的原因）、豁免名单、提醒与移出记录。
   如果 QQ 平台向本机器人开放了群成员列表接口，还会读取成员列表中的昵称、角色（群主/管理员/成员）和入群时间，用于统计从未发言的成员。

5. **组队游戏记录**（仅在管理员开启组队游戏后）
   当天的队伍名单：队长和队员的成员标识、昵称、被抓进队的时间，以及游戏内状态（例如「小队长」「死亡」）。

6. **与机器人的 AI 对话**（仅在配置了 AI 后）
   - @ 本机器人的消息和它的回复，连同发言人的昵称，用于让它记得群里刚才聊了什么；更早的内容会被整理成一段群聊总结。
   - 本机器人会根据成员**亲口告诉它的、关于自己的**事情，整理少量长期记忆（每人最多 12 条，每条不超过 60 字），用于之后的聊天，例如记得某人在准备考试。不记录手机号、住址、证件号、账号密码等敏感信息。
   - 每个群的对话和记忆分开保存，在一个群里说的话不会出现在另一个群。

7. **运行日志**
   服务器运行日志中会记录成员标识（member_openid）与指令调用记录，用于故障排查。

我们**不会**收集：QQ 号码、手机号、密码、身份证件、位置、通讯录、支付信息或任何其他个人敏感信息。我们不会以任何方式索取用户账号密码。

### 二、信息如何使用

上述信息**仅用于本机器人自身功能的实现**：

- 执行群规：统计有效发言，第 25 天 @ 提醒，按群规移出连续 30 天没有有效发言、且未请假、未被豁免的成员；
- 状态卡片、排行榜、成就和每周小报；
- 其他娱乐与资讯功能。

信息不会用于其他机器人、其他产品或任何其他用途，不会用于用户画像或广告投放。

**关于移出**：是否移出由程序依据群规自动判断。移出前一定会先 @ 提醒并留出至少 5 天；管理员可以设置为「确认模式」逐份审核移出名单；移出时**不会**把成员加入黑名单，被移出的成员可以随时重新申请入群，重新入群后重新开始计时。

### 三、第三方共享

**AI 对话功能（含 `/服务器QA`）：** 当用户使用该功能时，以下内容将被传输至 **DeepSeek**（api.deepseek.com）以生成回复：用户发送的**消息内容**；该群最近与机器人的对话（含发言人的昵称）和群聊总结；参与对话的成员的长期记忆。每次回复后，机器人还会把这一轮对话发给 DeepSeek，用于整理发言人的长期记忆。传输内容**不包含**成员标识或群组标识（member_openid、group_openid）。`/服务器QA` 只传输问题本身。请勿通过 AI 对话功能发送敏感或私密信息。DeepSeek 的数据处理方式适用其自身隐私政策。

> 若部署时未配置 DeepSeek 密钥，AI 对话与 `/服务器QA` 功能将自动停用，届时不存在任何第三方数据传输。

**群管理功能：** 有效发言的判断完全在本机器人服务器上完成，**不会**把群消息发送给任何第三方。提醒消息和移出操作通过 QQ 开放平台接口完成。

**其他功能：** Epic 免费游戏、Steam 免费游戏、Minecraft 服务器状态查询仅向对应服务发起公开数据请求，**不发送任何用户信息**。

除上述情形及法律法规要求外，我们不会向任何第三方提供、出售或交换用户信息。

### 四、存储与保留期限

- 群管理数据保存在本机器人服务器的本地数据库中。
- **消息元数据**：保存 **35 天**后自动删除。
- **提醒、请假、移出等操作记录**：保存 **180 天**后自动删除。
- **已离开或被移出群聊的成员**：其统计数据再保留 **180 天**（方便重新入群），之后自动删除。
- **组队游戏的队伍名单**：保存在服务器本地文件中，**每天清空一次**（默认北京时间 5:00）。
- **AI 对话记录**（@ 机器人的消息和它的回复）：保存 **7 天**后自动删除。删除前，尚未整理的内容会先并入群聊总结。
- **群聊总结**：保留到管理员发送 `/重置对话`，或本机器人被移出该群为止。
- **长期记忆**：保留到成员自己用 `/忘记` 删除、管理员按要求删除，或本机器人被移出该群为止；每人超过 12 条时，最早的一条会被自动删除。
- **本机器人被移出某个群时**：立即删除该群的全部群管理数据、队伍名单、AI 对话记录、群聊总结和长期记忆。
- 群组绑定、订阅状态、游戏状态、睡眠记录**仅保存在服务器内存中**，机器人重启后即全部清除；睡眠记录在记录后 **24 小时**自动删除。
- 运行日志保留期不超过 **30 天**，到期自动删除。
- **服务终止时**，我们将在 **24 小时内**删除全部用户数据，包括运行日志。

### 五、安全措施

访问凭证与 API 密钥独立存储于配置文件中，不写入代码仓库。服务器仅限授权人员访问。供管理员查看的成员活跃表网页默认只允许服务器本机访问；如需远程查看，必须设置访问口令。我们采取必要措施防止数据被窃取、泄露或非法使用。

### 六、内容合规

本机器人对 AI 生成内容及语录内容进行过滤，防止输出违法违规或不适宜信息。如发现不当内容，请通过下方方式联系我们。

### 七、用户权利

用户可随时：

- `@小莲 /我的状态` —— 查看本机器人记录的自己的活跃情况；
- `@小莲 /请假`、`/销假` —— 因考试、工作、健康等原因暂停计时；
- `@小莲 /我的记忆` —— 查看本机器人记下的关于自己的长期记忆；`/忘记 序号` 或 `/忘记 全部` 删除；
- `/mc解绑`、`epicTD` / `steamTD` —— 解除服务器绑定、取消推送订阅；
- 将机器人移出群聊 —— 停止该群的一切数据处理，并删除该群的全部群管理数据、队伍名单和 AI 对话数据。

如需删除您的相关数据或查询数据情况，请通过下方联系方式或群管理员与我们联系，我们将在 **7 个工作日**内处理。

### 八、未成年人保护

本机器人不定向收集未成年人信息。若您为未成年人，请在监护人指导下使用。

### 九、政策更新

本政策如有更新，我们将在机器人资料页公示。继续使用即视为接受更新后的政策。

### 十、联系我们

**联系邮箱：** ____________________

---

## English reference (not submitted)

**What changed from Castorice's policy:** the old text promised the bot "does not read or store
other chat messages". That is no longer true in groups where the manager is on, so section 一.3 now
splits managed and unmanaged groups.

**What is collected:**
- `group_openid` and `member_openid`, both platform-generated and unique per bot.
- QQ nicknames, which arrive with every message.
- For managed groups only:
  - per-message metadata: id, sender, time, valid or not, and the reason
  - the first 30 characters of each member's latest valid message
  - counters and streaks, badges, leave records, the exempt list, and the reminder and removal log
  - the member list (nickname, role, join time), if Tencent grants that invite-only API
- For team games, once enabled: today's rosters (captain and member IDs, nicknames, when each was
  grabbed, in-game statuses such as 小队长 or 死亡).
- For AI chat, once a DeepSeek key is set (`data/chat.db`):
  - messages that @ the bot and its replies, with speakers' nicknames
  - a rolling summary per group
  - up to 12 short memories per member, built only from what that member said about themselves
- Runtime logs.

**Explicitly not collected:** QQ numbers, phone numbers, passwords, ID documents, location,
contacts, payment info.

**Third parties:** only AI chat talks to DeepSeek. Each reply sends:
- the message
- the group's recent conversation with the bot, with nicknames, and its summary
- the memories of the members in that conversation

A second call after each reply sends that one exchange so memories can be noted. No openids are
sent. `/服务器QA` sends only the question. The classifier runs locally, so nothing from the
manager leaves the server except QQ API calls.

**Retention, and where each is enforced:**

| Data | Kept for | Enforced by |
|---|---|---|
| Message metadata | 35 days | `MESSAGE_RETENTION_DAYS` in `modules/manager/module.py` |
| Action log | 180 days | `ACTION_RETENTION_DAYS` |
| Departed members' stats | 180 days | `DEPARTED_RETENTION_DAYS` |
| Team rosters (`data/teams.json`) | until the daily reset (05:00 by default) | `reset_time` in `prompts/teams.yaml`, cleared by `TeamsModule.run_scheduler` |
| AI chat turns (`data/chat.db`) | 7 days, after being folded into the summary | `ai_chat.keep_days`, `AIChatModule.housekeeping` (hourly) |
| AI chat summary | until `/重置对话` or the bot leaves | `ChatStore.reset_chat` / `forget_group` |
| AI chat memories | until `/忘记`, `/删除数据` or the bot leaves; 12 per member, oldest dropped | `ai_chat.memories_per_member`, `AIChatModule.observe` |
| A group's data after the bot is removed | deleted immediately (manager data, rosters, AI chat) | `on_group_del_robot` in `main.py` |
| Deletion on request | when an admin runs it (also takes them off every team and deletes their AI chat turns and memories, and the group's summary) | `/删除数据 @某人` |
| botpy logs | 7 rotated files (< 30 days) | botpy's own `TimedRotatingFileHandler` |

### Before you submit, check these
1. **Fill in the blanks:** effective date and contact email (twice).
2. **Snippets:** if you set `manager.store_snippets: false`, delete the "前 30 个字" bullet.
3. **DeepSeek:** if you ship without an AI key, section 三 still reads correctly thanks to the
   note. Don't declare the AI commands in the self-test report.
4. **The member list:** the sentence about it only applies once Tencent whitelists the bot. It's
   worded conditionally, so it's accurate either way.
5. **Old logs:** this repo has none, but delete any copied over from the Castorice folder.
6. **AI chat numbers:** sections 一.6 and 四 say 7 days and 12 memories. If you change
   `ai_chat.keep_days` or `ai_chat.memories_per_member`, change them there too. With
   `ai_chat.memory: false`, delete the long-term memory sentences.
