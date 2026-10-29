# Kimi 会话中的 MCP 交互复查

复查 2026-10-07 导出的卸载输入法会话，以 agents/main/wire.jsonl 的 agent.message.appended 为准，
不重复统计 context 中的事件副本。共 48 次 MCP 调用；未执行记录中的卸载、提权或计划任务命令。

## 会话证据与处理

| 证据 | 结论与处理 |
|---|---|
| 第 224 行 connect：auto_approve、elevation_requested=false、administrator=false | MCP 审批已跳过，终端仍是普通用户；补充管理员连接说明，并在 connect/status 返回可用 admin_credential_refs 名称 |
| 同一结果：目标 RustDesk 1.4.8、PowerShell 5.1、pwsh=null、downloads_enabled=false | 下载开关未开启，模型也未调用部署工具；按维护者要求改为可信下载默认开启，并提示缺少 pwsh 时优先部署便携版 |
| 第 234、489 行：7-Zip 的中文产品名/错误含 34 个 U+FFFD | 原生输出在 PowerShell 管道按错误编码解码；新增 exec_command.native_output_encoding=gbk，优先让 7z -sccUTF-8 输出 UTF-8 |
| 第 243 行：嵌套 cmd /c 报 Invalid argument/option；随后改写 bat 文件 | PowerShell/cmd/JSON 多层引号混用；指引直接传 PowerShell 源码或 exec_script + typed parameters |
| write_file 新建多份 .ps1，显式使用 UTF-8 | 原实现无 BOM，PowerShell 5.1 可能误读中文；默认 encoding=preserve 新建 .ps1 现在加 UTF-8 BOM，显式 utf8 保持原选择 |
| 第 250、258、268、521 行：四次 RunAs | 普通终端每次启动独立管理员进程；同意子进程提权不会提升普通父终端 |
| 后期创建 KimiElevatedAgent 登录任务和脚本投递目录 | 已存在一次 UAC 后复用管理员进程的方案，但它不属于 MCP 会话；取消 MCP 不保证停止该任务，输出和生命周期未统一管理 |
| 最后仍有占用文件和待重启删除项，却宣布全部完成 | detached 启动或写入重启删除队列不是业务完成；补充检查真实结果、输出分页及禁止 unknown 重放的指引 |

记录中的 JSON 双反斜杠和字符串内转义是正常序列化。解码后的文件路径、PowerShell 中文输出正常，
没有证据表明 MCP JSON 或 UTF-8 Base64 framing 在途中损坏中文。
原生程序输出已经变成 U+FFFD 后无法恢复，因此在解码前选择编码，不能事后“反转乱码”或自动重跑有副作用的命令。
同一次调用含不同编码的原生程序时，应统一为 UTF-8 或拆分调用。

Windows PowerShell 5.1 将无 BOM 的脚本按 ANSI 读取，`>` 写出的日志通常是 UTF-16LE；
把 UTF-8 内容直接追加到这种日志会混合编码。MCP 的 read_file 应按真实编码读取，
生成脚本应优先 exec_script（已有 UTF-8 BOM 上传及类型化参数），输出文件须统一显式编码。
参见 [Microsoft 编码说明](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_character_encoding?view=powershell-7.5)。

## 管理员权限

源码 connection.rs 的 fill_terminal_user_token/handle_installed_user 表明：安装版普通终端有意使用当前登录用户的 token；
handle_administrator_check 用有效 Windows 本地/域管理员凭据验证后选择 RustDesk 服务/进程自身 token。
这是已存在的管理员终端认证通路；MCP connect(elevate=true, admin_credential_ref=...) 已对接。
当前本机 MCP 配置未保存管理员 profile；不能将 RustDesk 连接密码当作 Windows 密码尝试。

一次本地交互配置并用 DPAPI 保存有效 OS 管理员凭据，启用 auto_approve 后，
后续管理员连接及命令可以不再逐次 MCP 审批或 RunAs。实际权限以返回的 administrator/identity/SID 为准，
SYSTEM 的 HKCU/用户目录与登录用户不同。配置命令见 [configuration.md](configuration.md#管理员终端与-uac)。

UAC 没有给普通父进程“点击一次后永久管理员”的记忆机制。复用已经获准的管理员进程，或使用已安装的服务，
可以避免反复提示；登录计划任务的后续可用性取决于登录条件、文件和任务是否仍在。
参见 [Microsoft UAC 机制](https://learn.microsoft.com/en-us/windows/security/application-security/application-control/user-account-control/how-it-works)。
本次没有修改目标机上的 KimiElevatedAgent 任务，也未添加新的管理员脚本轮询服务。
维护者随后明确偏好临时 UAC 脚本：模型提示已调整为优先复用现有管理员终端，
无 OS 管理员凭据时按需合并为一次临时 RunAs，等待实际结果，不创建常驻/开机提权任务。
远端 Python 工作优先使用已有 uv 和已有 Python 环境，不自动安装或下载新解释器；
MCP 的命令包装为自己的远端终端设置 UV_PYTHON_DOWNLOADS=never，避免 uv 隐式下载解释器。
本机 wheel 构建仅是 MCP 服务打包，与远端安装 Python 无关。

## UAC 画面冻结与重连

已检查 video_service.rs 的 desktop_changed/DXGI 恢复、service.rs 的错误后切换桌面、
windows.rs/windows.cc 的 OpenInputDesktop/SetThreadDesktop、scrap DXGI 的错误映射。
两项被审提交未修改这些采集路径；MCP 使用独立终端连接，不采集远端画面。

UAC 默认切到安全桌面；DXGI DuplicateOutput 要求相应访问权限，Microsoft 明确指出安全桌面访问需要 LOCAL_SYSTEM。
因此“管理员运行 UI”与“实际采集进程由 SYSTEM 服务启动”需要分开核对。
参见 [DuplicateOutput](https://learn.microsoft.com/en-us/windows/win32/api/dxgi1_2/nf-dxgi1_2-idxgioutput1-duplicateoutput)。

现有证据不足以确定目标机故障。需要在问题出现时核对目标 RustDesk 服务的运行状态、账户和 exe 路径，
实际 --server 进程的身份/会话，以及采集日志中的 Failed to switch desktop、Desktop changed、
dxgi access lost、gdi/encoder 错误；并区分网络未认证与认证后等待首帧。
本机普通 RustDesk 日志未检索到上述采集错误，不能代替目标机日志，也不能据此证明目标采集正常。
本次未改动共享视频、桌面切换、输入或上游权限认证路径。

## 验证与回归范围

- 真正的 Windows PowerShell 5.1 验证 GBK stdout/stderr、中文脚本、退出码及后续 UTF-8 输出；旧实现确实产生乱码。
- 新增测试验证管理员引用发现、编码参数参与幂等检查，以及可信下载默认开启/显式关闭/布尔类型校验。
- 共 22 项 Python 测试通过，wheel 构建、PowerShell 语法检查及 git diff --check 通过。
  默认 PowerShell 7.4.20 x64 ZIP 的 URL 和 SHA256 已与官方发布资产及 hashes.sha256 核对一致。
- 只改变 MCP 的 app.py/tool_files.py/tool_workflows.py 工具契约与模型提示，engine.py 状态/命令参数，
  framing.py 显式 GBK 解码和终端内 uv 下载策略，files.ps1 默认新建 .ps1 的 BOM，config.py/downloads.py 的下载开关。
- 默认 UTF-8 命令、已有文件编码及显式下载禁用继续使用原路径；没有添加运行时状态、定时器或后台部署任务。
- 管理员 OS 凭据登录、实际远端便携 PS7 部署和 UAC 画面恢复未进行现场验收，不能以本地测试替代。
- 与前一轮提交 review 的修复共存；CI、审批清理、传输 staging 等前一轮修改仍在工作区。

## 部署完成与完整回归范围

本机 Kimi 的 `.kimi-code/mcp.json` 已切换到独立 release，具体路径、更新和回滚见
[deployment-workflow.md](deployment-workflow.md)。复用已有 Python 3.13.12，通过 uv 安装依赖和新 wheel；未下载解释器。
原始 Kimi 配置已备份，其他 MCP 条目逐项比较一致；新启动命令在隔离数据目录下实际验收通过。
Kimi 需完全退出并重启，运行中的旧会话/命令不能移交给新版本。

既有文件及其受影响路径如下，均属于本次 MCP review、交互修复和部署要求：

| 文件 | 既有路径的变化及必要原因 |
|---|---|
| `.github/workflows/mcp-windows.yml`、`scripts/bootstrap-build.ps1` | MCP CI 缓存恢复与构建引导，解决缓存的 cargo 代理缺失和非空 vcpkg 目录导致构建失败 |
| `scripts/dev.ps1` | uv 参数转发，使原生短选项正确传递 |
| `scripts/package.ps1` | 交付包增加 Kimi 部署脚本，使源码与 ZIP 使用同一流程 |
| `app.py` | MCP 初始化失败时关闭 Hub；工具编码参数、管理员说明及执行提示修正 |
| `config.py`、`downloads.py` | 配置布尔值严格校验与可信下载默认开启，避免字符串误授权及默认无法部署 PS7 |
| `engine.py` | 命令编码透传/幂等绑定、管理员引用发现、下载能力状态，与实际工具契约一致 |
| `framing.py` | 可选 GBK 解码和 uv 解释器下载禁用，避免原生工具中文损坏与隐式安装 Python |
| `operations.py` | 提交状态不明/输出流丢失后关闭不可信终端，避免后续操作接着执行 |
| `hub.py`、`web.py` | 审批失败/取消及页面地址写入失败时清理拥有的资源，避免进程泄漏或输出审批令牌 |
| `scripts/files.ps1` | staging 遵守 remote_roots，新建默认 .ps1 使用 BOM，保证允许目录与 PS5 中文脚本正确 |
| `tool_files.py`、`tool_workflows.py` | 工具描述明确脚本编码、临时执行、下载默认值及便携部署用法 |
| `tests/test_catalog.py`、`tests/test_controls.py`、`tests/test_p0.py` | 只验证本次已报告的 API/中文编码/配置和审批行为 |
| `README.md`、`docs/api-reference.md`、`docs/configuration.md`、`docs/delivery-guide.md`、`docs/installation-and-upgrades.md`、`docs/tools-schema.json` | 文档/schema 与实际工具、配置和部署路径同步 |

上述相对路径除 workflow 外均在 `mcp/` 下，Python 模块位于 `server/src/rustdesk_mcp/`。
新增部署脚本不会终止现有进程、不改写 MCP 持久配置/数据库；它更新的既有运行路径仅为 Kimi 的 RustDesk 启动条目。
没有修改 RustDesk Rust 核心、Flutter、共享协议、子模块、视频采集、服务或目标机权限路径。
