# MCP 编译、部署与更新流程

## 目录规范

| 用途 | 路径 | 使用方式 |
|---|---|---|
| Rust 编译输出 | `target/release/rustdesk-mcp-bridge.exe` | 编译器产物，不直接注册给 Kimi |
| 交付暂存 | `build/mcp-dist/0.2.0-rustdesk-1.5.0/` | EXE、wheel、manifest、依赖锁、脚本、文档，每次构建可更新 |
| 可分发 ZIP | `build/mcp-dist/rustdesk-mcp-0.2.0-windows-x64.zip` | EXE 和 Python 服务成套分发，不包含 runtime、缓存、凭据 |
| 实际部署 | `%LOCALAPPDATA%/RustDeskMCP/releases/<UTC时间>-<manifest哈希前12位>/` | 每次部署创建新目录，包含独立 runtime，不覆盖旧运行环境 |
| Kimi 启动配置 | `%USERPROFILE%/.kimi-code/mcp.json` | 仅更新 `mcpServers.rustdesk` 的启动路径；保留其他 MCP 和已有环境设置 |
| Kimi 配置备份 | `mcp.json.bak-<部署目录名>` | 切换时保存原始配置，用于回滚 |
| MCP 持久数据 | `%LOCALAPPDATA%/RustDeskMCP/` 下的 `config.json`、数据库、spool 等 | 跨版本复用；部署不清空历史、凭据或修改下载设置 |
| 部署记录 | 新部署目录中的 `deployment.json`、`manifest.json` | 保存实际路径、源码提交、版本、哈希和配置备份位置 |

开发构建的版本号可能相同，用时间和 manifest 哈希区分实际构建。
正式分发仍应同步提升 Bridge/Python 版本，再构建、验收、部署。
EXE 是本机的连接组件；Python wheel 是本机 MCP 服务包，两者都部署到控制电脑。
目标电脑保留现有 RustDesk，无需安装这个 wheel。

## 从源码更新

在仓库根目录执行；环境脚本使用子进程，避免改变当前终端的构建环境。
首次构建或缺少构建工具时，先执行 `bootstrap-build.ps1`。

```powershell
pwsh -NoProfile -File mcp/scripts/build.ps1
pwsh -NoProfile -File mcp/scripts/dev.ps1 python -m unittest discover -s mcp/server/tests
pwsh -NoProfile -File mcp/scripts/package.ps1
pwsh -NoProfile -File mcp/scripts/deploy-kimi.ps1 -AutoApprove
```

涉及 Rust 或 feature 入口的变更，还需执行 `build.ps1 -FeatureOff`。
工具签名/描述变化时，打包前执行 `dev.ps1 python mcp/scripts/export-tools.py`。
仅更新 Python 时可复用经过验收的匹配 EXE，执行测试、`package.ps1` 和部署；
此时 manifest 的 `source_revision` 描述 EXE 的构建来源，wheel 用独立 SHA256 标识。

部署脚本会依次：

1. 检查交付文件齐全，复制成套文件到新部署目录，排除旧 runtime/缓存。
2. 校验 EXE/wheel SHA256，复用已有 Python，由 uv 按依赖锁安装新 runtime；禁止自动下载 Python。
3. 验证 EXE 握手、错误令牌拒绝、连接凭据缺失的明确错误和拥有的进程退出。
4. 用独立临时数据目录验证 MCP stdio 握手和工具列表，不连接目标电脑。
5. 通过验收后备份并原子替换 Kimi 配置，生成部署记录；检测配置被同时修改时停止切换。
6. 提示完全退出并重启 Kimi。脚本不结束正在使用的 Kimi、MCP 或远端任务。

`-AutoApprove` 为 RustDesk 条目设置 `RUSTDESK_MCP_AUTO_APPROVE=1`。
省略时保留已存在的审批设置；新增条目则使用 MCP 默认策略。
该选项控制 MCP 本地审批；Windows UAC 仍取决于远端实际执行身份。

交付暂存目录不应长期作为 Kimi 的运行目录，否则后续构建可能遇到 EXE 占用或半套更新。
在切换前确认旧 MCP 没有正在执行、等待审批或尚未核实结果的操作。
完全退出旧 MCP 后，可以一致备份持久数据；代码部署和回滚不恢复旧操作数据库。

## 使用 ZIP 或其他 Kimi 配置路径

解压完整 ZIP 后执行：

```powershell
pwsh -NoProfile -File scripts/deploy-kimi.ps1 -AutoApprove
```

脚本会把解压目录作为交付来源，实际运行目录仍统一放到 `RustDeskMCP/releases/`。
可显式传入 `-PackageDir`、`-PythonExe`、`-KimiConfig`。
使用自定义 Kimi home 或旧 CLI 的机器，应传入它实际读取的配置路径，避免修改错误的配置文件。
本机当前有效路径为 `C:\Users\wang1\.kimi-code\mcp.json`。
首次使用还需按 [configuration.md](configuration.md) 设置 official/profile 来源与凭据；已有配置的升级无需重复配置。

## 本机 2026-10-07 部署记录

已部署 EXE 和 Python MCP `0.2.0`，RustDesk 基线 `1.5.0`，源码标记 `4e99e6b4238a2ef85b2842c67a65f1d8c82444af-dirty`。
当前目录为 `C:\Users\wang1\AppData\Local\RustDeskMCP\releases\20261007T103557487Z-3680dc395c67\`。
Kimi 启动配置已切换到其中的 `runtime\Scripts\python.exe` 和 `rustdesk-mcp-bridge.exe`，auto_approve 保持开启。
EXE 的 5 项本地验收、实际启动命令的 40 工具握手、新 GBK 参数、可信下载默认开启和 `list_sessions` 已通过。
其他 MCP 条目与现有持久配置保持一致；原始配置备份路径记录于该目录的 `deployment.json`。
后续部署以新生成的 `deployment.json` 及 Kimi 实际配置为准。

## 更新后的确认与回滚

重启后先让 Kimi 调用 `list_sessions`，确认 MCP 可以响应。
需要远端验收时，再连接目标电脑检查 `administrator`、`pwsh`、`downloads_enabled` 和中文输出。
本地验收通过不代表目标电脑上的 UAC、画面捕获或服务状态已经验证。
PATH 没有全局修改；Kimi 通过绝对路径启动新 Python 和 EXE，无需重启 Windows。

回滚前完全退出 Kimi，读取当前部署目录的 `deployment.json`，找到 `config_backup`。
如果部署后未修改其他 MCP 配置，可把该备份复制回记录中的 `kimi_config`，再启动 Kimi。
若其他 MCP 已有新配置，只把备份中的 `mcpServers.rustdesk` 合并回当前文件，保留其他条目。
旧部署目录继续保留；不要删除持久历史或用旧数据库覆盖新记录。
某个版本不再被客户端引用且相关进程已退出后，才可手工清理对应 release 目录。
