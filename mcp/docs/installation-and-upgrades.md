# 与官方版共存、版本管理与升级

## 当前交付方式

首期交付 **独立控制台 bridge exe + Python MCP**，不交付 RustDesk GUI 安装器。
不安装/卸载官方应用，不注册服务、URI handler 或文件关联，不覆盖官方 exe/DLL。
bridge 启动不会运行官方自动更新器。机器原有 RustDesk 服务和桌面客户端继续由官方安装维护。

MCP 自己的连接和非持久终端不接管桌面/普通终端会话。
按照用户 2026-10-07 补充，使用独立 `RustDeskMCP` 应用命名空间，
自身身份、运行配置与日志独立，不写回官方 PeerConfig。
服务器/凭据来源通过 official/profile 选项选择：只读复用官方配置或 MCP 自定义 profiles。
官方模式导入的网络快照写入 MCP 命名空间，peer 认证 hash 只进入连接内存。
用法与边界见 [configuration.md](configuration.md)。
上游最近文档机制仍会创建本产品的记录，测试时核实官方配置不变。

**如果以后构建普通 RustDesk GUI 安装包并沿用官方品牌/安装目录/服务名，它可能替换官方版。**
这不是本次 bridge 的部署方式。未来若要两套完整 GUI 并装，需另立安装包产品标识、服务、
协议/文件关联、配置/数据路径和更新渠道；不要简单把 exe 改名当作完整隔离。

## 四个版本

| 版本 | 当前值与用途 |
|---|---|
| RustDesk 源码基线 | `1.5.0`，源码提交与 dirty 标记写入 manifest |
| Bridge 版本 | `0.2.0`，本项目连接层实现版本 |
| A/B 协议 | `1.1`，major 不兼容拒绝启动，minor 兼容新增能力 |
| Python MCP | `0.2.0`，工具/执行/授权逻辑，依赖由 uv.lock 锁定 |

远端官方 RustDesk 版本另外由连接探测获得，不能用本地基线代替。
当前目录：`build/mcp-dist/0.2.0-rustdesk-1.5.0/`，包含 exe、wheel、manifest 与安装脚本；完整 ZIP 见交付手册。
正式分发必须更新本项目版本、构建目录、SHA256、commit 和测试记录，
不能把不同实现长期包装为同一个版本。

## 升级

1. 新版本解压到新目录，保留旧目录，不覆盖正在运行的 exe。
2. 停止 MCP，等待其关闭拥有的终端；确认没有正在等待批准或执行的操作。
3. 备份 `%LOCALAPPDATA%/RustDeskMCP/` 的 config.json 和操作数据库。
   SQLite WAL/SHM 文件需随数据库一致备份，最简单是在 MCP 完全退出后复制整个目录。
4. 修改 config.json 的 bridge_exe 为新版本绝对路径。
   交付包运行 install.ps1 创建新版本 runtime，并更新客户端 command/args；源码开发用 uv sync --locked。
   不要手工混搭未知版本的 exe/DLL。
5. 检查 major/能力，进行只读连接、身份、中文输出与官方配置隔离验收，再启用写操作。

没有自动追踪/覆盖官方 release 的更新逻辑。官方版自身升级与 bridge 升级互不替代。
bridge 源码更新从官方 tag 或选定 commit 合并，在隔离 worktree 做 feature off/on 和远端回归。

## 回退与记录

切回旧 exe 路径及旧 Python 代码/uv.lock，保留加密凭据与完整操作历史。
运行中的命令不能跨版本移交；重启后未完成操作为 unknown，从不自动重发。
不要为了回退删除/清空操作历史，也不要把缺少新操作记录的旧数据库直接恢复为活动数据库。

SQLite 当前 schema_version=1，未知 schema 在启动时拒绝使用。
未来迁移必须明确向后兼容性；无法安全回退时，应导出记录并使用兼容程序，
不能假装切回一个旧 exe 就能恢复全部业务状态。

Python 日常工具升级可独立于 Rust 重编译，只要 bridge major/capability 仍匹配；
上游 Session/InvokeUiSession/终端协议变化仍需适配和构建验收。
