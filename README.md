## astrbot_plugin_PLGCAT3

# MC 服务器管理插件

通过 QQ 聊天远程管理 Minecraft 服务器。基于 RCON 协议，支持白名单、封禁、聊天转发、签到经济等功能。

## 功能概览

| 功能       | 说明                                                              |
| ---------- | ----------------------------------------------------------------- |
| 白名单管理 | 管理员直接操作，玩家可自助申请绑定                                |
| 封禁/踢人  | ban、pardon、kick、tempban                                        |
| 服务器信息 | 在线玩家（Server List Ping）、插件列表、TPS、Spark 健康、实体列表 |
| MC↔QQ 聊天 | 游戏内 `.mcsay` 转发到 QQ，支持文件模式和 MCSM 面板模式           |
| 签到经济   | 每日签到领铜钱、查余额、查功勋、玩家间转账、铜钱/补签卡补签        |
| RCON 透传  | `/mcrun` 执行任意控制台命令（带安全拦截）                         |

## 安装

将本仓库放入 AstrBot 的插件目录，在 AstrBot 管理面板中启用插件并填写配置。

## 配置

在 AstrBot 插件配置页面填写以下项：

### 基础连接

| 配置项                | 说明                                          | 默认值      |
| --------------------- | --------------------------------------------- | ----------- |
| `rcon_host`           | RCON 地址                                     | `localhost` |
| `rcon_port`           | RCON 端口                                     | `25575`     |
| `rcon_password`       | RCON 密码                                     | —           |
| `mc_server_port`      | MC 游戏端口（Server List Ping 用）            | `25565`     |
| `admin_qqs`           | 管理员 QQ 号列表                              | `[]`        |
| `whitelist_command`   | 白名单命令名（可改为 `swl` 等第三方插件命令） | `whitelist` |
| `mcrun_blocked_extra` | `/mcrun` 追加拦截词                           | `[]`        |

### MC→QQ 聊天转发

| 配置项                       | 说明                                             | 默认值    |
| ---------------------------- | ------------------------------------------------ | --------- |
| `mc_chat_log_to_qq_enabled`  | 启用聊天转发                                     | `false`   |
| `mc_chat_source`             | 消息来源：`file`（读日志）或 `mcsm`（MCSM 面板） | `file`    |
| `mc_chat_log_path`           | `latest.log` 绝对路径                            | —         |
| `mc_chat_trigger_prefix`     | 游戏内触发前缀                                   | `.mcsay ` |
| `mc_chat_unified_msg_origin` | 转发目标会话 ID（可留空，用 `/mcbindchat` 绑定） | —         |
| `mc_chat_qq_group_id`        | QQ 群号（备用方案）                              | —         |
| `mc_chat_log_tail_from_end`  | 启动时跳过历史日志                               | `true`    |

**MCSM 模式**额外需要：`mcsm_panel_url`、`mcsm_api_key`、`mcsm_instance_uuid`、`mcsm_daemon_uuid`。

### 白名单申请

| 配置项                   | 说明                                                                     | 默认值  |
| ------------------------ | ------------------------------------------------------------------------ | ------- |
| `enable_apply_whitelist` | 允许玩家自助申请白名单                                                   | `false` |
| `whitelist_verify_mode`  | 验证方式：`code`（游戏内验证码）、`online`（需在线）、`none`（直接绑定） | `code`  |

### 玩家统计（/mcstats）

| 配置项                                                                        | 说明                                                            | 默认值 |
| ----------------------------------------------------------------------------- | --------------------------------------------------------------- | ------ |
| `mc_stats_world_path`                                                         | 服务器 `world` 目录的绝对路径（如 `/mc_server/world`）；留空则通过 MCSM 面板读取 | 空     |
| `mcsm_panel_url` / `mcsm_api_key` / `mcsm_instance_uuid` / `mcsm_daemon_uuid`  | 走 MCSM 面板读取统计文件时需配置                                 | —      |

统计文件位置随游戏版本变化：**26.1 起**位于 `world/players/stats/<UUID>.json`；**26.0 及以前**位于 `world/stats/<UUID>.json`。插件会按顺序自动尝试两种位置（本地目录与 MCSM 面板均如此），`mc_stats_world_path` 始终填 `world` 目录本身即可，无需手动迁移。

### 签到经济

| 配置项                              | 说明                                                                  | 默认值                        |
| ----------------------------------- | --------------------------------------------------------------------- | ----------------------------- |
| `enable_sign`                       | 启用签到功能                                                          | `false`                       |
| `sign_money_min` / `sign_money_max` | 签到随机铜钱范围                                                      | `10` / `100`                  |
| `sign_money_command`                | 加钱 RCON 命令模板（`{name}`、`{amount}` 会被替换）                   | `d money add {name} {amount}` |
| `money_command_prefix`              | 余额查询命令前缀                                                      | `d money`                     |
| `honor_command_prefix`              | 功勋查询命令前缀                                                      | `dbc honor`                   |
| `sign_backfill_cost_per_day`        | 补签基础费用                                                          | `50`                          |
| `sign_backfill_step`                | 补签每往前一天递增的费用（第N天前 = 基础 + (N-1)×步进）               | `100`                         |
| `sign_streak_bonus`                 | 连续签到加成（连续N天 = N×该值，封顶 2000）                           | `20`                          |
| `sign_card_auto_use`                | 持有补签卡时补签自动免铜（关闭后需显式发送 `卡`）                     | `true`                        |
| `sign_card_max`                     | 单个玩家可持有的补签卡上限                                            | `99`                          |
| `silent_sign`                       | 静默：签到（/mcsign）不回复                                           | `false`                       |
| `silent_sign_cal`                   | 静默：签到日历（/mcsigncal）不回复                                    | `false`                       |
| `silent_sign_backfill`              | 静默：补签（/mcsignback）不回复                                       | `false`                       |
| `silent_card`                       | 静默：补签卡（/mccard）不回复                                         | `false`                       |
| `silent_money`                      | 静默：余额查询（/mcmoney）不回复                                      | `false`                       |
| `silent_honor`                      | 静默：功勋查询（/mchonor）不回复                                      | `false`                       |
| `silent_mcid`                       | 静默：绑定查询（/mcid）不回复                                         | `false`                       |
| `sign_cal_font_path`                | 签到日历自定义字体路径（留空自动检测）                                | —                             |

**静默模式（防刷屏）**：`silent_*` 系列开关打开后，对应命令**照常执行**（照常发铜、记录签到、扣款、消耗补签卡、发放补签卡），只是**不发送任何回复**（成功、失败提示都不发）。适合人多的群屏蔽每日签到、补签等刷屏消息。

## 命令列表

`[管]` 表示需要管理员权限。括号内为别名。

### 白名单

| 命令                                 | 说明                      |
| ------------------------------------ | ------------------------- |
| `/mcwl <add\|remove\|list> [游戏ID]` | 白名单管理（mcwhitelist） |
| `/wantwl <游戏ID>`                   | 申请绑定（需私聊）        |
| `/wantwllist`                        | 查看已绑定账号（wantwll） |
| `/wantwlunbind <游戏ID>`             | 解绑（wantwlu）           |
| `/mcid [游戏ID]`                     | 按游戏ID反查绑定的QQ（查绑定；不带参数看自己的绑定；查他人需管理员） |

### 封禁

| 命令                                     | 说明             |
| ---------------------------------------- | ---------------- |
| `[管] /mcban <游戏ID> [原因]`            | 封禁             |
| `[管] /mcunban <游戏ID>`                 | 解封（mcpardon） |
| `/mcbanlist`                             | 封禁列表（mcbl） |
| `[管] /mckick <游戏ID> [原因]`           | 踢人（mck）      |
| `[管] /mctempban <游戏ID> <时长> [原因]` | 临时封禁（mctb） |

### 服务器信息

| 命令                                 | 说明                      |
| ------------------------------------ | ------------------------- |
| `/mclist`                            | 在线玩家列表（mcl）       |
| `/mcstats [游戏ID\|@某人]`           | 玩家统计（mcstat）        |
| `/mcplugins`                         | 插件列表                  |
| `[管] /mctps`                        | 查看 TPS                  |
| `[管] /mcsparkhealth`                | Spark 健康报告（mcspark） |
| `[管] /mcping [目标]`                | ping（mcp）               |
| `[管] /mcentitylist [选择器] [世界]` | Paper 实体列表（mcel）    |

### 聊天

| 命令                       | 说明                               |
| -------------------------- | ---------------------------------- |
| `/mcsay <内容>`            | 在游戏内以 tellraw 发送消息（mcs） |
| `[管] /mcbroadcast <内容>` | 管理员广播（mcb）                  |
| `[管] /mcbindchat`         | 绑定当前会话为聊天转发目标         |

### 经济

| 命令                                  | 说明                                                             |
| ------------------------------------- | ---------------------------------------------------------------- |
| `/mcsign`                             | 每日签到领铜钱（mcqd）                                           |
| `/mcsigncal`                          | 查看签到日历图片（mcsigncalendar）                               |
| `/mcsignback [日期] [卡\|铜]`         | 补签：默认花铜，持卡自动免铜（mcbq）                             |
| `/mccard`                             | 查看自己持有的补签卡（mcsigncard、补签卡、bqk）                  |
| `[管] /mccard give <QQ\|@某人\|游戏ID> <数量>`| 发放补签卡（游戏ID会反查绑定的QQ）                        |
| `[管] /mccard giveall <数量>`         | 一键发放给所有已绑定 MC 账号的人                                 |
| `[管] /mccard take <QQ\|@某人\|游戏ID> <数量\|all>` | 回收补签卡                                         |
| `[管] /mccard list`                   | 补签卡持有人列表                                                 |
| `/mcid [游戏ID]`                      | 按游戏ID反查绑定的QQ（查绑定；不带参数看自己的绑定；查他人需管理员） |
| `/mcmoney`                            | 查询铜钱余额（mcqian）                                           |
| `/mchonor [游戏ID\|@某人]`            | 查询功勋（功勋、honor；指定 MC 名查他人需管理员）                |
| `/mctransfer <游戏ID> <数量>`         | 转账（mczz）                                                     |

**功勋查询**：`/mchonor`（或 `/功勋`）会查询你自己已绑定 MC 账号的功勋；管理员可用 `/mchonor <游戏ID>` 或 `/mchonor @某人` 查他人。实际执行的服务端命令为 `<honor_command_prefix> get <游戏ID>`（默认 `dbc honor get <游戏ID>`），并把服务端返回的功勋文本原样展示。

**绑定反查（游戏ID → QQ）**：`/mcid <游戏ID>`（或 `/查绑定`、`/mcbind`）会反查绑定该游戏ID的QQ，并显示是主账号还是副账号、同一QQ的其它绑定；管理员查询时还会带上签到天数与补签卡数量。不带参数时显示自己绑定的所有MC账号。普通玩家只能查自己的游戏ID，查他人需要管理员。管理员发放补签卡时可直接写游戏ID，例如 `/mccard give Steve 2`，无需先查QQ号。

**补签卡**：由管理员发放，玩家补签时免铜（不扣铜钱）。

- `/mcsignback 卡` — 用补签卡补最近漏签的一天
- `/mcsignback 2026-08-10 卡` — 用补签卡补指定日期
- `/mcsignback 2026-08-10 铜` — 不用卡，改花铜钱补签（或 `sign_card_auto_use` 关闭后显式加 `卡` 才用卡）
- 补签卡不改变 30 天补签窗口，也不能补已经签到的日期；用卡失败不消耗卡片

### 其他

| 命令                              | 说明                |
| --------------------------------- | ------------------- |
| `[管] /mckill <游戏ID>`           | 击杀玩家            |
| `[管] /mcrun <命令>`              | RCON 透传（mcexec） |
| `[管] /mcauthunregister <游戏ID>` | AuthMe 注销         |
| `/mcmanhelp`                      | 帮助（mchelp）      |

## 数据文件

插件数据存储在 AstrBot 插件数据目录下：

- `apply_whitelist.json` — QQ→MC 账号绑定记录
- `sign_data.json` — 签到记录
- `sign_admin.json` — 签到数据管理员覆盖
- `sign_cards.json` — 玩家持有的补签卡数量
- `sign_card_log.jsonl` — 补签卡发放/回收/使用审计日志（`give` / `giveall` / `take` / `use` / `refund`）

> `sign_cards.json` 采用「先写临时文件再原子替换」，读不出来时会先改名备份成
> `sign_cards.json.bad-<时间戳>` 再按空库存启动，不会直接覆盖玩家已有的卡；
> 需要人工恢复时对照 `sign_card_log.jsonl` 即可。
- `transfer_log.jsonl` — 转账审计日志
- `mc_chat_qq_target.json` — 聊天转发目标

## 支持

[帮助文档](https://astrbot.app)
