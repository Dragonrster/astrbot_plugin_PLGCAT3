## astrbot_plugin_PLGCAT3

# MC 服务器管理插件

通过 QQ 聊天远程管理 Minecraft 服务器。基于 RCON 协议，支持白名单、封禁、聊天转发、签到经济等功能。

## 功能概览

| 功能 | 说明 |
|------|------|
| 白名单管理 | 管理员直接操作，玩家可自助申请绑定 |
| 封禁/踢人 | ban、pardon、kick、tempban |
| 服务器信息 | 在线玩家（Server List Ping）、插件列表、TPS、Spark 健康、实体列表 |
| MC↔QQ 聊天 | 游戏内 `.mcsay` 转发到 QQ，支持文件模式和 MCSM 面板模式 |
| 签到经济 | 每日签到领铜钱、查余额、玩家间转账 |
| RCON 透传 | `/mcrun` 执行任意控制台命令（带安全拦截） |

## 安装

将本仓库放入 AstrBot 的插件目录，在 AstrBot 管理面板中启用插件并填写配置。

## 配置

在 AstrBot 插件配置页面填写以下项：

### 基础连接

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `rcon_host` | RCON 地址 | `localhost` |
| `rcon_port` | RCON 端口 | `25575` |
| `rcon_password` | RCON 密码 | — |
| `mc_server_port` | MC 游戏端口（Server List Ping 用） | `25565` |
| `admin_qqs` | 管理员 QQ 号列表 | `[]` |
| `whitelist_command` | 白名单命令名（可改为 `swl` 等第三方插件命令） | `whitelist` |
| `mcrun_blocked_extra` | `/mcrun` 追加拦截词 | `[]` |

### MC→QQ 聊天转发

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `mc_chat_log_to_qq_enabled` | 启用聊天转发 | `false` |
| `mc_chat_source` | 消息来源：`file`（读日志）或 `mcsm`（MCSM 面板） | `file` |
| `mc_chat_log_path` | `latest.log` 绝对路径 | — |
| `mc_chat_trigger_prefix` | 游戏内触发前缀 | `.mcsay ` |
| `mc_chat_unified_msg_origin` | 转发目标会话 ID（可留空，用 `/mcbindchat` 绑定） | — |
| `mc_chat_qq_group_id` | QQ 群号（备用方案） | — |
| `mc_chat_log_tail_from_end` | 启动时跳过历史日志 | `true` |

**MCSM 模式**额外需要：`mcsm_panel_url`、`mcsm_api_key`、`mcsm_instance_uuid`、`mcsm_daemon_uuid`。

### 白名单申请

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `enable_apply_whitelist` | 允许玩家自助申请白名单 | `false` |
| `whitelist_verify_mode` | 验证方式：`code`（游戏内验证码）、`online`（需在线）、`none`（直接绑定） | `code` |

### 签到经济

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `enable_sign` | 启用签到功能 | `false` |
| `sign_money_min` / `sign_money_max` | 签到随机铜钱范围 | `10` / `100` |
| `sign_money_command` | 加钱 RCON 命令模板（`{name}`、`{amount}` 会被替换） | `d money add {name} {amount}` |
| `money_command_prefix` | 余额查询命令前缀 | `d money` |

## 命令列表

`[管]` 表示需要管理员权限。括号内为别名。

### 白名单

| 命令 | 说明 |
|------|------|
| `/mcwl <add\|remove\|list> [MC名]` | 白名单管理（mcwhitelist） |
| `/wantwl <MC名>` | 申请绑定（需私聊） |
| `/wantwllist` | 查看已绑定账号（wantwll） |
| `/wantwlunbind <MC名>` | 解绑（wantwlu） |

### 封禁

| 命令 | 说明 |
|------|------|
| `[管] /mcban <MC名> [原因]` | 封禁 |
| `[管] /mcunban <MC名>` | 解封（mcpardon） |
| `/mcbanlist` | 封禁列表（mcbl） |
| `[管] /mckick <MC名> [原因]` | 踢人（mck） |
| `[管] /mctempban <MC名> <时长> [原因]` | 临时封禁（mctb） |

### 服务器信息

| 命令 | 说明 |
|------|------|
| `/mclist` | 在线玩家列表（mcl） |
| `/mcplugins` | 插件列表 |
| `[管] /mctps` | 查看 TPS |
| `[管] /mcsparkhealth` | Spark 健康报告（mcspark） |
| `[管] /mcping [目标]` | ping（mcp） |
| `[管] /mcentitylist [选择器] [世界]` | Paper 实体列表（mcel） |

### 聊天

| 命令 | 说明 |
|------|------|
| `/mcsay <内容>` | 在游戏内以 tellraw 发送消息（mcs） |
| `[管] /mcbroadcast <内容>` | 管理员广播（mcb） |
| `[管] /mcbindchat` | 绑定当前会话为聊天转发目标 |

### 经济

| 命令 | 说明 |
|------|------|
| `/mcsign` | 每日签到领铜钱（mcqd） |
| `/mcmoney` | 查询铜钱余额（mcqian） |
| `/mctransfer <MC名> <数量>` | 转账（mczz） |

### 其他

| 命令 | 说明 |
|------|------|
| `[管] /mckill <MC名>` | 击杀玩家 |
| `[管] /mcrun <命令>` | RCON 透传（mcexec） |
| `[管] /mcauthunregister <MC名>` | AuthMe 注销 |
| `/mcmanhelp` | 帮助（mchelp） |

## 数据文件

插件数据存储在 AstrBot 插件数据目录下：

- `apply_whitelist.json` — QQ→MC 账号绑定记录
- `sign_data.json` — 签到记录
- `transfer_log.jsonl` — 转账审计日志
- `mc_chat_qq_target.json` — 聊天转发目标

## 支持

[帮助文档](https://astrbot.app)
