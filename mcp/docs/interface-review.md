# MCP 接口评估与 Kimi Code 参考清单

评估日期：2026-10-06。Kimi Code 本地源码：`C:\05workspace\kimi-code`，
提交 `f67e6398f`；RustDesk 本地源码：提交 `fada664df`，本地 tag `1.5.0`。
本文件记录调查依据与接口设计建议；产品范围、分期和实现约束以 [PRD](PRD.md) 为准。
0.2.0 已实现本调查对应的 40 个 MCP 工具；阶段划分保留用于说明优先级。
准确接口见 [API](api-reference.md) / [实际 schema](tools-schema.json)，实测边界见 [验证记录](validation-record.md)。
实际构建/远端验收状态以 [validation-record.md](validation-record.md) 为准，不能以接口注册代替验收。

## 1. 结论：核心能力够用，原接口契约还不完整

原 PRD 的 connect / disconnect / status / exec_command 可以演示远程执行，
但还缺少实际连续工作的闭环：长任务如何继续查询、输出截断后如何取回、
命令如何取消、重试如何避免重复执行、文件如何安全编辑、权限不足如何返回。

参考 Kimi Code 的工具类别很合适。值得复用的是接口语义和操作习惯：
命令执行，文件读取/写入/编辑，文件名和内容搜索，任务查询/停止，有限的结构化诊断。
这些能力加上 RustDesk 的文件传输，能够覆盖常见远程开发、故障排查和运维任务。

“有这些工具”不等于“任何任务都能成功”：远端必须具备相应软件、权限和网络条件；
交互式安装、桌面点击、离线系统恢复和可信恶意软件取证不属于本期保证。
这里的“常用”按任务覆盖判断，未读取用户会话日志，也不是调用频率统计。

## 2. Kimi Code 实际提供了哪些工具

依据是当前 TypeScript 的输入 schema、工具实现和注册代码。
存在注册代码不代表所有模式都启用；工具还受 profile、功能开关、
运行环境能力和 provider 配置约束。下面的路径相对 Kimi Code 仓库根目录。

| Kimi 工具 | 常见用途与主要参数 | RustDesk MCP 如何参考 |
|---|---|---|
| `Bash` | `command`、`cwd`、`timeout`、`run_in_background`、`description` | `exec_command`；远端使用 PowerShell，不要求安装 Git Bash |
| `Read` | `path`、`line_offset`、`column_offset`、`n_lines`、`max_chars`；支持从末尾读 | `read_file`；分页、尾部读取、编码和续读位置 |
| `Write` | `path`、`content`、`mode=overwrite/append` | `write_file`；显式覆盖、追加，补充写前校验 |
| `Edit` | `path`、`old_string`、`new_string`、`replace_all` | `edit_file`；匹配不唯一即失败，避免整文件覆盖 |
| `Glob` | `pattern`、`path`、`head_limit`、`offset`、`include_ignored` | `find_files`；明确 Windows 路径、排序、扫描范围 |
| `Grep` | `pattern`、`path`、`glob/type`、输出模式、上下文、分页 | `search_text`；基础文本搜索，远端没有 rg 时用受限 PowerShell 实现 |
| `ReadMediaFile` | 图片/视频读取，图片区域和原始分辨率选项 | 暂不复制；已有图片可下载后交给宿主读取，屏幕截图另立需求 |
| `TaskList` | `active_only`、`limit` | `status` 返回当前命令；P1 的 `list_commands` 查询历史 |
| `TaskOutput` | `task_id`；非阻塞快照，32 KiB 预览及完整日志引用 | `get_command_output`；稳定命令 ID、状态和分页输出 |
| `TaskStop` | `task_id`、`reason`；已结束任务可安全再次调用 | `cancel_command`；区分取消请求与已确认停止 |
| `WaitFor` | `task_id`、`timeout`；等待超时可继续查询 | `get_command_output` 的有限等待；不用再建一套任务 ID |
| `WebSearch` | 搜索资料、故障说明；需搜索 provider | 使用 Agent 已有的联网工具，不在 RustDesk 内重做搜索引擎 |
| `FetchURL` | 获取网页正文 | 宿主资料查询；远端下载文件另用 `download_url` |
| `AskUserQuestion` | 结构化提问、选择方案 | 普通澄清交给宿主；执行授权必须走 MCP Server 可验证的确认通道 |
| `NotifyUser` | 进展更新 | Python 日志/监视台及可选 MCP progress；不假设宿主一定展示通知 |
| `Agent` | 创建或恢复 subagent | 由宿主编排；RustDesk MCP 不创建模型或 subagent |
| `AgentSwarm` | 批量 Agent 协作 | 由宿主编排；P0 同一终端并发执行应返回 busy |
| `TodoList` | 待办与进度管理 | 宿主能力，不加入远端协议 |
| `Skill` | 调用技能 | 宿主能力；远端可复用处置脚本库 |
| `EnterPlanMode` | 开始制定计划 | 宿主能力 |
| `ExitPlanMode` | 提交计划并等待用户选择 | 宿主能力；不能替代本次远端写操作的授权 |
| `select_tools` | 加载需要的工具 schema | 借鉴按场景启用工具；MCP Server 不自行复制 Kimi 的选择工具 |

另外发现目标管理工具 `CreateGoal/GetGoal/SetGoalBudget/UpdateGoal`、
定时工具 `CronCreate/CronList/CronDelete` 和 `Tower*` 协作工具。
它们服务于 Agent 生命周期与协作，本项目无需照搬。
远端 Windows 计划任务的“查询启动项”也不等同于宿主的 Cron。

主要源码依据：

- 命令：`packages/agent-core-v2/src/agent/tools/os/bash/{bash.ts,bashTool.ts,process-task.ts}`。
- 文件：`packages/agent-core-v2/src/agent/tools/os/{read,write,glob,grep}/`；
  编辑：`packages/agent-core-v2/src/agent/tools/edit/`、
  `packages/agent-core-v2/src/app/edit/editService.ts`。
- 后台任务：`packages/agent-core-v2/src/agent/tools/task/{task-list,task-output,task-stop,task-wait}/`。
- 其他工具：`packages/agent-core-v2/src/agent/tools/` 与 `src/features/` 的注册代码。
- 审批：`packages/agent-core-v2/src/agent/permissionPolicy/policies/default-tool-approve.ts`。

## 3. 不能直接照搬的行为与客户端兼容性

### 3.1 本地进程与远端 PTY 的差别

Kimi 的 Bash 实现通过 process service 启动独立 `shell -c`，
立即关闭 stdin，并能等待真实进程退出、分别读取 stdout/stderr、发送进程终止信号。
它不依赖前一次 Bash 调用保留变量。

RustDesk 终端是交互式 PTY，只有合并输出；命令完成和 shell 退出是两回事。
`TerminalClosed.exit_code` 是整个终端的退出码，不能当成其中一条命令的退出码。
不能承诺同样可靠的退出码、流分离、后台进程管理和进程树终止。

本期保持原需求的持久 PowerShell 会话；同一终端只执行一条前台命令。
耗时操作可以返回 command_id 后继续查询，但仍占用这个终端。
需要并发时后续使用独立终端；不要在同一 PTY 里混跑后台命令与诊断探针。

RustDesk 的独立连接还共享本地 PeerConfig：client.rs::create_login_msg 会读
terminal service_id，client/io_loop.rs 在 opened 时经 set_option 持久写回。
MCP 必须创建并独占自己的终端，同时隔离用户终端恢复标识/持久设置；
这一点进入 M1a 验收，不能仅凭“另外建立一条连接”就认定互不影响。

### 3.2 当前 Kimi MCP 客户端需要特别适配的四点

1. `mcpCore/client-stdio.ts` 和 `client-http.ts` 构造 SDK Client 时没有显式
   声明 elicitation，当前源码未发现相应请求处理器。
   **Kimi 内置 AskUserQuestion 可用，不能据此认定外部 MCP 能调用它。**
   P0 可只读；启用写操作时，M2 必须有最小本地确认页。
2. `mcpCore/client-shared.ts::toMcpToolDefinition` 只转交 name、description、
   inputSchema；该路径未转交 annotations/outputSchema。
   工具描述必须说清远端执行、可能修改系统、超时和查询方法，
   授权在 Server 实施，不能依赖客户端理解 hint。
3. `agent/mcp/tools/mcp.ts::retryAfterReconnect` 可探测存活、重新连接并再次
   `callTool`。写操作必须携带稳定 operation_id；重试只返回已有状态，
   不再次执行。判断不清是否已送达就返回 unknown，不能自动补发。
4. `client-stdio.ts::listTools` 当前只调用一次工具列表请求；
   `callTool` 是普通调用，没有本项目可依赖的 Tasks 接入逻辑。
   初期工具列表一次返回；长任务用普通查询工具，不要求 Tasks 扩展。
   MCP 返回内容支持 structuredContent；`agent/mcp/output.ts` 会补充为文本，
   对普通非媒体 resource_link 则不能假设会自动读取。

上述是这份本地源码的结论，不代表所有 Kimi 版本或其他客户端。
M2 仍须用实际安装的客户端验证握手、工具发现、错误结果、确认和长任务查询。

MCP 的输出 schema 和工具 annotations 可以提供，但 annotations 是提示信息，
不是权限执行机制；本项目同时返回结构化数据与兼容文本。
依据：[MCP Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)。
原生 Tasks 仅在双方支持相应协议/扩展后作为增强，
依据：[MCP Tasks 扩展](https://tasks.extensions.modelcontextprotocol.io/)。

## 4. 建议的 RustDesk MCP 接口清单

所有操作针对远端 Windows。除 connect 外，会话工具显式传 session_id；
本地文件参数命名 local_path，远端参数命名 remote_path，避免操作错机器。
所有接口按阶段实现，未实现的工具不注册到 tools/list。
0.2.0 的40个已实现工具可发现；下载/情报未配置时明确拒绝并标示 disabled，避免误解为已授权可用。

### 4.1 P0：7 个接口，先完成可靠闭环

| 接口 | 主要输入 | 关键返回与语义 |
|---|---|---|
| `connect` | peer_id、可选 config_source=official/profile、credential_ref、admin_credential_ref、elevate=false | session_id、实际身份/SID、权限、版本、能力及来源；官方只读/自定义可选，不接收明文密码 |
| `disconnect` | session_id | 关闭本项目拥有的终端和连接；重复调用安全，报告远端清理是否确认 |
| `status` | session_id | 连接/终端状态、实际身份、shell、最近 cwd、active_command_id、policy、capabilities |
| `exec_command` | session_id、operation_id、command、cwd、wait_timeout_s、execution_timeout_s | command_id、running/completed/failed/unknown 等状态、合并输出和结果元数据 |
| `get_command_output` | session_id、command_id、offset、max_chars、wait_timeout_s | 当前状态、分页、next_offset、截断/缺失标志、结果；查询超时不停止命令 |
| `cancel_command` | session_id、command_id、reason | 取消是否接受、termination_confirmed；不保证已撤销修改或结束所有子进程 |
| `get_system_info` | session_id | OS/build/架构、PowerShell、实际权限、工作根目录与可用工具；由受控只读探针实现 |

这里的 operation_id 用来识别同一次操作的重试，不是 JSON-RPC id。
status 查询本地已有快照；终端忙时不插入探针命令。
get_system_info 需要终端时同样遵守 busy 规则。

### 4.2 P1：常见开发与文件操作

| 接口 | 关键要求 |
|---|---|
| `list_dir` / `get_file_info` | 路径、类型、大小、时间、有限分页；说明拒绝访问和扫描范围 |
| `read_file` | 文本按行/尾部读取，长单行续读；返回编码、换行和读取时的原始文件哈希 |
| `write_file` | 创建/覆盖/追加明确；覆盖/追加带 expected_sha256，创建用 exclusive；失败报告实际结果 |
| `edit_file` | 精确替换；默认唯一匹配；expected_sha256、原编码/换行、可选 diff 预览；冲突拒绝写 |
| `find_files` | 文件名匹配；有界根目录、深度、条数和时间；不自动跟随目录 junction/symlink |
| `search_text` | 内容搜索；字面量优先，可选有界正则；文件名/内容/计数模式与上下文 |
| `upload_file` / `download_file` | local_path 与 remote_path；官方文件通道；大小、哈希、覆盖策略、完成确认 |
| `make_dir` / `rename_file` / `remove_file` | 写操作审批；rename 默认不覆盖，remove 默认非递归；受控路径检查 |
| `exec_script` | 本地 .ps1 或注册 runbook、类型化参数；全文可 review；上传、执行、取回结果、按策略清理 |
| `list_commands` | 有限历史列表、命令状态；复用 command_id，避免另一套后台任务模型 |

文件编辑的 expected_sha256 是乐观并发校验，不能独自解决检查后文件被改变的竞争。
实现时在同一次远端操作内持有合适的文件句柄/锁，校验与写入一起完成；
无法满足时返回 file_busy/conflict，记录文件类型/文件系统限制，
不要通过增加跨组件缓存来假装解决竞争。

默认遵守用户批准的工作目录和敏感文件策略；忽略文件规则影响搜索范围，
不能代替权限限制。不为搜索自动安装 rg。
二进制或大文件走传输；不要把大段 base64 交给模型当文件传输。

### 4.3 P1/P2：远程诊断和处置

这些接口均在 Python 侧包装受控脚本，优先注册查询类；修改类依赖明确授权。
参数和输出应使用 JSON 字段，避免让模型解析排版表格。

| 需求 | 建议接口 | 分期与注意点 |
|---|---|---|
| 进程与资源异常 | `list_processes` | P1；PID、父进程、路径、时间、资源；取不到的字段标明 unavailable |
| 服务异常 | `list_services` | P1；状态、启动类型、账户、路径；查询不改配置 |
| 软件盘点 | `list_installed_software` | P1；系统/用户、32/64 位范围明确，不保证穷尽所有便携软件 |
| 启动项排查 | `get_startup_items` | P1；Run/启动文件夹/服务/计划任务，返回已覆盖与未覆盖的范围 |
| 网络异常 | `get_network_connections` | P1；TCP/UDP、监听/连接、PID、地址；权限不足单独标注 |
| 日志定位 | `get_recent_events` | P1；log/provider/级别/时间/条数过滤，记录 ID；不用整本日志返回 |
| 可疑文件 | `get_file_hash` / `get_file_signature` | P1；SHA256、签名状态和发布者；哈希与签名不能独自判定是否恶意 |
| 防护状态 | `get_security_status` | P1；Defender 是否可用、模式、更新/检测状态；不可用时明确返回 |
| 扫描 | `run_defender_scan` | P2；quick/full/custom，长任务查询；扫描可能隔离文件，需写操作审批 |
| 结束进程/控制服务 | `kill_process` / `control_service` | P2；PID 与启动时间校验、明确动作、保护目标；不能只依赖名称 |
| 下载/部署 | `download_url` / `deploy_pwsh_portable` | P1 后段、opt-in；固定来源策略、可信预期哈希、进度、取消 |
| 安装/卸载/处置 | `exec_script` 的受审 runbook | P2；记录修改目标、备份/回退条件及实际结果，不默认提供万能安装器 |
| 外部哈希情报 | `analyze_file_hash` | P2 opt-in；先只查哈希，上传文件另行授权；情报不可用不阻塞本地查询 |

Defender 扫描与参数可参考微软 [Start-MpScan](https://learn.microsoft.com/en-us/powershell/module/defender/start-mpscan?view=windowsserver2025-ps)。
启动项范围可参考 [Sysinternals Autoruns](https://learn.microsoft.com/en-us/sysinternals/downloads/autoruns)；
本期基础查询不承诺等价覆盖，也不自动下载/运行 Autoruns。

### 4.4 能完成哪些典型任务

| 场景 | 使用的接口组合 | 仍依赖什么 |
|---|---|---|
| 改配置、修脚本、验证构建 | find_files/search_text/read_file/edit_file/exec_command/get_command_output | 远端已有项目、编译器与依赖；写授权 |
| 查服务启动失败 | get_system_info/list_services/get_recent_events/read_file | 日志与配置读取权限 |
| 查高 CPU、可疑进程 | list_processes/get_network_connections/get_file_hash/get_file_signature | 能读取进程信息；必要时管理员 |
| 查开机自启动 | get_startup_items/list_services/get_recent_events | 系统和用户范围权限；覆盖有限 |
| 采集日志交给本地分析 | exec_script/download_file | 文件通道及输出目录访问权限 |
| 安装或卸载指定软件 | upload_file/download_url/exec_script/命令查询 | 安装包、非交互参数、权限、重启安排 |
| 启动 Defender 扫描并观察 | get_security_status/run_defender_scan/get_command_output | Defender 可用；扫描结果不能只看启动命令退出码 |

不单独加入 git/build/test/npm/pip 等工具：远端已有的 CLI 由 exec_command 调用即可。
将重复且需要严格权限判断的任务收敛成 runbook，再决定是否值得提供专用接口。

## 5. 接口完整性检查表

“名称都列出来”之后，至少还要把下面的契约落实，才算能够持续使用：

- **定位机器与会话**：显式 session_id；身份从远端探测，拒绝静默提权或串到桌面终端。
- **确定执行结果**：completed 不自动等于业务成功；cmdlet/原生退出码分开表达，
  断线、哨兵缺失、流丢失允许 unknown；结果未知不得自动重跑。
- **等待与取消**：等待期限和执行期限分开；同一终端 busy；
  取消确认与子进程残留、不可逆修改分别表达。
- **重复调用**：effectful 操作带 operation_id；Python 在发送前持久记录意图，
  同 ID 同参数返回原状态，不同参数冲突；重启后的未完成记录视为 unknown。
- **输出**：小预览、可续读、明确上限；丢流、保留上限和展示截断分开；
  不能承诺 PTY 每字节不丢，Web 窗口变慢不能阻塞命令解析。
- **文件**：local/remote 路径明确；编码、CRLF/LF、BOM、冲突和覆盖；
  搜索有界、链接路径处理、文件传输权限与终端权限分别检查。
- **授权**：Server 强制策略；自由 PowerShell 无法靠关键词判断只读；
  凭据引用不进模型上下文，危险命令的批准绑定本次完整操作。
- **可发现性**：严格输入 schema、输出 schema、错误码、能力、版本；
  描述写明场景和副作用；只注册已实现且当前配置启用的工具。
- **宿主兼容**：普通 tools/call 即可完成核心功能；progress/elicitation/Tasks/resources
  只作已验证的增强，不能成为读回结果或授权的唯一入口。
- **维护边界**：以上策略、工具、runbook 放 Python；Rust 只提供连接、终端 I/O、
  文件通道和事件，避免为每个 AI 工具增加 Rust 接口。

## 6. 其他可参考的实现与使用范围

[MCP 官方 Filesystem 参考实现](https://raw.githubusercontent.com/modelcontextprotocol/servers/main/src/filesystem/README.md)
提供文件读取/写入/编辑、目录枚举、搜索、移动、文件信息和允许目录，
并支持编辑预览及工具 annotations。适合借鉴路径边界、文件工具分工和编辑预览；
不需要把其服务进程安装到远端。

[MCP Tools 规范](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
适合校准输入/输出 schema、结构化结果和错误返回；
[MCP elicitation 规范](https://modelcontextprotocol.io/specification/latest/client/elicitation)
明确要求客户端支持相应能力。实现必须锁定并测试实际 SDK/协议组合，
不能以规范存在某功能推断客户端已支持。

Kimi 的代码适合作为交互参考；复制其 Node Agent 引擎、DI、subagent 或完整任务系统
会扩大本项目。先用 7 个 P0 接口验证可靠闭环，再补 P1 文件/诊断能力，
按 terminal/files/diagnostics/admin 场景配置启用工具，即可覆盖本期主要需求。
