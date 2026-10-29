# MCP API 参考：0.2.0 / 40 个工具

准确字段类型、默认值、范围及 annotations 见 [tools-schema.json](tools-schema.json)。
该文件由 `mcp/scripts/export-tools.py` 从实际服务导出；更改工具后重新生成，避免手写文档与代码漂移。

## 共用契约

- 不向 MCP 传密码；仅用 credential_ref/admin_credential_ref。本地配置 DPAPI，官方密码 hash 在 EXE 内使用。
- R：固定只读查询；W：启用 allow_writes 后逐次在本地 Web 审批（auto_approve 模式下自动批准）。annotations 只是客户端提示，服务仍强制检查。
- connect 返回 session_id；管理员 connect 先返回审批记录，批准后查询 status/get_command_output。
- W 工具要求稳定 operation_id（1–128 字符）。同 ID/请求返回旧记录；改参数 conflict；unknown 不重放。
- 一个会话一次一个操作，busy 不排队。只读查询也是操作，可能超过默认 10 秒等待而返回 running。
- 业务查询成功常返回 `{state:completed,data:...}`；大结果/未结束返回 command_id 与 output 分页。
- 工具提交成功不代表业务成功，必须看 state、result.error、业务 confirmed 字段。参数/策略错误可返回 MCP isError。
- get_command_output 的 offset/next_offset 按 Unicode codepoint，每页最多 32 KiB UTF-8，保留最多 8 MiB。
- wait_timeout_s 是有限等待，execution_timeout_s 是操作期限；期限到关闭拥有的终端，不能撤销已发生的写入。
- 文件路径要求绝对 Windows 盘符路径；拒绝 UNC、设备路径、ADS、junction/symlink 祖先和配置根目录逃逸。
  remote_roots=[] 表示允许实际账户可访问的路径，不是 OS 安全沙箱。自由 PowerShell 的完整脚本必须人工审阅。

## 连接、执行与记录（10）

| 工具 | 关键参数 | 权限 / 返回 |
|---|---|---|
| connect | peer_id；credential_ref、config_source=official/profile；elevate、admin_credential_ref | 普通认证；管理员另审；session_id/身份/SID/权限/配置来源 |
| disconnect | session_id | 关闭本项目终端/连接，cleanup_confirmed |
| status | session_id | R，缓存快照及本 peer 的 admin_credential_refs 名称，不插入远端探针 |
| exec_command | session_id、operation_id、command、cwd；wait_timeout_s 0–30、execution_timeout_s 1–3600、native_output_encoding=utf8/gbk | 六个固定 diagnostic 名称默认 R；自由命令/自定义 cwd 为 W |
| get_command_output | session_id、command_id；offset>=0、max_chars 1–65536、wait_timeout_s 0–30 | R，状态、output、next_offset、has_more、result |
| cancel_command | session_id、command_id、reason | 可取消待批准；运行中关闭终端并报告 termination_confirmed，需重连 |
| get_system_info | session_id | R，OS/build/架构/实际身份/SID/管理员状态/shell/cwd/工具 |
| list_sessions | 无 | R，本实例会话和缓存身份 |
| list_commands | session_id 可省略；offset>=0、limit 1–200 | R，历史列表；可查询以前会话 |
| list_audit_events | offset>=0、limit 1–200 | R，审计分页 |

固定命令：diagnostic:system_info、processes、services、network、disk、unicode。
仅名称完全匹配且 cwd 缺省时为只读；并非任意 Get-* 都可只读执行。
每次新连接探测默认 shell；本项目终端卸载 PSReadLine，避免大输入逐字符重绘破坏控制流，不接管用户终端。

command 是 PowerShell 源码，反斜杠不用于转义引号。复杂脚本优先 exec_script + parameters，
避免再套 cmd /c、powershell -Command。脚本/cwd 通过 UTF-8 Base64 传送；JSON 中的双反斜杠是正常序列化。
原生 stdout/stderr 默认按 UTF-8 解码；老中文程序可显式 native_output_encoding=gbk（CP936），
只影响本次调用，结束恢复 UTF-8；编码选择也参与 operation_id 的请求一致性检查。
该设置覆盖本次调用的全部原生程序；混合编码时拆分调用，或让程序输出 UTF-8，例如 7z -sccUTF-8、python -X utf8。
已在程序内部或 PowerShell 管道中损坏的文本无法恢复；不会通过猜测编码自动执行或重放命令。

## 文件（12）

| 工具 | 关键参数 | 权限 / 行为 |
|---|---|---|
| list_dir | remote_path、offset、limit<=1000 | R，目录元数据页；排序后分页 |
| get_file_info | remote_path | R，类型、大小、时间、属性 |
| read_file | remote_path、offset、max_chars、encoding、start_line、tail_lines、expected_sha256 | R，<=8 MiB 文本；每页<=4096 codepoint，编码/BOM/换行/原始 SHA256 |
| write_file | operation_id、remote_path、content；mode=create/overwrite/append、expected_sha256、encoding、newline | W，排他创建或独占句柄核对哈希/备份/写入 |
| edit_file | operation_id、remote_path、old_text、new_text、expected_sha256；replace_all、encoding | W，默认必须唯一匹配；精确替换，保留 BOM/换行 |
| find_files | remote_path、pattern、max_depth、limit、max_files、timeout_s、include_hidden、exclude | R，有界文件名搜索，不跟随重解析目录 |
| search_text | remote_path、query、pattern；regex、case_sensitive、深度/条数/大小/时间、encoding、exclude | R，字面量优先，正则有超时；返回路径/行号/有界文本 |
| make_dir | operation_id、remote_path | W，已存在拒绝 |
| rename_file | operation_id、remote_path、destination | W，不覆盖目标 |
| remove_file | operation_id、remote_path、expected_sha256 | W，文件独占句柄内校验/删除；目录仅空目录、非递归 |
| get_file_hash | remote_path | R，SHA256 |
| get_file_signature | remote_path | R，Authenticode 状态和发布者，不等于恶意判定 |

编码：auto/preserve 按 BOM 探测，未带 BOM 默认严格 UTF-8；可显式 utf8/utf16le/utf16be/gb18030。
未知编码/二进制拒绝，不静默替换非法字节。read 的行/尾部过滤先执行，offset 相对于过滤后的文本。
写入 encoding=preserve、newline=preserve 为默认；可显式 lf/crlf。原始字节哈希与读取的过滤文本不同。
默认新建 .ps1 加 UTF-8 BOM，供 PowerShell 5.1 正确读取中文；新建时显式 encoding=utf8 可选择无 BOM。
其他文件和已有文件的覆盖/追加保持原有编码策略。
覆盖/追加需 expected_sha256，默认唯一编辑找不到/不唯一均拒绝。备份后原位置写入不承诺断电原子性。
inline 参数加助手脚本总量上限 64 KiB；过大时使用 upload_file/exec_script，不经 PTY 传二进制。
搜索默认扫描10000项、限时10秒，最大深度32、结果1000、扫描100000、单文件8 MiB、时间60秒。

## 文件通道（2）

| 工具 | 关键参数 | 行为 |
|---|---|---|
| upload_file | operation_id、local_path、remote_path、mode=create/overwrite、expected_sha256、等待/期限 | W；先捕获本地不可变快照供审阅，官方上传新 staging、校验后提交；覆盖保留备份 |
| download_file | operation_id、remote_path、local_path、等待/期限 | W；远端前后 SHA256 与本地比较，提交到新本地路径，拒绝覆盖 |

默认单文件上限512 MiB，并受本地总容量预算约束。无目录/自动续传/隐式覆盖确认。
设置 remote_roots 时，上传/脚本/部署的暂存目录也创建在允许根目录内；未设置时仍使用 ProgramData/TEMP。
终端管理员身份与文件通道权限可能不同；官方 overwrite challenge 失败关闭，不能自动批准。
同卷创建使用不覆盖的移动；跨卷创建与覆盖是流式写入，失败可能留下部分产物。
进度在 status/Web 当前操作和 get_command_output.result.transfer 中显示，完成需官方回执及哈希验证。

## 诊断与处置（10）

| 工具 | 参数 | 权限 / 覆盖 |
|---|---|---|
| list_processes | offset、limit<=500 | R，PID/父进程/路径/命令行/创建时间/资源；取不到的字段标 unavailable |
| list_services | offset、limit<=500 | R，状态/启动模式/账户/路径/PID |
| list_installed_software | offset、limit<=500 | R，HKLM 64/32 bit 和当前 HKCU 卸载项；非全部便携软件 |
| get_startup_items | offset、limit<=500 | R，Run 键/启动目录/自动服务/计划任务及 covered/unavailable |
| get_network_connections | offset、limit<=500 | R，TCP/UDP、地址/端口/PID；协议查询失败标 unavailable |
| get_recent_events | log_name、provider、hours 1–168、levels 1–5、limit<=100 | R，四类允许日志，消息<=4096字符；Security 可能需要管理员 |
| get_security_status | 无额外参数 | R，Defender 可用性/保护模式/签名/扫描时间 |
| kill_process | operation_id、pid>4、creation_time | W，核对 PID 创建时间，保护系统/本进程/RustDesk；等待停止回执 |
| control_service | operation_id、name、action=start/stop/restart | W，保护关键服务；等待实际目标状态 |
| run_defender_scan | operation_id、scan_type=quick/full/custom、remote_path、execution_timeout_s | W，可能隔离文件；submitted 与 scan_completion_confirmed 分开 |

Defender 不可用不是“无威胁”。custom 完成状态可能无法由状态字段证明，仍可返回未确认；查事件/防护状态核实。
扫描、服务任务可能脱离终端继续；cancel 不承诺撤销。

## 脚本、下载与可选情报（6）

| 工具 | 参数 | 权限 / 行为 |
|---|---|---|
| list_runbooks | 无 | R，本地 hello/install_msi/uninstall_msi 全文 |
| exec_script | operation_id；local_path 或 runbook；parameters、shell_path、shell_sha256、portable_deployment_command_id、期限 | W；UTF-8 .ps1 <=64 KiB，全文/哈希审批、官方上传、标量参数、执行、默认 keep |
| download_url | operation_id、asset_ref、remote_path、期限 | W + downloads.enabled；manifest HTTPS/固定 SHA256/域名白名单，官方上传到排他新目标 |
| deploy_pwsh_portable | operation_id、asset_ref=pwsh-7.4.20-win-x64、期限 | W + downloads.enabled（默认 true）；固定官方 ZIP/哈希，安全有界解压、版本探测，不改默认 shell/PATH/注册表 |
| cleanup_artifacts | operation_id、command_id | W；本会话记录产物；校验哈希/路径，已空目录，返回 cleanup_pending，保留审计 |
| analyze_file_hash | sha256 | R + intel.enabled/DPAPI API key；VirusTotal 仅哈希查询，不上传文件；未找到非清白结论 |

parameters 仅 str/int/float/bool/null 标量，合法参数名，总 JSON<=8192字节；不拼接成可执行字符串。
PS5.1 上传脚本加 UTF-8 BOM。默认当前 shell，shell_path 显式创建 -NoProfile PowerShell 进程。
使用 portable_deployment_command_id 时执行前验证其 manifest 及全部记录文件。
可信下载默认启用，downloads.enabled=false 可关闭；auto_approve 控制写审批，不覆盖下载开关。
connect 本身不开始下载；模型在缺少 pwsh 且具备写权限时优先调用 deploy_pwsh_portable，
随后将已完成 command_id 作为 exec_script 的 portable_deployment_command_id，持久终端的默认 shell 不变。
MSI 模板要求哈希或产品 GUID，禁止自动重启；真正安装/卸载条件需在目标环境验收。

## 状态与错误

pending_approval → submitting/running → completed/failed；取消可 cancelled；流丢失/重启未完成为 unknown。
completed 是受控操作得到结果，业务 confirmed/success/scan_completion_confirmed 仍须核对。
固定查询权限缺失会返回 unavailable 或失败，不返回假数据。当前 Web 最近历史30项，全部历史由工具分页。

常见错误：readonly_policy、busy、operation_conflict、approval_conflict、session_closed、runtime_in_use、
credential_unavailable、official_credential_unavailable、official_application_locked、connect_timeout、
file_conflict、text_not_unique、reparse_path_refused、path_outside_allowed_roots、file_transfer_failed、
stream_lost、submission_unknown、storage_budget_exceeded、audit_storage_unavailable、
downloads_disabled、download_hash_mismatch、portable_manifest_conflict、deployment_integrity_conflict、
intel_disabled_or_credential_unavailable、intel_rate_limited。

默认产品不实现宿主的 WebSearch/Plan/Todo/Skill/Agent 编排；这些由 Kimi/其他客户端提供。
