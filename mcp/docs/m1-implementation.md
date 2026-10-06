# M1 / P0 实施记录

本文保留首期实现记录；0.2.0 的完整工具/多会话/文件/部署说明见 [交付手册](delivery-guide.md)。
以下“首期/未开放”只描述 P0 历史状态，不代表本版能力，当前实测见 validation-record.md。

## 本次范围

依据 PRD v0.3 与 interface-review 实现 Windows x64 bridge 和 Python MCP 的 7 个 P0 工具。
2026-10-06 用户补充：先完成 exe 和 MCP，再联调，因此代码与构建可以并行推进；
源码、自动验证、真实被控端验收分别记录，不把未联调的能力标记为已验收。

## 代码边界

- `src/mcp_bridge/`：独立 UI 回调适配器、有界事件、单连接鉴权 RPC、连接/终端原语。
- `src/mcp_bridge_main.rs`：独立控制台 exe，避免依赖 Flutter UI 和便携包装器的管道行为。
- Cargo 默认关闭 `mcp-bridge`；普通 exe 也提供 feature 开启时的 `--mcp-bridge` 入口。
- 用户 2026-10-07 要求独立存储，并补充可选官方来源：首次初始化前使用 RustDeskMCP 命名空间，
  身份/配置/日志独立；official 只读官方设置及 peer 认证 hash，profile 使用本项目 DPAPI 配置。
  来源明确、不自动回退，官方应用 PIN 阻止读取凭据，终端恢复 ID/persistent 始终过滤。
- `src/client.rs`：仅 feature 开启且 MCP 会话时，PeerConfig 读写限于私有内存快照；
  终端恢复 ID 与 persistent 选项强制清空。不修改普通会话配置写回，也不在退出时恢复旧快照。
- Python：官方 MCP SDK，执行包装/分帧、SQLite 操作意图、默认只读、凭据引用、人工批准页。
- 不修改共享 trait、函数签名、protobuf、生成桥接代码或 hbb_common 子模块。

## 构建与验收

本机 Visual Studio C++ 已安装；Rust/vcpkg 工具放在忽略的 `build/mcp-tools/` 下，
Cargo 使用短路径 `build/c/` 避免 Windows 子模块路径上限；不改注册表或全局 Git 配置。
构建脚本只设置自身进程环境。提供不发布 release 的 Windows CI Artifact 工作流。

本地自动验证包括 MCP 握手/7 工具发现、分片 UTF-8/哨兵、忙状态、操作重试/重启 unknown、
只读拒绝和批准绑定完整操作、默认 feature off 编译及 feature on 构建。

真实验收在生成 exe 后进行：无窗口连接、认证、中文 I/O、身份/权限、断线与取消、
用户终端恢复配置不受影响。测试凭据不进入此文件或源码。
当前已通过三种配置来源的真实连接、连续查询、Unicode 输出和正常关闭；未完成项见验证记录。

## 已知边界

首期一个 peer/一个非持久终端；不支持文件工具和多 peer。PTY 合并输出。
终端 close 回执确认终端结束，不代表回滚修改或结束所有脱离终端的子进程。
应用设置了访问 PIN 时默认拒绝无头连接，暂不提供 PIN 解锁流程。
本版显式 elevate 拒绝，管理员登录的连接级人工批准流程尚未开放；普通终端实际身份通过探针返回。
不能依靠源码检查确认远端版本/权限及发行包管道；以实际联调记录为准。
