# PRD：RustDesk MCP 客户端（代号 mcp-bridge）

版本：v0.4 完整交付补充 · 2026-10-07
仓库：lNeverl/rustdesk-mcp（fork 自 rustdesk/rustdesk）· 工作分支：mcp-dev
核对基线：本地提交 `fada664df`，本地 tag `1.5.0`；0.2.0 已实现 40 个工具、官方文件通道、独立多会话与 Web。
本 PRD 的 P0/P1/P2 表示需求阶段；本版实际契约见 [交付手册](delivery-guide.md) / [API](api-reference.md)，
实测与凭据相关未验收条件见 [验证记录](validation-record.md)。

本次评估结论：复用官方连接/终端/文件通道、Python 独立工具层、本地 Web
监视台的方向合理。优先改善接口闭环、失败语义和上游适配边界。
源码中“存在接口”与“无头模式已跑通”必须区分，实际验收分别记录。
用户补充：先完成 exe 和 MCP 再联调，代码可并行；本次交付独立无 UI exe，不替换官方 GUI。
2026-10-07 补充：配置来源可选 official/profile，可只读复用官方服务器与已保存 peer 凭据，
也可使用独立 MCP profiles；身份、终端状态、日志与审计独立，不写回官方配置。
具体用法见 [configuration.md](configuration.md)，开发状态见 [validation-record.md](validation-record.md)。
常用接口与 Kimi Code 源码调查见 [interface-review.md](interface-review.md)。

---

## 1. 背景与目标

让本地 Agent 通过 MCP 使用官方 RustDesk 通道操作远端 Windows，
用于系统诊断、恶意软件排查、文件/脚本操作、软件安装卸载及远程开发。

产品范围：

- 首期主控端 Windows x64，被控端 Windows；优先验证官方安装版。
  其他平台、便携版、其他架构仅在验证后列入支持矩阵。
- 默认只读诊断，管理员连接与写操作分别显式授权。
  不绕过 RustDesk 的认证、权限、远端确认、UAC 或安全策略。
- 默认使用已有 PowerShell；不自动安装/升级工具。
  opt-in 便携 PS7 是唯一预先设计的 shell 部署增强。
- 本期不提供桌面点击/键鼠、交互式 TUI、任意后台进程托管、
  无凭据提权或离线恢复。安装器需具备非交互参数。
- 恶意软件处置记录目标、修改与结果；远端可能已被攻陷，
  不能把其输出或扫描成功当成可信取证/彻底清除保证。

### 技术依据与待验证前提

使用路径与符号定位，不依赖会随 merge 漂移的行号。

| 项 | 已从本地源码确认 | 仍需实测 |
|---|---|---|
| 终端控制 | `Session<T: InvokeUiSession>` 的 open_terminal/send_terminal_input/close_terminal；`src/ui_session_interface.rs` | 无头登录、连接状态、终端开启及错误闭环 |
| CLI/发行包入口 | `--terminal` 走 Flutter 窗口；Windows runner 调用 core_main 后决定是否启动 UI | `--mcp-bridge` 能驻留且不启动窗口；发行包 stdout/stdin 重定向可用 |
| 回调适配 | InvokeUiSession 可实现独立适配器；官方 FlutterHandler 处理 TerminalResponse | 必需认证/权限/错误回调清单，哪些可安全 no-op |
| Windows 权限 | connection.rs 的 fill_terminal_user_token/handle_administrator_check 验证管理员账户；SelfUser 使用服务/当前进程身份 | 安装服务运行时能否得到 SYSTEM；便携版/UAC、锁屏/登录状态、实际 SID |
| Shell | terminal_helper.rs 的 get_default_shell 探测若干 pwsh 路径、PS5.1，最后 cmd；初始化 UTF-8 | 对端版本差异、中文/换行/ANSI、profile、自定义路径和实际可执行性 |
| 文件通道 | ConnType::FILE_TRANSFER 与 `libs/base/src/fs.rs` 的 TransferJob | 无 UI 确认回调、覆盖/取消、目录传输、文件通道的权限与目标目录访问 |
| 本地凭据/会话配置 | client.rs 的 handle_hash 可使用 PeerConfig 密码；client/io_loop.rs 在终端 opened 时经 set_option 写回 service_id | 缓存/应用锁定/认证挑战；MCP 不读取或覆盖用户终端恢复标识及持久设置 |
| 自建服务器 | `src/custom_server.rs` 支持文件名 host/key 等现有配置机制 | 解包后的 exe 与 bridge 使用正确配置、服务器和公钥 |

`enable-terminal` 的空配置按通用 enable 规则视为启用，但策略和连接权限可覆盖；
默认值不能替代运行时能力检查。支持哪一个官方被控端版本由 M1a 实测记录，
不以“官方最新版一定可用”作为兼容性承诺。

---

## 2. 总体架构与职责

```
本地 Agent / MCP 客户端
   │ MCP stdio
B：Python MCP Server（mcp/server/，uv）
   ├─ 工具、PowerShell 编排、授权、审计、命令结果
   └─ 本地 Web 确认页/监视台（浏览器可多开）
   │ JSON-RPC 2.0，127.0.0.1 随机端口，进程级 token
A：RustDesk --mcp-bridge（src/mcp_bridge/）
   └─ 独立 Session 适配器，复用官方连接/终端/文件代码
   │ 官方 RustDesk 协议
官方被控端 → PTY / PowerShell / 文件传输
```

A、B 在同一仓库便于版本追踪，但独立构建和发布。
B 按配置的绝对路径启动已验证的 bridge exe，不依赖当前目录/PATH 找到正确版本。
B 高频改动；A 随上游接口演进仍需适配，不能承诺“稳定后永远不动”。

### 项目 A：连接与通道原语

| 任务 | 职责 |
|---|---|
| 入口/IPC | 无头入口、启动握手、鉴权、协议版本/能力、进程退出 |
| 连接 | connect/disconnect/status；官方登录路径、权限/错误状态 |
| 终端 | open/write/resize/close，开启/输出/关闭/错误事件 |
| 文件 | P1 接入官方 upload/download/list/remove/rename/mkdir 及任务进度/取消 |
| 事件 | 转换官方响应；必要解压后输出 base64 数据；有界队列、事件顺序/缺失报告 |

A 不解析 PowerShell 命令、哨兵、镜像、runbook，不存业务审计与命令输出历史。
认证、权限、PeerInfo、terminal opened/error/closed 等回调不能一律 no-op；
只对本功能不使用的画面/音频等回调使用最小空实现。
未经授权不继续不安全连接、远端认证挑战或提权。

### 项目 B：AI 工具与策略

| 任务 | 职责 |
|---|---|
| MCP 工具 | 输入/输出 schema、工具注册、错误与客户端兼容 |
| 执行引擎 | shell 探测、命令包装/哨兵解析、串行执行、等待/取消、输出续读 |
| 业务工具 | 文件读写/编辑/搜索、结构化诊断、处置 runbook |
| 部署编排 | 下载来源、健康检查、可信哈希、便携 PS7、远端目录/清理 |
| 授权/凭据 | 强制策略、人工确认、credential_ref、受限存储 |
| 审计/监视 | 追加审计、operation_id 记录、Python 本地确认页与实时 Web UI |

**与 v0.1 的关键调整：exec 分帧、输出缓冲与命令超时迁至 B。**
这些依赖 PowerShell 版本与 AI 调用习惯，属于执行策略。
Rust 保留 terminal I/O，可避免后续加一个工具就改一次 fork。
不为复用增加新的共享 trait，不把所有官方 UI 统一改成新的抽象。

### bridge-protocol v1 必须明确的内容

M1b 产出 `docs/bridge-protocol.md`，先确定 P0 传输与原语契约；
P1 文件能力通过新增 capability/方法扩展，不能提前冻结未经验证的全部接口。

- B 启动子进程，A 绑定 `127.0.0.1:0`，经私有 stdout 管道发送一条 ready JSON：
  协议版本、实际端口、随机 token、instance_id、构建版本、capabilities。
  token 每次进程启动更换，只在内存使用；不使用全局固定 bridge.token 文件。
  token 用于本进程生命周期内的握手鉴权，并非每请求消耗一次。
- TCP 使用 UTF-8、换行分帧 JSON-RPC；换行在字符串内转义；
  明确请求/事件最大尺寸、鉴权期限、未知方法与畸形帧处理。
  未鉴权不能创建会话或获取事件；错误/日志不得回显 token 和凭据。
- 一个 B 独占一个 A 控制连接；监视窗口都连 B。
  A 的 stdin 管道用于感知父进程 EOF，控制连接关闭也触发退出。
  B 正常退出先关会话再关进程；强制退出用 Windows Job Object 等已验证方式兜底。
  这些行为必须在实际 Windows 构建/解包产物上验证。
- 握手/connect 等进程级请求没有 session_id；会话请求和事件带 session_id，
  终端事件带 terminal_id，文件事件带 transfer_id。
  请求响应与异步事件可交错，B 按 JSON-RPC id 匹配。
- write 返回表示已接收/提交本地通道，不表示远端命令已完成或甚至已开始；
  断线时允许结果不确定。不得用 RPC 请求 id 代替业务 operation_id。
- 有界事件队列；本地队列满或解析流缺失时中断并报告 stream_lost，
  不悄悄丢失哨兵后继续执行。Web 慢窗口不反向阻塞 A→B 解析。
- 区分 bridge 协议 major/minor 与官方 RustDesk 版本；
  major 不兼容即启动失败，minor 仅兼容扩展。缺能力明确报 unsupported，
  未识别事件忽略/记录，未识别请求拒绝。
- 分类错误至少包含认证/凭据不可用、peer offline、权限拒绝、
  unsupported terminal/version/shell、busy、timeout、stream_lost、
  session_closed、结果 unknown。保留可脱敏的官方原始错误供排查。

A 的日志使用既有日志渠道/stderr；B 的 stdout 仅发 MCP 消息。
监视台 URL、uv 启动提示、bridge ready 内容不能透传到 B stdout。
MCP stdio 要求依据：[MCP Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)。

---

## 3. 功能需求

### 3.1 MCP 工具与执行契约

#### P0：可靠远程执行，7 个工具

| 工具 | 功能 |
|---|---|
| `connect` | peer_id、credential_ref、可选 admin_credential_ref、elevate=false；返回会话、实际身份与能力 |
| `disconnect` | 关闭本项目拥有的终端/会话，重复调用安全；返回清理确认状态 |
| `status` | 连接/终端、peer、实际权限、shell/cwd 快照、当前 command_id、策略与能力 |
| `exec_command` | PowerShell 命令、operation_id、cwd、等待期限/执行期限；返回 command_id 与当前结果 |
| `get_command_output` | 查询状态、分页/有限等待，取回后续输出与结果 |
| `cancel_command` | 请求中断/关闭本项目终端；返回是否已确认停止 |
| `get_system_info` | 受控只读探针返回 OS/build/架构、shell、实际身份及可用工具 |

P0 一个 peer、一个本项目拥有的终端，不能接管用户正在使用的终端或持久 service。
身份通过远端探针确认；管理员凭据有效不等于固定获得 SYSTEM。
凭据由 config_source 显式选择本地 credential_ref 或官方已保存 peer 凭据，不进入模型参数；
来源不可用时失败，不自动回退。official 可用 credential_ref 覆盖密码，仍使用官方服务器。
本期 DPAPI + ACL 受限 config.json；服务器地址/公钥与 peer ID 同样归本项目管理。
0.2.0 显式 elevate 先进入连接级人工批准；批准前不发送管理员认证。管理员成功路径需实际 OS 凭据验收。

独立连接不等于独立配置：create_login_msg 会读已保存的 terminal service_id，
官方接收循环在 opened 回调前又会把 service_id 写回 PeerConfig。
M1a 必须验证新建非持久终端、避免恢复用户 service、避免覆盖其终端配置；
仅在适配器回调里处理输出不够。优先在 bridge 内隔离配置访问；确有必要时
允许调用链上的最小 MCP 专属 hook，列入改动清单，不改其他会话的行为。
不能在结束时写回旧快照来恢复配置，这会覆盖同时发生的用户修改。

**命令生命周期与并发**

- 一条命令对应一个 command_id，预留状态：
  pending_approval/running/completed/failed/cancelling/cancelled/unknown。
  同一 PTY 只执行一条命令；其他执行请求直接 busy，P0 不做隐式排队。
- wait_timeout_s 默认 10 秒，范围 0–30 秒；到期返回 running + command_id，
  不停止、不重发。后续通过 get_command_output 查询。
  execution_timeout_s 默认 300 秒，范围 1–3600 秒；到期触发取消流程。
  更长上限是管理员配置，先查询、再决定，不能把超时自动改成后台继续执行。
- 取消先尝试已验证的终端中断；不能确认时关闭本项目拥有的终端并使其失效。
  已执行的修改不会回滚，也不保证所有脱离终端的子进程已结束。
  仅收到 Ctrl+C/close 提交确认不能返回 termination_confirmed=true。
- 网络或 B/A 连接断开，正在执行的命令结果标为 unknown。
  P0 不自动重连并继续复用旧命令；重新 connect 创建新的终端并重新探测。
  后续可恢复连接，但从不自动重跑写操作或假定原 shell 状态已恢复。
- status 使用已有快照并带探测时间；执行中不插入 cwd/shell 探针。
  需要终端的 get_system_info 同样受 busy 约束。

**重复调用与重启**

客户端可能重试工具调用（本地 Kimi 代码已确认，见接口调查）。
exec_command 及其他可能写入的接口必须接收调用者生成的稳定 operation_id。
B 用本地持久记录（优先标准库 SQLite）在提交前写入意图、规范化参数摘要与状态。

同 ID/同参数只返回原 command_id/状态；同 ID/不同参数返回 conflict。
已记为提交中而未得到结果，或进程重启后的未完成操作，一律 unknown，不补发。
明确未提交且待授权的操作可在确认后提交一次。记录保留期间不得自动遗忘后重执行；
清理/重置记录需维护者显式操作。旧 session_id 在重启后失效。
不承诺分布式 exactly-once，目标是本项目不主动重复提交不确定的操作。

**PTY 分帧与结果**

PTY 只有合并输出，不能自然分离 stdout/stderr。
TerminalClosed 中的退出码属于终端进程，不是一条 PowerShell 命令。

- 分帧全部在 Python：每条命令随机 nonce，开始/结束帧结构明确；
  包装负载编码后发送，完整哨兵不以明文出现在 PTY 输入行；
  增量解析跨消息边界、中文、CRLF、ANSI、无末尾换行和输入回显。
  nonce 只防偶然误匹配，不是对恶意远端输出的安全认证。
- 不能使用 v0.1 的单行 `echo "$LASTEXITCODE:$?"` 作为可靠实现。
  LASTEXITCODE 可能沿用上一条命令；? 只描述相关语句，额外命令、
  函数或包装会影响它，PS5.1/7 也有差异。
  依据：[PowerShell 自动变量](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_automatic_variables?view=powershell-7.5)。
- M1b 验证并记录包装器：立即捕获命令结果、处理终止/非终止错误，
  区分 native_exit_code（可空）、powershell_success（可空）、
  受控包装器的 outcome/错误信息；不得伪造 cmdlet 的原生退出码。
  任意脚本中间某步失败不能只靠最后 $? 判断，业务 runbook 定义自己的成功条件。
- 未收到合法结束帧、shell 被 exit/交互程序占用、发生已知丢流时返回 unknown；
  不能假装 success 或继续把后续输出归到下一条命令。
- 持久 shell 的 cwd 和显式保留的状态可跨命令；
  cwd 参数先 Set-Location，失败则不执行正文，成功会改变后续 cwd。
  不承诺内部包装局部变量全部保留；不支持任意嵌套 shell、交互输入与脱离终端的后台输出。
  无状态需求由 B 显式调用 -NoProfile/-NonInteractive 子进程。
- 返回 execution_state、结果字段、elapsed_ms、output_stream=combined、
  truncated、output_complete/缺失原因。completed 表示结束帧已收到，
  不等于业务处置成功；输出完整性只描述已观察的流，不保证对端从未丢数据。

**输出与 MCP 兼容**

B 保存有界输出供续读：默认每次展示最多 32 KiB，
每命令保留上限 8 MiB，达到上限仍消费流寻找结束帧并标记保留截断。
分页 offset 使用 Unicode 码点，next_offset 由 Server 返回，避免 UTF-8 字节切断。
输出保留/审计是不同对象；输出文件清理策略可配置且必须返回过期状态。

工具以普通 tools/call 返回 structuredContent 与兼容文本，并提供严格 schema、
稳定错误 code、stage、retryable 与是否可能已执行。
功能未实现不注册；0.2.0 已实现的可选工具保持可发现，未启用时返回明确 disabled 错误，
status 标示 optional_features 开关。工具列表一次返回，避免当前 Kimi 未遍历分页。
原生 progress、elicitation、Tasks、resources 仅在锁定 SDK/协议和客户端验证后增强。
核心结果始终可通过普通查询工具取回。

#### P1：文件、脚本和结构化诊断

接口/参数清单见 [接口调查第 4 节](interface-review.md#4-建议的-rustdesk-mcp-接口清单)。

- 文件：list_dir/get_file_info/read_file/write_file/edit_file/find_files/search_text；
  upload_file/download_file/make_dir/rename_file/remove_file。
  编码/换行/覆盖/分页明确，修改前校验哈希，冲突干净失败；
  校验与写入必须在同一远端受控操作内完成，无法安全锁定则拒绝。
- 传输首选独立 FILE_TRANSFER 会话；终端权限不代表文件权限或同等提权身份，
  必须验证 ProgramData 等目标可访问。目录/续传是官方协议已有能力，
  只有通过无头验证后才对 MCP 承诺支持。
  小量文本可经 PTY 编码传送；大文件/二进制不用该 fallback。
- exec_script：本地 .ps1 或注册 runbook → 官方上传 → 执行 →
  取回结果 → 按策略清理；全文/哈希可审阅。
  模板参数类型化，不把路径/字符串直接拼成可执行 PowerShell。
- 诊断：list_processes/list_services/list_installed_software/get_startup_items/
  get_network_connections/get_recent_events/get_file_hash/get_file_signature/get_security_status。
  JSON 结果、有界范围，明确 inaccessible/unavailable/覆盖限制。
- list_commands 查询有限历史，沿用 command_id。
  长任务占用一个终端；命令查询/取消工具先完成后才开放长任务。

#### Shell 与便携 PS7

官方 OpenTerminal 没有 shell 路径字段；默认 shell 的 profile/探测行为属于对端。
B 探测实际 shell/版本，P0 支持 PS5.1 和已验证 PS7；
无法获得受支持 PowerShell 时明确 unsupported_shell，不盲发 PS 语法。

P1 的 shell 参数和自定义路径在 B 处理，绝不修改官方 protobuf。
候选路径（Get-Command、标准安装目录、用户配置、自有便携目录）
都必须验证当前身份能执行；Store/非标准路径不保证自动发现。
默认不嵌套切换；需要其他 shell 时先使用明确的命令级调用。
-NoProfile 只约束显式创建的进程，不能消除官方首个 PTY shell 已加载的 profile。

deploy_pwsh_portable 默认关；官方 ZIP 的部署方式可参考
[微软安装说明](https://learn.microsoft.com/en-us/powershell/scripting/install/install-powershell-on-windows?view=powershell-7.5)。
不使用 MSI，不修改 PATH、注册表或已有安装。
版本、OS 支持和 CPU 架构固定在 B 的已验证 manifest。

- 下载源在 B 可配置优先级；自有可信 HTTPS/官方源优先，
  第三方 GitHub 代理只有维护者配置后使用，不内置“满速/永远可用”的保证。
- SHA256 由可信官方来源核对后写入 manifest，不能从同一不可信镜像同时拿包与哈希。
  无可信预期哈希则拒绝部署。校验下载包后再解压，记录来源/版本/架构/结果。
- curl.exe 是否存在、续传是否被服务器接受均需探测；失败可重下或通过文件通道传输。
  换源续传仅允许同一资产/哈希；HTTP 错误、代理限制、磁盘不足要明确报错。
- 复用 command_id 查询/取消下载；同一终端下载期间不能插入 Get-FileHash，
  下载完成后再校验。初期进度允许未知，不能靠解析不同语言的 curl 进度
  承诺准确百分比；后台独立任务另行验证。
- 已有包哈希不代表解压目录未改动；复用时核对安装记录及必要完整性，
  无法确认则拒绝复用/重新部署，不仅凭 pwsh.exe 存在就执行。

#### 远端产物与清理

默认根目录 `%ProgramData%\RustDeskMCP\`，当前身份无法写入则探测
`%TEMP%\RustDeskMCP\`；返回实际绝对路径。
首次写入也遵守写授权，P0 只读探针不创建远端工作目录。

缓存放 pwsh/；会话产物放 sessions/<session_id>/{scripts,out,tmp}/。
确认根目录、访问权限与重解析路径，拒绝经 junction/symlink 清理到根目录外。
只能清理由本次工具创建且有记录的产物，不能递归删其他会话或用户文件。

keep 默认保留；clean_tmp 只删本次 tmp 和已成功取回的 out；
wipe 经显式授权删本项目拥有的产物/指定缓存。
清理失败如实返回 cleanup_pending，不为清理失败自动重连/重执行。
清理不删除本地审计，也不能承诺远端“不留痕迹”。
用户显式批准的软件安装或目标文件修改可位于工作根目录之外。

#### P2：处置与扩展

run_defender_scan、kill_process、control_service、审阅后的安装/卸载/处置 runbook、
外部哈希情报、多 peer/独立终端并发。
Defender 扫描可能修改/隔离文件，非纯只读；扫描启动成功不代表扫描完成。
外部文件上传单独授权；远端输出按不可信数据处理。
宿主已有的 WebSearch、Agent、Todo、Plan、Skill 不复制进 RustDesk MCP。

### 3.2 安全护栏与审计

1. **只读默认**：只开放预审受控诊断模板/类型化参数。
   自由 exec_command/script、写文件、安装、停止进程等在只读模式拒绝。
   不按命令名/关键词认定任意 PowerShell 只读；静态解析不能证明无副作用。
2. **写操作确认**：默认所有自由命令/写操作需批准；
   格式化、清日志、关闭防护等默认拒绝，需维护者本地配置显式解锁后再确认。
   规则与例外在 B，不向模型提供 approved=true 等绕过参数。
3. **确认入口**：M2 写操作启用前提供最小本地 Web 确认页；
   支持并实测 elicitation 的客户端可使用可选适配器，否则返回 approval_required。
   当前本地 Kimi 未验证具备该能力，不能把其内置提问/客户端工具审批
   当成 Server 已收到可信批准。依据：[MCP elicitation](https://modelcontextprotocol.io/specification/latest/client/elicitation)。
4. **批准绑定**：peer/session/实际身份/完整命令或脚本哈希/规范化参数绑定本次操作，
   内容或目标变化重新确认。拒绝、取消、过期默认不执行；
   多窗口只有一次有效决策。重启后未提交的批准失效。
5. **本地接口**：bridge 仅 loopback + token，监视页仅 loopback，
   独立浏览器会话鉴权；校验 Host/Origin、限制 CORS、写接口用 POST 与 CSRF 防护。
   命令/输出按纯文本显示，防止输出中的 HTML/脚本冒充确认按钮。
   浏览器不接触 bridge token 和管理员密码。
6. **凭据**：credential_ref 解析到本地受限存储，优先 OS 凭据存储；
   可选 ACL 受限配置不进 git。使用独立 RustDeskMCP 配置/身份，不更改官方安装和配置；
   official 来源允许只读导入连接设置和对应 peer 的认证 hash，不恢复官方终端，不写回/迁移官方文件。
   不导出已保存密码明文，不绕过应用锁定或认证挑战。
   各 peer/admin 凭据不混用；认证失败不无限自动重试。
7. **审计**：P0 使用同一受限 SQLite 中的操作记录和追加 audit 表，减少双写一致性问题；
   后续可增加 JSONL 导出/轮转。命令前记录意图，之后记录结果/未知状态。
   字段含时间、peer/session/command/operation、实际身份、调用来源、
   批准来源、脱敏参数、脚本/输出引用、结果、耗时与清理状态。
   actor 身份不能由模型自报证明；不存 token/密码，敏感命令内容支持脱敏。
   输出按已定义上限保留，不能承诺“全量无上限”。
8. **存储与故障**：保留期/容量预算明确；默认不自动覆盖或删除审计，
   达预算/无法写审计时拒绝新的远端执行并报告原因；
   允许本地查询与取消。日志只追加不等于防篡改；
   需要更强证明时另立需求，不先引入远端日志系统。

### 3.3 Web 监视台

归属 B。M2 先最小确认页，M3 再补连接信息、实时命令/输出、
状态/耗时、输出折叠与分页、下载进度、历史审计。
浏览器可多开；B 汇总 bridge 通道事件与自己的命令/授权事件，
通过 SSE 等已选机制推送，批准/拒绝使用鉴权 POST。

URL 通过 stderr/本地启动入口展示；不污染 MCP stdout。
慢窗口允许少看实时显示片段并从 B 查询补齐，不能让执行流悄悄丢失。
监视台不另开桌面或终端，也不直接向 A 发送命令。
Flutter 菜单入口保留为 P2 可选，默认用浏览器入口即可。

---

## 4. 可维护性与官方主线合并

目标是少改已有运行路径、少持有上游私有接口依赖。
“新增文件零冲突”只描述常见文本合并，不能保证编译/行为兼容。

### 实现边界与预计改动面

- 新 Rust 逻辑在 src/mcp_bridge/，上游依赖集中到本目录的适配器；
  其他模块通过桥接自己的窄接口调用，不直接散落访问 Session/InvokeUiSession。
  此适配层只服务 MCP，不改官方 UI/其他调用者的抽象。
- 新增默认关闭的 Cargo feature `mcp-bridge`，首期仅 Windows；
  不启动 bridge 时不打开端口、不建会话、不执行探针。
  feature off 和其他平台保留原行为；显式不支持的 MCP 入口明确失败，
  不退回 Flutter 窗口或默默进入服务模式。
- 不改共享 trait/函数签名，不编辑 protobuf/生成桥接代码，
  不做客户端功能导致的 hbb_common 修改或子模块提交。
  同步官方快照的官方子模块指针按上游原样跟进并检查准确范围。
- admin 状态放本项目私有 Session 的既有字段；
  不通过进程级 IS_TERMINAL_ADMIN 环境变量协调并发会话。
- 不复制官方加密/NAT/连接/传输协议；不为了减少空回调改 InvokeUiSession。

| 已有文件/路径 | 预计必要改动 | 原因 |
|---|---|---|
| `Cargo.toml` | 新 feature，默认不启用 | 编译隔离与回归开关 |
| `src/lib.rs` | cfg 模块声明 | 新模块必须注册，v0.1 遗漏此处 |
| `src/core_main.rs` | 最小入口分支，调用模块内函数 | 在官方初始化后进入独立 MCP 路径 |
| `src/client.rs` / `src/client/io_loop.rs`（仅 spike 证明必需时） | 最小 MCP 专属配置读取/持久化 hook，不改函数签名 | 官方会读取/写回 terminal service_id；必须避免占用或覆盖用户终端恢复记录 |
| 构建/CI 参数入口（如 build.py、复用 workflow） | 仅必要时追加可选 feature 传递，缺省为旧行为 | 官方流水线不会自动启用新 feature |
| Flutter 工具栏、语言文件 | 仅实际启用 P2 菜单时追加 | 非 M1–M3 必需 |

新增 Python、runbook、监视台、测试及文档位于 mcp/；
平台相关必要 hook 放 src/platform/ 的自包含函数。
薄 hook 目标为几行，但不对未经 spike 验证的文件数/行数作保证。

### 同步策略与验收

维护 mcp-dev 的可读功能提交，不把官方历史整体 rebase。
默认选择稳定 release tag，也允许按需要选择一个 master commit；
不得约束为“以后只能跟 tag”。安全修复是否需要提前同步由维护者判断。

1. fetch upstream 后选定并记录完整提交/tag、工具链与子模块版本。
2. 在独立 worktree/临时分支做 merge，保留主工作区和未提交文档。
   merge 冲突解决后也不能据此认定兼容。
3. 重点核对 Session/InvokeUiSession、登录/缓存凭据及终端配置隔离、TerminalResponse/编码、
   文件回调、Windows 初始化、构建 feature 和打包入口。
4. 编译 feature off 与 on；on 验证连接/认证失败、PS5.1/7 命令、
   中文/长输出、等待/取消/断线 unknown，以及已实现的文件工具。
   off 至少检查官方桌面、终端、文件入口的相关冒烟行为。
5. 记录兼容矩阵和结果，再合并经过验证的同步分支。
   CI Artifact/发行 manifest 标明上游提交、fork 提交、bridge 协议与 B 版本。

对 hbb_common 官方指针变化检查精确 commit 范围，不单独引入无关升级。
每个功能提交/同步提交完成前执行 AGENTS.md 的最小化与回归面检查。
merge 时间取决于接口变动，不能承诺分钟级完成。

### 构建与发布

现有 Flutter Nightly 工作流可手动触发，但它调用的复用工作流还会发布/更新
nightly 资产，不能把它视为纯验证任务。新增 mcp 专用构建/验证入口，
首期集中 Windows x64，优先复用官方构建步骤/脚本；
必须增加参数时保持既有默认值，不复制完整全平台流水线。

M1a 即产出可运行 Artifact，验证解包目录内 exe/DLL 与便携包装器差异；
不要求维护者预先搭完整本地 Flutter 环境，也不等 M5 才知道发行包能否工作。
Python 用 pyproject.toml/uv.lock 和固定已测试 SDK；
发布提供配置示例、无秘密的工具清单、兼容矩阵和回滚所需的历史产物。
定制主控端与官方安装/自动更新的共存、配置路径在 spike 验证，
不能自动用官方更新包覆盖启用 bridge 的定制产物。

---

## 5. 风险与已接受的限制

| 风险 | 要求/已接受限制 |
|---|---|
| 无头路线只具备源码入口 | M1a 验证真实发行包、认证/权限回调、配置和运行时边界；失败先缩小支持范围 |
| Tokio 边界 | 官方 io_loop 已带 tokio::main 且由线程驱动；沿用官方线程路径，不从 bridge async 内直接调用并创建嵌套 runtime |
| PTY 输出/哨兵 | B 增量解析，合法完成帧才确定命令结果；远端现有缓冲可能丢输出，不改被控端协议来保证取证完整 |
| cmdlet/原生结果 | 分字段、可空、runbook 明确成功条件；不声称能自动证明任意脚本所有步骤成功 |
| 超时/断线/取消 | 结果可 unknown；无自动写重试、无远端进程树终止/回滚保证 |
| 自由命令只读判定 | 默认只读拒绝自由代码；受控模板或人工批准 |
| 提权与文件权限 | 返回实际身份，终端/文件通道分别验证；不静默降级再运行管理员操作 |
| 上游接口变化 | 集中桥接适配、默认关闭 feature、同步时双构建/冒烟；文本 merge 成功不足以验收 |
| Shell/工具差异 | PS5.1/已验证 PS7；cmd 或不可用候选路径干净失败；不自动安装依赖 |
| 远端处理可信度 | 输出是数据，不接受其指令作为授权；诊断/扫描覆盖不足明确说明 |
| 少见可用性边角 | 已能干净失败且无数据/安全后果的情况记 Known limits，不加跨组件缓存/回调补丁 |
| 回调/状态规模增长 | 每轮审查总 diff；遵守 AGENTS.md 的增长阈值和维护者批准要求 |

---

## 6. 里程碑与验收

| 阶段 | 产出 | 通过条件 |
|---|---|---|
| M1a spike | 最小适配器、入口/构建清单、可运行 Artifact、验证记录 | 无 UI connect→认证→open→收发中文输出→close；独立凭据、实际身份、错误路径；确认 tokio/配置隔离/管道边界，官方配置不受影响 |
| M1b 通道与执行验证 | bridge-protocol v1、A 的 P0 原语、最小 Python 执行驱动 | PS5.1/7 结果、输入回显/流分片、输出续读、busy、等待/执行期限、取消/断线 unknown、重复 operation_id；不先接 AI |
| M2 MCP P0 | 7 个工具、默认只读、凭据引用、审计与持久操作记录；写启用前最小确认页 | 实际 Kimi 及一个目标 MCP 客户端发现/调用成功；无确认不能写，重试不重复提交，stdout 干净 |
| M3 监视台 | 实时输出/状态、历史、完整批准/拒绝 UI | 多窗口决策一次有效，慢窗口不影响命令解析，URL/输出不泄露凭据或执行 HTML |
| M4 P1 | 文件通道/文件编辑搜索、runbooks、结构化诊断；便携增强可后置 | 文件权限、覆盖/冲突、哈希、传输完成与清理验证；已有 P0 行为保持 |
| M5 首个定制 release | 同步演练、兼容矩阵、可追溯产物、B 配置/启动说明 | 选定官方基线 merge 后 feature off/on 验收；普通桌面路径冒烟；能回退上个已验证版本 |

M1a 开始前直接形成 docs/m1-implementation.md：
列出入口、必需回调、官方线程/运行时边界、配置读写、实际改动文件、
构建方式与可验证假设。该清单是后续实现的一部分，不默认额外增加审批流程。
若成功必须依赖修改共享接口/被控端协议，或触发 AGENTS.md 的明确批准条件，
先说明具体原因及最小替代方案，再等待维护者决定。

自动化只覆盖已实现契约和关键错误行为；真实远端/UAC/打包记录保留手工验收。
复用现有测试能力，不为边角情况引入浏览器截图 harness、远端模拟平台或新抽象。

---

## 7. 决策记录（v0.2）

1. 保持 Python + uv MCP、内嵌 bridge、官方被控端、本地 Web 监视台。
2. exec 包装/分帧/结果/超时迁至 Python；Rust 仅连接、终端 I/O、文件与事件。
3. P0 7 个工具；执行、输出查询、取消共用 command_id；同终端 busy，无隐式排队。
4. 持久 shell 保留；不默认嵌套切换；结果字段区分原生/PowerShell/业务语义。
5. 默认只读受控诊断；写操作不依赖宿主拥有确认功能，M2 提供最小确认页。
6. loopback 随机端口，私有启动管道传进程级 token，父进程/控制连接退出清理。
7. 自由执行和其他 effectful 操作使用 operation_id 与 B 的持久记录；
   断线/重启结果不确定时不重跑。
8. 便携 PS7 opt-in，下载来源/可信哈希在 B；第三方镜像按维护者配置启用。
9. 默认关闭 feature，预计 Cargo/lib/core 及必要构建 hook；
   可以同步官方 tag 或选定 master commit，每次验证接口与现有运行路径。
10. 工具/schema/协议/源码兼容分别管理；不承诺新增目录或 thin hook 免维护。

---

## 8. 开工提示词

```text
在 C:\05workspace\rustdesk\rustdesk-client 工作，分支 mcp-dev。
先读 AGENTS.md、mcp/README.md、mcp/docs/PRD.md；
MCP 工具语义参考 mcp/docs/interface-review.md。

任务：实现 M1a spike，验证无头连接、认证/权限、终端收发与关闭。
先用代码核对 Session/InvokeUiSession、官方线程与 runtime 边界、
Windows Flutter runner、配置/凭据路径，再形成 m1-implementation.md 并实施。
只有验证通过才继续 M1b；把实际版本/身份/构建和失败限制写入记录。

约束：
- Rust 新代码集中 src/mcp_bridge/；默认关闭 mcp-bridge feature。
- shared 文件只加必要 hook；不改官方共享 trait/协议、生成代码或 hbb_common。
- 不接管用户终端，不绕安全挑战，不用进程环境变量协调 admin 会话。
- 复用官方连接/传输；脚本、哨兵、命令超时/输出缓冲/策略放 Python。
- 遵守 Rust/Tokio/错误处理/日志节流/import 规则。
- 使用 CI Artifact 验证实际发行包入口与管道，不先承诺整条路线已可用。
- 最后检查最小 diff 并列出已有文件及运行路径的回归面。
```
