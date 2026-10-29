# 配置来源与凭据

## 两种来源，可逐次连接选择

| config_source | 服务器与网络 | 连接密码 |
|---|---|---|
| `official` | 当前 Windows 用户官方 RustDesk 配置的服务器、公钥、代理和连接相关网络选项 | 对应 peer 的已保存凭据；传 credential_ref 时用 MCP 凭据覆盖密码 |
| `profile` | MCP config.json 中指定 profile 的 server/server_key/force_relay | 同一个 profile 的 DPAPI 密文 |

`connect` 的 config_source 优先于本地默认值。省略时使用 config.json 的 config_source；
旧配置未设置时仍使用 profile，保持原行为。CLI `--config-source` 可覆盖进程默认值。
来源缺失、密码解不开或认证失败时明确报错，不自动换服务器或使用另一组凭据重试。

官方来源的 MCP 调用：

```json
{"peer_id":"<peer ID>","config_source":"official"}
```

官方服务器 + MCP 保存的密码：

```json
{"peer_id":"<peer ID>","config_source":"official","credential_ref":"test-peer"}
```

完全自定义：

```json
{"peer_id":"<peer ID>","config_source":"profile","credential_ref":"test-peer"}
```

以上是 `connect` 参数，不是完整 MCP JSON-RPC 消息。没有明文 password 工具参数。

## 设置默认来源

```powershell
# 官方模式不需要重复输入密码，不修改官方配置；保留已有 MCP profiles。
pwsh -NoProfile -File mcp/scripts/configure.ps1 -ConfigSource official

# 自定义模式，密码交互输入后由当前 Windows 用户 DPAPI 加密。
pwsh -NoProfile -File mcp/scripts/configure.ps1 -ConfigSource profile `
  -PeerId '<peer ID>' -Server '<server>' -ServerKey '<public key>' -CredentialRef test-peer

pwsh -NoProfile -File mcp/scripts/dev.ps1 rustdesk-mcp
```

官方默认目录为 `%APPDATA%/RustDesk/config/`，使用上游 ProjectDirs 规则。
若官方版使用其他目录，本地配置脚本可传 `-OfficialConfigDir '<config directory>'`；
这不是允许模型任意指定配置路径的工具参数。

## 全自动模式（auto_approve）

默认策略是只读；开启写策略后，自由命令、写入、传输、脚本/处置和显式管理员连接
仍需在本地确认页逐次人工批准。Agent 以 YOLO/全自动模式运行时，可让 MCP 同样全自动：

```powershell
# 安装交付包时一次性配置：写策略 + 自动批准，client-config.json 注入环境变量。
pwsh -NoProfile -File scripts/install.ps1 -Configure -AutoApprove

# 或事后改写本地配置：
pwsh -NoProfile -File mcp/scripts/configure.ps1 -ConfigSource official -AutoApprove
```

生效规则：

- config.json 的 `auto_approve` 和 `allow_writes` 必须是 JSON 布尔值；字符串 `"false"` 等错误类型返回 `invalid_config`。
- config.json 的 `auto_approve: true`，或进程环境变量 `RUSTDESK_MCP_AUTO_APPROVE=1`
  （`true/yes/on` 亦可），任一为真即启用；环境变量可写在 MCP 客户端注册的 `env` 字段里，
  与具体 Agent 的权限模式绑定。
- 启用后写策略视为开启，审批环节自动通过并记录 `source: auto_approve` 审计；
  本地 Web 页面仍提供状态监视、输出查看、取消和断开。
- 此模式下任何能调用本 MCP 的客户端都对远端拥有完整操作能力，
  只应在本机可信、Agent 已处于全自动授权时使用；关闭时须同时关闭配置值和环境变量并重启 MCP。
  保留 `allow_writes: true` 时恢复人工审批，否则恢复只读策略。

## 管理员终端与 UAC

安装版 RustDesk 服务运行在 SYSTEM/管理员身份，不代表默认终端也有管理员权限。
普通连接使用当前登录用户的 token；先核对 connect/status 的 system_info.administrator。
需要管理员操作时，一次配置 Windows 本地/域管理员凭据（不是 RustDesk 密码，也不是 Windows Hello PIN）：

```powershell
pwsh -NoProfile -File mcp/scripts/configure.ps1 -ConfigSource official -AutoApprove `
  -AdminPeerId '<peer ID>' -AdminCredentialRef admin-office -AdminUsername '<domain\user or local user>'
```

脚本交互输入 OS 密码并用当前本机用户 DPAPI 保存；重启 MCP 后，connect/status 的
admin_credential_refs 列出该 peer 可用的引用名称，不返回密码。
后续使用 `connect(peer_id=..., elevate=true, admin_credential_ref="admin-office")`，
已安装且正常运行的服务通过现有管理员认证通路创建管理员终端；auto_approve 下无需 MCP 人工审批，
同一管理员终端执行命令无需每次 RunAs/UAC。必须再次确认实际 administrator/identity/SID；可能是 SYSTEM，
此时 HKCU 和用户配置目录属于 SYSTEM，操作登录用户的配置需指定实际用户。
没有有效 OS 凭据时不能仅凭 RustDesk 密码进入此通路；Microsoft 在线账户暂不支持。

UAC 同意作用于创建的管理员进程，不会永久提升普通父终端。
一次同意后复用管理员进程可避免反复提示；进程退出/重启后的可用性取决于服务或计划任务。
已有服务 + 保存的有效管理员凭据满足后续无逐次 UAC 的需求，不需要关闭系统 UAC。

## PowerShell 7 下载与部署

`downloads.enabled` 默认 true；配置中省略 downloads 或 enabled 时也启用。
显式关闭的方法：

```json
{"downloads":{"enabled":false}}
```

enabled 必须是 JSON 布尔值，字符串 "false" 等返回 invalid_config。
这个开关同时控制受信 manifest 的 download_url/deploy_pwsh_portable，不允许任意 URL；
内置资产是固定版本、固定 SHA256 的官方 PowerShell ZIP。写策略和审批规则继续生效。
auto_approve 不覆盖显式 disabled；外部哈希情报 intel 仍默认关闭。
connect 不自动下载；模型收到提示，在目标缺少 pwsh 且具备写权限时优先部署便携版，
随后用部署 command_id 调用 exec_script 的 portable_deployment_command_id。
便携部署不改 PATH/注册表或默认终端，不要求管理员安装 MSI。

本机 MCP 的 Python wheel 是服务安装包，由本机 uv 安装/管理，再用该环境的 Python 运行，不要求目标机安装 Python。
远端确需 Python 脚本时，模型优先复用已有 uv 和 Python 环境，禁用自动下载新解释器；
MCP 自己的远端终端设置 UV_PYTHON_DOWNLOADS=never，不改目标机的全局环境变量。
没有可用解释器时报告缺失条件。脚本优先临时执行；需要 UAC 时尽量将一个已授权任务合并为一次临时提权，
确认执行结果后用现有 cleanup_artifacts 清理记录中的临时产物，不创建常驻管理员代理或开机任务。

## 读取哪些内容

- `RustDesk2.toml`：custom-rendezvous-server、key、socks、relay-server、
  allow-websocket、enable-webrtc、ice-servers、enable-ipv6-punch、relay-fallback-delay。
- `peers/<peer ID>.toml`：保存的连接密码和本 peer 连接选项；必要时识别该服务器的 qualified peer 文件名。
- 密码按官方保存格式解密为认证所需的 salted SHA-256 hash，只进入 Rust 连接的内存快照；
  不解出或返回明文密码，不交给 Python、模型、浏览器或日志。
- 读取文件直接反序列化，不调用会迁移/写回文件的官方 PeerConfig::load。
  连接需要的网络快照保存于 MCP 自己的命名空间；切换 profile 时清空上次导入的网络设置。
- 不复制官方本机 ID/私钥、服务身份、桌面 UI 偏好、API 登录 token、地址簿账户或自动更新设置。
  服务器 `key` 是 rendezvous 公钥，不是服务器管理 API token。

## 仍独立保存的内容

| 内容 | 位置/生命周期 |
|---|---|
| MCP 默认来源、exe 路径、写权限、DPAPI profiles | `%LOCALAPPDATA%/RustDeskMCP/config.json` |
| 命令状态、输出、审计 | 同目录 `operations.sqlite3`；验收可使用独立 `--data-dir` |
| Bridge 本机身份与网络配置 | `%APPDATA%/RustDeskMCP/config/` |
| Bridge 日志 | `%APPDATA%/RustDeskMCP/log/` |
| Peer 配置及认证 hash | 本次 bridge 连接的私有内存；不写回官方 peer 文件 |

`terminal-service-id`、`terminal-admin-service-id` 和 terminal_persistent 始终过滤。
每次 MCP 创建自己的非持久终端，不恢复官方终端，不在结束时用旧快照覆盖官方的新设置。

## 当前限制

- 官方必须已保存该 peer 的连接密码；过期的临时密码仍可能认证失败。
  缺失/不可解密返回 official_credential_unavailable，可显式指定 MCP credential_ref。
- 跨机器复制的密文、使用其他用户/服务账户的配置未保证可解密，失败不尝试绕过。
- 官方配置存在访问 PIN 时返回 official_application_locked；没有无头解锁流程，
  即使官方 UI 已解锁也不借用其解锁状态。
- 本版只支持数字 peer ID；默认最多 4 个独立连接，每个拥有一个非持久终端。显式管理员连接需要本地单独批准（auto_approve 模式下自动批准）和有效 OS 凭据。
- 不监视配置文件变化。下一次连接重新读取；现有连接采用连接时的快照。
