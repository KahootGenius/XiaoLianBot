# 机器人自测报告 / Self-Test Report

> 提交至 QQ 开放平台审核时使用。审核为**人工审核**：审核员会逐条输入下表指令，
> 对照「预期返回」核验实际回复。**描述与实际不符是最常见的驳回原因**，请勿凭记忆填写。
>
> 下表「预期返回」均为在本机代码上实际运行捕获的真实输出，非手写。

**机器人名称：** ____________________
**AppID：** ____________________
**测试场景：** 群聊（需 @ 机器人触发）
**测试日期：** ____年__月__日

---

## 一、指令列表

### 无需外部依赖（离线可测，审核必过项）

| # | 指令 | 预期返回 | 备注 |
|---|---|---|---|
| 0 | `/帮助` | 分组列出全部可用指令的文本<br>别名：`/菜单`、`/help`、`/指令` | 未配置 AI 时自动隐藏 AI 相关指令；未开启群管理时隐藏【群管理】 |
| 1 | `/21点` | `游戏开始！`<br>`你的手牌: X (点数: N)`<br>`机器人的明牌: Y`<br>`你可以输入 /抽卡 继续要牌，或输入 /停止 停止要牌` | 牌面随机，格式固定 |
| 2 | `/抽卡` | 游戏中：`你的手牌: … (点数: N)` + 机器人明牌；爆牌时追加 `爆牌了！你输了！`<br>无游戏时：`当前没有进行中的游戏，请先输入 /21点 开始游戏。` | 需先执行 `/21点` |
| 3 | `/停止` | 游戏中：`游戏结束！` + 双方手牌与点数 + 胜负结果<br>无游戏时：`当前没有进行中的游戏，请先输入 /21点 开始游戏。` | 需先执行 `/21点` |
| 4 | `/mc绑定 <域名或IP:端口>` | `已绑定Minecraft服务器` | 缺参数时返回 `请提供服务器地址，格式：/mc绑定 域名或IP:端口` |
| 5 | `/mc解绑` | 已绑定：`已解除 Minecraft 服务器绑定`<br>未绑定：`当前群组未绑定任何服务器` | |
| 6 | `/晚安` | `晚安！现在是HH:MM，祝你好梦~ 🌙` | 记录入睡时间 |
| 7 | `/早安` | `早安！现在是HH:MM。`<br>`你睡了N小时M分钟。`<br>+ 一句提示语<br>未记录时：`你还没有记录睡眠时间呢，请先使用 /晚安 指令~` | 需先执行 `/晚安` |
| 8 | `/发癫 <名称>` | 随机语录，其中占位符替换为所给名称 | 缺参数时返回 `请输入对象名称，例如：/发癫 小明` |
| 9 | `/摇骰子` | `你投出了: N 点` （N 为 1–6） | |
| 10 | `/我的ID` | `你的 member_openid：` + 换行 + 一串标识 + `（要设为管理员，把它填进 config.yaml 的 manager.admins）` | 任何群可用 |
| 11 | `/群ID` | `本群的 group_openid：` + 换行 + 一串标识 + `（要开启群管理，把它填进 config.yaml 的 manager.groups）` | 任何群可用 |

### 群管理指令（仅在开启群管理的群生效）

在**未开启**群管理的群里（包括审核用的测试群，除非把它加入 `manager.groups`），下列指令统一返回：
`这个群还没有开启群管理哦～（管理员可以把 /群ID 的结果填进 config.yaml 的 manager.groups）`

| # | 指令 | 开启群管理的群中的预期返回 |
|---|---|---|
| 12 | `/群规` | `📜 群规` + 群规全文 + 有效发言的判断标准 + `查看自己的情况：@我 /我的状态` |
| 13 | `/我的状态` | `📋 昵称 的活跃卡片` + 最近有效发言 / 连续发言 / 近 7 天有效发言 / 累计有效发言 · 成就 / 状态 + 一句小莲的话<br>例：`状态：计时从今天开始，距离提醒还有 25 天` |
| 14 | `/排行` | `🏆 近 7 天有效发言榜` + 前 10 名（`/排行 月` 为 30 天）<br>无人上榜时：`这周大家都好安静……小莲有点寂寞。` |
| 15 | `/成就` | `🎖️ 昵称 的成就（N/11）` + 已解锁成就 + `还没解锁：…` |
| 16 | `/请假 30 期末考试` | `收到～昵称 的假期到 M月D日，这段时间小莲不会催你。祝一切顺利！`<br>缺天数：`用法：/请假 天数 原因，例如「/请假 30 期末考试」（单次最长 90 天）`<br>超过 90 天：`单次请假最长 90 天哦，到期后可以再续～` |
| 17 | `/销假` | 请假中：`欢迎回来！假期结束啦，计时从今天重新开始～`<br>未请假：`你现在没有在请假哦～` |
| 18 | 管理员指令（`/管理状态` 等） | 非管理员：`这个指令只有管理员能用哦～` |

### 互动（任何群可用，无需外部依赖）

| # | 指令 | 预期返回 |
|---|---|---|
| 18d | `/今日运势` | `🔮 昵称 的今日运势 · M月D日` + 运势、宜、忌和一句点评（同一天结果相同） |
| 18e | `/生日 10-25` | `记住啦！你的生日是 10月25日……`；格式不对时：`生日写成「月-日」哦，例如：/生日 10-25` |
| 18f | `/投票 周末去哪 公园 商场` | `📊 周末去哪（发起：昵称，已有 0 票）` + `A. 公园 · 0 票` 等选项 |
| 18g | `/投 A` | `昵称 投给了 A. 公园（现在共 1 票）`；没有投票时：`现在没有进行中的投票……` |
| 18h | `/翻牌子` | 未开启群管理的群：`这个群没开群管理……@ 几个人，我从里面帮你翻` |

### 组队游戏（仅在 config.yaml 配置了队长时生效；未配置时请勿申报）

| # | 指令 | 预期返回 |
|---|---|---|
| 18a | `/队伍` | 没有队伍时：`今天还没有队伍哦～队长 @我 /抓 @某人 就能开始组队。`<br>有队伍时：`🏳️ 今日队伍 · M月D日` + 每支队伍一行 |
| 18b | `/抓 @某人`（非队长发送） | `只有队长才能用 /抓 哦～` |
| 18c | `/清空队伍`（非管理员发送） | `这个指令只有管理员能用哦～` |

### 依赖外部网络（审核前请自行确认可用）

| # | 指令 | 预期返回 | 依赖 |
|---|---|---|---|
| 19 | `/mc状态` | 已绑定且在线：`服务器状态：在线` + 延迟 + 在线人数 + 玩家列表<br>未绑定：`请先使用 /mc绑定 命令绑定服务器地址`<br>无法连接：`无法连接到服务器，请检查服务器地址是否正确或服务器是否在线` | 目标 MC 服务器 |
| 20 | `/epicfree` | `Epic免费游戏推送` + `当前免费：` / `即将推出：` 分组列表 | Epic 商店 API |
| 21 | `订阅epic` | `已订阅Epic免费游戏推送，将在每天中午12点推送信息。` | |
| 22 | `epicTD` | `已取消订阅Epic免费游戏推送。` | |
| 23 | `/steamfree` | 先回 `正在获取Steam免费游戏信息，请稍候...`，随后返回游戏列表或失败说明 | Steam 商店页面 |
| 24 | `订阅steam` | `已订阅Steam免费游戏推送，将在每天下午6点推送信息。` | |
| 25 | `steamTD` | `已取消订阅Steam免费游戏推送。` | |

### 依赖 DeepSeek 密钥 —— 未配置密钥时请勿申报以下指令

| # | 指令 | 预期返回 | 依赖 |
|---|---|---|---|
| 26 | `/服务器QA <问题>` | 针对 Minecraft 服务器问题的 AI 回答<br>无问题时：`请在指令后提供您的问题，例如：/服务器QA 如何重启服务器？` | DeepSeek API |
| 27 | 其他任意 @ 消息 | 以「小莲」人设生成的 AI 回复 | DeepSeek API |
| 28 | `/deepseek` | 发送一张图片 | 需富媒体权限 |
| 29 | `/我的记忆` | 没有记忆时：`小莲还没有记下关于你的事哦～多聊聊就会记住啦。`<br>有记忆时：`📒 小莲记得的关于 昵称 的事（N 条）：` + 编号列表 + 删除方法 | 记忆由 AI 整理 |
| 30 | `/忘记 序号` / `/忘记 全部` | `好，第 N 条已经忘掉了。` / `好，关于你的 N 条记忆都删掉了。`<br>缺参数：`用法：/忘记 序号（序号见 /我的记忆），或 /忘记 全部` | 不调用 API |

---

## 二、特殊说明（审核方要求主动申报）

- **触发方式**：所有指令均需在群聊中 **@ 机器人** 后发送。
- **群管理功能**：仅在管理员通过配置开启、并经群主授权「接收群内全部消息」的群中生效。机器人读取群消息只为判断「有效发言」，不保存聊天记录（详见隐私政策）。会在群内发送第 25 天的 @ 提醒、成就祝贺和每周小报；移出成员需群主授权相应管理能力，且默认需管理员确认。
- **AI 对话的上下文与记忆**：同一个群里 @ 机器人的对话会保存 7 天作为上下文，更早的整理成群聊总结；每次回复后会再调用一次 DeepSeek，根据发言人亲口说的关于自己的事整理长期记忆（不记敏感信息）。成员可用 `/我的记忆` 查看、`/忘记` 删除。
- **第三方服务**：AI 对话与 `/服务器QA` 调用 DeepSeek API；Epic / Steam / Minecraft 查询调用对应公开接口。群管理功能不调用任何第三方服务。
- **频率限制**：Steam 查询含最多 5 次重试（指数退避）。定时任务：每日 Epic 12:00、Steam 18:00 推送；群管理每日 20:00 检查一次；每周日 21:00 发送小报；成就祝贺每群每小时最多 3 条。
- **富媒体权限**：`/deepseek` 使用 `post_group_file` 上传图片，需开通相应权限。
- **组队游戏**：仅在管理员于配置中指定队长后生效。队长 @ 机器人并 @ 群友，把群友加入自己的队伍并设置游戏内状态；队伍每天北京时间 5:00 自动清空。不调用任何第三方服务。
- **状态依赖指令**：`/抽卡`、`/停止` 依赖 `/21点`；`/早安` 依赖 `/晚安`；`/mc状态` 依赖 `/mc绑定`；`/销假` 依赖 `/请假`；队长操作（`/踢出` 等）依赖 `/抓`。
  上述指令在前置条件未满足时**均会返回明确提示**，不会无响应。

---

## English reference (not submitted)

### Why this report is structured in tiers

The reviewer types each command and compares the reply with your declared output. Anything that can
fail for an environmental reason is a risk you should control before submitting:

- **Tier 1 (offline)** always works. These carry the review.
- **Group-manager commands** answer in the reviewer's group, but with the "not enabled" message,
  unless that group is listed in `manager.groups`. The table declares both replies, so either is
  accurate.
- **Network tier** depends on Epic, Steam or your MC server being reachable when the reviewer
  tests. `/steamfree` is the shakiest.
- **DeepSeek tier** returns nothing at all without a configured key. **Do not declare commands
  26–28 unless your key is live and funded.**

### Recommendations before you submit

1. **Consider dropping `/steamfree` from the declared list on the first submission.** It scrapes
   HTML that Valve can change at any time, and you cannot resubmit while a review is pending.
2. **Decide on the AI commands now.** Without a DeepSeek key, declaring `/服务器QA` is a guaranteed
   rejection.
3. **`/deepseek` needs the rich-media permission** *and* hotlinks a miHoYo CDN image that may have
   rotted. Load the URL in a browser before declaring it.
4. **Bot name must be ≥2 characters and reflect actual function.** 小莲 now also does group
   management, so mention it in the bot's description.
5. **Re-capture rows 12–17** in your managed group before submitting. Their content depends on the
   member's activity, so the table describes the format rather than fixed text.
