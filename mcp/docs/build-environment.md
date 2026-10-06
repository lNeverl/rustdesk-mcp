# 构建环境与清理边界

## 隔离约定

构建工具归项目管理，不修改系统/用户 PATH，不设置系统/用户环境变量，不改变用户现有 Rust 默认工具链，
不安装/修改 Visual Studio 组件。请使用 `pwsh -NoProfile -File ...` 或 `powershell -NoProfile -File ...` 启动子进程。
脚本设置进程环境；直接在现有 PowerShell 中 `& ./脚本.ps1` 或 dot-source 会影响那个进程的环境，
因此不要用这两种方式在常用终端中运行构建/开发脚本。CI 每个 step 的 shell 是独立进程。

| 内容 | 位置 |
|---|---|
| Rustup、Rust | `build/mcp-tools/rustup/` |
| Cargo 及 registry/git 缓存 | `build/c/`（缩短 Windows 深层子模块路径） |
| vcpkg 源码与静态库 | `build/mcp-tools/vcpkg/` |
| vcpkg 下载/二进制缓存 | `build/mcp-tools/vcpkg-downloads/`、`build/mcp-tools/vcpkg-cache/` |
| libclang、CMake 辅助包 | `build/mcp-tools/python/` |
| uv 缓存/可选受管解释器 | `build/mcp-tools/uv-cache/`、`build/mcp-tools/uv-python/` |
| 构建临时目录 | `build/mcp-tools/tmp/` |
| Python 项目依赖 | `mcp/server/.venv/` |
| Rust 编译输出 | `target/` |
| 可交付 exe | `build/mcp-dist/` |

以上位置都被 Git 忽略。项目通过 `mcp/scripts/dev.ps1` 调用 PATH 已有 Python，
禁用 uv 自动下载 Python；`uv.lock` 锁定 Python 依赖。构建使用现有 Visual Studio C++ 工具。
bootstrap 复用已存在的 uv/Python，把 libclang/CMake 安装到项目 target 目录，不安装到系统 Python。
本次工具版本：Rust stable 1.99.0、uv 0.11.11、Python 3.13.12、libclang 18.1.1、CMake 4.4.4；
vcpkg 基线为 `9e593bb18ea69cc5095e012465dcd675a822ed0d`。stable 不是永久固定版本，重新初始化时
应记录实际 Rust 版本并复验 feature off/on，不能把一次构建结果当成所有未来工具链的保证。

初始化时首次 `uv sync` 使用了 uv 默认缓存和机器已有的受管 Python。
后续统一走本项目脚本。默认 uv 缓存与受管 Python 可能与其他项目共用，不能为了清理本项目直接删除。

## 运行时数据

正式运行数据单独位于 `%LOCALAPPDATA%/RustDeskMCP/`：DPAPI 加密凭据配置、SQLite 操作/审计记录。
目录 ACL 限当前用户及 SYSTEM。明文密码不写入仓库、构建日志或命令行参数。
测试使用显式 `--data-dir` 的临时目录，不复用正式历史。
Rust 层在任何配置初始化之前使用独立 `RustDeskMCP` 应用命名空间，
自身身份/TOML 位于 `%APPDATA%/RustDeskMCP/config/`，日志位于同产品的 `log/`；
official 模式只读官方连接设置/认证 hash，profile 使用自己的 DPAPI 凭据；不写回官方 PeerConfig。
上游仍可能留下本产品的 Windows 最近文档记录，详见实施记录中的回归面。

## 使用

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File mcp/scripts/bootstrap-build.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File mcp/scripts/dev.ps1 python -m unittest discover -s mcp/server/tests -v
```

PowerShell 7 也可以使用 `pwsh -NoProfile -File ...`。不要使用 `setx` 或向 profile 写入工具路径。

## 清理

停止 MCP 和构建进程后，删除本项目 `build/mcp-tools/`、`build/c/`、`mcp/server/.venv/` 即可移除独立工具。
`target/` 是整个仓库的编译输出，可能与其他 RustDesk 开发共用；按需手动清理。
`build/mcp-dist/` 是交付物，保留或单独删除。
正式运行数据包含凭据/操作去重记录，不能随构建缓存一起清理，删除历史会失去重试保护。
