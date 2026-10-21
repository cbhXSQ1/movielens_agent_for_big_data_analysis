# Agent Task 22 — 报告

## 标题
把远端已就绪的 `delete_scheme` 能力接到 Agent：工具表第 24 项 + 删除转发 / 本机回退

## 背景
远端（提交 `b4c0d02`）已实现 `delete_scheme(name)`：`name` 为单层 `.json` 文件名；
成功信封含 `scheme` / `deleted: true` / `note` / `schemes`（删除后的方案列表）；
错误语义：`USAGE`（默认方案不可删 / 非普通文件）、`FORBIDDEN_PATH`（名字非法 / 越界 /
符号链接）、`TASK_ALREADY_RUNNING`（有任务在跑，错误带 `occupier`）、`FILE_NOT_FOUND`
（不存在，错误带 `existing`）、`INTERNAL_ERROR`。随仓库交付的两份默认方案永不可删。
本任务按 `upload_scheme` 的既有接线方式，把删除能力接到 Agent 侧。

## 变更

### 1. `agent/mcp_tools.py` — 工具表第 24 项
- 新增 `delete_scheme`（`source=mcp`、`long_running=false`、`estimated_seconds=2`）：
  - `name`（string，必填，单层 `.json` 文件名）；
- 注释 `23 项` → `24 项`。声明集合与远端能力自描述一致，`verify_tools` 保持 ok。

### 2. `agent/tool_policy.py` — 策略表补齐
- 新增 `"delete_scheme": {}`（写侧，但不占任务锁、不读原始数据；占用判定由远端先行把关）。
  这是全量套件暴露的必需项（`test_stage2_policy_facts.test_policy_and_specs_one_to_one`
  强制 `set(TOOL_SPECS) == set(POLICY)`）。

### 3. `agent/resources.py` — 删除转发 / 本机回退
- 新增 `delete_scheme(client, name)`：
  - 先做轻校验（非空、单层、`.json` 扩展名），非法 → `USAGE` / `UNSUPPORTED_FILE`；
  - **有客户端**：`client.call_tool("delete_scheme", {"name": filename})`；`ok=false` 时
    经 `_remote` 抛 `ResourceError`，**原样保留**远端 `code`/`message` 与其余细节
    （如 `occupier` / `existing`）；成功返回
    `{"deleted": True, "scheme": …, "schemes": …, "note": …}`；
  - **无客户端**（离线/单测）：退回 `_delete_scheme_local`。
- 新增 `_default_scheme_names()`（唯一来源：`agent.tools` 的 `DEFAULT_RULES`/`DEFAULT_SCORING`
  基名）与 `_delete_scheme_local(filename)`：默认方案 → `USAGE`；不存在 → `FILE_NOT_FOUND`；
  非普通文件 → `USAGE`；否则删 `config/<name>`，回同形状（`scheme`/`schemes` 为 None）。
- 模块顶部说明补一句「删除经远端 `delete_scheme`，无客户端时退回本机」。

### 4. `agent/http_api.py` — `POST /api/schemes/delete`
- 新增 `_post_delete_scheme(body)`：从 JSON body 取 `name`，经 `resources.delete_scheme`
  转发（无远端客户端时退回本机删除）；成功回 `{"ok": true, deleted, scheme, schemes, note}`，
  失败如实回结构化错误信封并保留远端码。
- 在 `do_POST` 的 JSON 分派段加路由（`/api/chat` 之前）；模块顶部路由表补一行。

### 5. `agent/runtime/tool_exec.py`
- 注释 `23 项` → `24 项`（文档一致性，无行为变更）。

## 测试（TDD：先写失败 → 实现 → 转绿；假客户端，不连网）
- `agent/tests/test_mcp_tools.py`：计数 23 → **24**；新增 `delete_scheme` 形状用例
  （source / 非长耗时 / 2 秒 / 必填 `name` / `name` 为 string）。
- `agent/tests/test_tool_specs.py`：总数 24 → **25**（24 远端 + 快速演示）。
- `agent/tests/test_resources_api.py`（真 HTTP，假远端客户端）：
  - `test_delete_relays_to_remote`：转发到远端，断言映射后的 `deleted/scheme/schemes/note`
    与上送参数恰为 `name`；
  - `test_delete_task_already_running_passthrough`：远端 `ok=false` + `TASK_ALREADY_RUNNING`
    如实透传同码，并保留 `occupier`；
  - `test_delete_file_not_found_passthrough`：远端 `FILE_NOT_FOUND` 如实透传同码，并保留
    `existing`；
  - `test_delete_non_json_rejected_without_remote_call`：非 `.json` 在本端先拒，不触发远端调用；
  - `SchemeDeleteLocalTest`（`mcp_client=None`）：本机回退删除本地文件；默认方案名被拒且文件仍在。
  - 基础类新增 `post_json` 辅助。

## 验证
- 全量本地套件（repo 根）：
  `& "E:\学\大数据分析\movielens_agent_for_big_data_analysis\.scratch\langgraph-probe\.venv\Scripts\python.exe" -m pytest agent/tests -q`
  → **437 passed, 256 subtests passed**（基线 430；本次净 +7），保持全绿、无网络。
- 令牌未写入任何日志/信封；本端文案不含受限词（已 grep 复核）。

## 影响面
- 仅 `agent/` 源码与测试（及本报告）；未触碰前端、`hadoop/`、`config/`。
- 远端错误码 `USAGE` / `FORBIDDEN_PATH` / `TASK_ALREADY_RUNNING` / `FILE_NOT_FOUND` /
  `INTERNAL_ERROR` 本端均已收录（`mcp_contract`），无需新增常量。

## 遗留 / 关注点
- 本端保留一层轻校验（`.json` 扩展名、单层名），远端护栏（默认方案、越界、占用、存在性、
  普通文件）仍按远端原样透传；本机回退只覆盖默认方案 + 存在性 + 普通文件。
- 前端接线与真实远端联调为同一计划的其它任务，不在本次范围。

## 提交
一次性本地提交，仅含 agent 源码与测试（及本报告），未推送。
