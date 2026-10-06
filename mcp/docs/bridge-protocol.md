# Bridge protocol 1.1（0.2.0）

A 是独立 Windows x64 控制台 `rustdesk-mcp-bridge.exe`，也预留普通 RustDesk 的
`--mcp-bridge` feature 入口。一个 B 为每个会话启动独立 A；MCP 客户端只与 B 的 stdio 交互。

## 传输和生命周期

- A 的 stdout 仅一条 ready JSON：`type=ready`、`protocol={major:1,minor:1}`、
  `port`、随机 `token`、`instance_id`、`rustdesk_version`、`capabilities`。
- `127.0.0.1:0` TCP，UTF-8 NDJSON，单帧最多 2 MiB；非法/超限帧断开。
- ready 后 30 秒内接受唯一控制连接并鉴权。第一条 RPC 是
  `hello {token,major:1}`。失败返回 authentication_failed 并退出。
  token 只存在本次进程和私有管道内，不写文件/日志。
- 请求 `{jsonrpc:"2.0",id,method,params}`，结果/错误按 id 匹配。
  回调通知 `{jsonrpc:"2.0",method:"event",params:{session_id,channel,type,data}}`；channel=files 区分独立文件通道。
- 事件队列 256 条，每次解压输出上限 1 MiB，Zstd 解压失败、队列溢出或流损坏
  使会话失效。B 将未完成命令标记 unknown，不补发。
- 父进程 stdin EOF、控制连接断开触发 A 退出并关闭拥有的会话。
  B 使用 Windows Job Object 的 KILL_ON_JOB_CLOSE；正常退出先关闭远端终端。
- 终端原语只返回本地提交结果，不能把它当作远端命令完成。

## 原语

| 方法 | 参数/结果 |
|---|---|
| hello | token、major；返回实际协议版本 |
| connect | peer_id、config_source=profile/official；profile 用 server/server_key/password，official 在 A 内读取配置，password 可覆盖；本地 B 可提供 official_config_dir；返回 session_id、connecting、terminal_id=1 |
| disconnect | session_id；关闭拥有的会话，提交层不声明远端清理已确认 |
| status | session_id；authenticated、secured、terminal_open |
| terminal.open | session_id；固定 terminal_id=1、40 行/160 列，非持久新建 |
| terminal.write | session_id、UTF-8 data；上限 256 KiB；submitted |
| terminal.resize | session_id、rows/cols，范围 1–500 |
| terminal.close | session_id；submitted、termination_confirmed=false，等待 closed 事件 |
| files.connect / files.disconnect | session_id；建立/关闭额外官方 FILE_TRANSFER 会话 |
| files.status | session_id；文件通道 authenticated/secured |
| files.upload / files.download | session_id、job_id（正 i32）、local_path、remote_path；单文件官方任务 |
| files.cancel | session_id、job_id；请求取消，不将请求当成远端确认 |
| files.list | session_id、job_id、remote_path；ReadDir 回执没有对应请求 ID，本版 MCP 使用受控 PowerShell 列目录 |
| files.mkdir / files.rename / files.remove | session_id、job_id、路径/新名称；remove directory 非递归；MCP 使用哈希保护的文件工具 |

原语中的明文密码只由 B 在本地解析 DPAPI 引用后通过私有鉴权连接传入；
official 缓存密码只在 A 内解密为认证 hash，不经过 B。官方文件直接只读反序列化，不调用迁移写回。
MCP connect 不提供 password/official_config_dir 字段。显式 elevate 由 B 先做连接级人工批准，
批准后才发送 os_username/os_password；不能以命令批准代替登录提权批准。

## 事件

authenticated（对端版本/平台）、transport（secured/direct/type）、permission、
challenge（认证/错误）、terminal.opened（success/pid/replay/persistent_sessions 数量）、
terminal.data（base64 编码的已解压字节）、terminal.closed（shell_exit_code）、
terminal.error、session_selection_required、session_closed、stream_lost。
文件通道还发送 files.progress/done/error/directory/overwrite_required；原始任务完成仍需 B 校验哈希。
覆盖挑战默认失败，不自动同意覆盖；文件认证来自连接时保留的私有 snapshot，不读取用户终端恢复状态。

terminal.closed 是 shell/终端关闭，不能解释成某条 exec 的退出码。
认证 challenge 缺少凭据、应用 PIN、多 Windows 会话选择或不安全传输时失败，
不打开 UI、不自动重试认证、不自动同意不安全连接。

## Python 执行契约

- nonce BEGIN/END 哨兵在 base64 编码 PowerShell 包装内，输入回显不会伪装成结果。
  DATA/META 使用递增序号及每行 64 个 base64 字符，检测缺失/乱序；有界发送避免 ConPTY 批量重绘。
  长输入分段构造后执行， owned terminal 探针卸载 PSReadLine，避免逐字符重绘膨胀。
  UTF-8/ANSI/哨兵可以跨事件分片；同一终端一个操作。直接绕过 PowerShell pipeline 写控制台的程序
  仍受终端渲染影响，不能保证这类输出的原始字节；本期不支持交互式全屏程序。
- PowerShell 点执行保留 cwd/用户 shell 状态；每命令重置 LASTEXITCODE，
  捕获 ErrorRecord、终止异常、native exit code。`exit`/进程结束/断线可能返回 unknown。
- wait 0–30 秒只控制查询等待；execution 1–3600 秒到期关闭拥有的终端。
- preview 最多 32 KiB，保留 8 MiB；保留达到上限仍消费流寻找结束哨兵。
  offset/next_offset 按 Unicode 字符，结果明确 output_truncated/has_more。复合操作达到硬上限时失效并关闭终端。
- SQLite 在提交前记录稳定 operation_id、完整请求摘要及状态。
  同参数重试返回旧结果；不同参数 conflict；重启未完成操作 unknown。
  历史输出允许用原 session_id 查询，原 session_id 不能创建新的执行操作。
- 固定 diagnostics + 默认 cwd 可只读执行；任意命令或自定义 cwd 默认拒绝。
  allow_writes opt-in 后仍需本地 Web 批准完整请求摘要，一次决定只提交一次。
- 取消优先关闭专用终端，只有 closed 回执才确认 terminal termination。
  不保证所有脱离 shell 的子进程停止，不回滚修改。

## 错误和兼容性

invalid_params、invalid_peer_id、invalid_server、authentication_failed、credential_unavailable、
application_locked、not_authenticated、session_closed、busy、stream_lost、frame_too_large、
official_config_unavailable、official_config_invalid、official_credential_unavailable、official_application_locked、invalid_config_source、
unsupported_shell_or_probe_failed、elevation_approval_required、readonly_policy、
operation_conflict、approval_conflict、submission_unknown、connect_timeout。

bridge major 不兼容拒绝启动；minor 后续兼容扩展。RustDesk 官方版本由真实对端探测报告。
本版 capabilities 包含 terminal/files；业务工具不进入上游 protobuf 或共享 trait。
