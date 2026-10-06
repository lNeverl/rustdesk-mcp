# 验证记录：0.2.0 / 2026-10-07

## 验证结果

| 项目 | 证据/结果 |
|---|---|
| 独立 EXE release | 已构建，23,223,296 字节；bridge 0.2.0、RustDesk 1.5.0、协议1.1 |
| 普通 feature off | cargo check --locked --lib 通过；新增功能关闭时保留原路径 |
| EXE PE 依赖 | dumpbin /dependents 仅 Windows 系统库，无额外 VC/Sciter DLL 静态导入 |
| 本机回归 | 11 项 unittest 通过，含真实 PowerShell、真实 MCP stdio、审批/重试/容量/锁边界 |
| 工具注册 | 实际 MCP SDK 握手发现40个工具，均有 input/output schema |
| 原生生命周期 | ready、错误 token 拒绝、stdin EOF、控制 socket EOF、父进程强制结束的 Job 清理通过 |
| 官方配置 | 只读复用、应用 PIN/缺少凭据拒绝；既有10个官方 TOML 前后哈希核对 |
| 配置来源 | official、profile、官方服务器+私有密码覆盖均已真实连接验证 |
| 实际远端 | 官方1.4.8，Windows 11 build26200，PS5.1.26100.9444，普通账户；实际身份/SID 探测 |
| P0 stdio 全流程 | 新安装的独立 runtime：连接、系统信息、中文输出、重试、字符分页、只读拒绝、取消已结束命令、断开确认通过 |
| 文本文件 | 本机 BOM/编码/备份/锁定哈希；真实远端创建/读取/编辑/旧哈希拒绝/重命名/按哈希删除通过 |
| 目录/搜索 | 真实目录分页、文件名搜索、忽略大小写正则内容搜索、签名与近期 System 事件查询通过 |
| 官方文件通道 | 从对端下载 powershell.exe 495616字节；2 MiB 二进制上传/下载/前后 SHA256 相同/稳定重试通过 |
| 诊断 | 真实进程/服务/安装软件/启动项/网络/Defender状态查询通过；取不到的范围明确标记 |
| 本地审批 | 真实 loopback HTTP 批准后执行；本机测试错误 Origin=403、错误摘要=409、没有审批不执行 |
| 管理员边界 | 缺少有效引用拒绝；审批前不调用管理员连接，批准绑定请求；成功管理员认证未实测 |
| 多会话 | 同一 peer 两个独立 EXE/终端，断开一个后另一个继续探测通过 |
| 脚本 | hello runbook 全文审批、官方上传、中文/emoji参数、执行成功、记录产物清理通过 |
| 可信部署 | 官方 PS7.4.20/x64 ZIP下载111969701字节、固定 SHA256、上传、安全解压、版本探测通过 |
| PS7使用/清理 | portable_deployment_command_id 完整性复核，显式 PS7中文执行，925个记录产物清理通过 |
| 长输出 | 80001字节中文/emoji，3页拼接与预期完全相同；丢帧序号回归拒绝 |
| 真正运行中取消 | Start-Sleep120秒在2秒后取消，收到 terminal.closed；state=cancelled/termination_confirmed=true |
| 隔离安装 | 交付 install.ps1 按哈希锁创建本版本 runtime，wheel运行/MCP真实连接通过，不改系统 Python/PATH |
| 最终交付复核 | 最终wheel重装到隔离runtime后真实SDK联调通过；最终11项回归再次通过 |
| 提交敏感信息 | 67个提交文件和ZIP/wheel按本机私有配置值核对，无真实密码/API key/服务器地址/账号/机器key；私有联调脚本与用户测试信息文件均被Git忽略 |

测试日志放在忽略的 build/ 内，不提交凭据、token、完整日志或运行数据：
`mcp-full-build-final.log`、`mcp-feature-off-final.log`、`mcp-tests-final-delivery.log`、
`mcp-full-acceptance-final.log`、`mcp-workflows-acceptance-final.log`、`mcp-output-acceptance.log`、
`mcp-bridge-final.log`、`mcp-clean-smoke.log`、`mcp-install.log`。

## 实际支持矩阵

| 组合 | 状态 |
|---|---|
| 主控 Windows x64，本机现有 VS2026/MSVC14.50/Rust1.99/Python3.13 | 已编译和运行 |
| 远端官方 RustDesk1.4.8 + Windows11 build26200安装版 + 自建服务器 | 已通过上述普通账户验收 |
| 远端 PS5.1 | 默认终端，已验收 |
| 自有 PS7.4.20/x64 ZIP显式命令级调用 | 已验收，不修改默认 shell |
| 其他Windows版本/便携端/锁屏/UAC多登录会话 | 未纳入实测保证，失败必须明确返回 |
| Linux/macOS被控端、ARM主控 | 不属于本版产品范围；拒绝非Windows对端 |
| 管理员连接成功、真实安装/卸载、进程/服务系统处置、Defender扫描 | 代码/护栏已实现；本轮不执行真实破坏性操作，管理员凭据未提供 |
| VirusTotal真实 API key 查询 | 可选接口已实现，缺少key明确拒绝；成功路径未实测 |
| Kimi UI注册/实际Agent调用、真实浏览器手动点审批 | 已提供配置/界面；SDK/HTTP已验收，客户端UI侧仍需验收 |
| 公网官方服务器、代理/WebSocket及多网络组合 | 未全面实测，不从单个自建服务器推断 |

## 本轮修复的实际问题

1. Windows accepted socket 继承非阻塞，已显式恢复阻塞读取。
2. ConPTY折行破坏长标记，改为短 DATA/META、UTF-8增量分帧。
3. 长输入逐字符重绘引发流中断，改有界分段输入，专用终端探针移除PSReadLine。
4. 大部署文件清单在PTY输出中丢失，改简短结果、独立manifest，本地可信ZIP导出记录。
5. 流记录加连续序号，缺失/乱序变unknown，不能把不完整输出宣布成功。
6. 清理便携包从逐个发送完整助手改为有界批量校验/删除，仍拒绝改动/未知文件和非空目录。
7. 同一运行目录加OS文件锁，防止第二个MCP将第一个实例的活跃记录误标unknown。

## 回归面与维护检查

既有上游文件总计仅新增30行，无删除/重构：

- Cargo.toml（6行）：默认关闭feature和独立bin，必要的编译入口。
- src/lib.rs（2行）：Windows+feature on加载新模块。
- src/core_main.rs（7行）：显式--mcp-bridge在全局初始化前进入独立命名空间；普通入口原样。
- src/client.rs（15行）：feature专属标记，仅MCP load/save走私有内存；过滤持久终端恢复标识。

其余新增逻辑在 src/mcp_bridge/ 和 mcp/。共享trait/函数签名/protobuf/Flutter/Cargo.lock未改，
hbb_common子模块仍为229b904508364c8997aad0fb5af57effac859f60。
`.gitignore`只追加用户测试凭据文件的排除规则；该文件不进入提交。
最终最小化检查保留普通feature off、普通GUI、官方配置路径的既有实现。

MCP专用行为改变：新终端不持久化/恢复；探针卸载该终端的PSReadLine；读写只走MCP私有配置；
分帧/审批/多会话/文件/部署/历史均属于新模块，不改变官方用户正在使用的终端。
上游最近文档机制可能留下本产品记录；不是“无痕”，不承诺端点被攻陷时输出可信。

## 复现命令

```powershell
pwsh -NoProfile -File mcp/scripts/dev.ps1 python -m unittest discover -s mcp/server/tests
pwsh -NoProfile -File mcp/scripts/build.ps1 -FeatureOff
pwsh -NoProfile -File mcp/scripts/dev.ps1 python mcp/scripts/check_bridge.py `
  --bridge-exe build/mcp-dist/0.2.0-rustdesk-1.5.0/rustdesk-mcp-bridge.exe
pwsh -NoProfile -File mcp/scripts/dev.ps1 python mcp/scripts/smoke.py `
  --bridge-exe build/mcp-dist/0.2.0-rustdesk-1.5.0/rustdesk-mcp-bridge.exe `
  --peer-id '<授权目标ID>' --config-source official --data-dir build/mcp-acceptance/runtime
```

正式配置仍默认official+只读，保留独立test-peer profile；下载/情报默认关闭。
测试不把密码放进命令行，不修改官方配置，不执行软件安装/卸载、系统终止或扫描。
源码提交与交付包哈希以最终Git/manifest记录为准，不能把P0旧产物当成本版完整交付。
