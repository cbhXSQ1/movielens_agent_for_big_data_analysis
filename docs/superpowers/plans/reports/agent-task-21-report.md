# Agent Task 21 — 报告

## 标题
把远端已就绪的 `upload_scheme` 能力接到 Agent：工具表第 23 项 + `SCHEME_EXISTS` 错误码 + 上传转发

## 背景
远端（提交 `6baf7f7`）已实现 `upload_scheme(name, content, type?, overwrite?)`：`name`
为单层 `.json`，`content` 为方案 JSON **文本**，`type ∈ {cleaning_rules, scoring_scheme}`
可选，`overwrite` 默认 false；成功信封含 `scheme` / `validation` / `overwritten` /
`size_bytes`；同名未允许覆盖回 `SCHEME_EXISTS`；默认方案不允许被覆盖。本任务把 Agent 与前端
接上，让"前端上传方案 → 远端落盘 → 可用于校验/发起任务"真正生效。

## 变更

### 1. `agent/mcp_tools.py` — 工具表第 23 项
- 新增 `upload_scheme`（`source=mcp`、`long_running=false`、`estimated_seconds=3`）：
  - `name`（string，必填）、`content`（string，必填）；
  - `type`（enum：`cleaning_rules` / `scoring_scheme`，可选）；
  - `overwrite`（bool，默认 `false`，可选）。
- 注释 `22 项` → `23 项`。声明集合与远端能力自描述一致，`verify_tools` 保持 ok。

### 2. `agent/mcp_contract.py` — 结构化错误码
- 新增 `CODE_SCHEME_EXISTS = "SCHEME_EXISTS"` 并收进 `STRUCTURED_ERROR_CODES`。
- `ERROR_MESSAGES` 增可读中文：「同名方案已存在：请改名后重试，或允许覆盖同名方案。」
  （不含受限词；`describe_error` 直接取用）。

### 3. `agent/resources.py` — 上传转发 / 本机回退
- 新增 `upload_scheme(client, name, content_text, scheme_type=None, overwrite=False)`：
  - 先做本地校验（非空、单层、`.json` 扩展名），非法 → `USAGE` / `UNSUPPORTED_FILE`；
  - **有客户端**：`client.call_tool("upload_scheme", {...})`，`type` 仅在给出时上送、
    `overwrite` 仅在开启时上送；`ok=false` 时经 `_remote` 抛 `ResourceError`，**原样保留**
    远端 `code`/`message`；成功返回
    `{saved:{name,path}, validation, scheme, overwritten, size_bytes}`
    （`path` 取远端 `scheme.path`，缺失回退 `config/<name>`）；
  - **无客户端**（离线/单测）：退回 `_save_scheme_local`（写 `config/` + 本地校验）。
- 原 `save_scheme(parts)` 删除（已无调用方），其落盘逻辑收敛到 `_save_scheme_local`。
- 模块顶部说明补一句「方案上传经远端 `upload_scheme` 落盘，无客户端时退回本机」。

### 4. `agent/http_api.py` — `_post_upload`
- 保留请求体大小上限守卫；
- 从 multipart 读取 `file`（文件名可被可选 `name` 字段覆盖），按 UTF-8 解码为文本
  （`errors=replace`）作为 `content`；
- 经 `self._runtime_or_error()` 取运行时的 `mcp_client`，调用
  `resources.upload_scheme(client, name, content)`；
- 响应保持向后兼容：`{ok, saved, validation, scheme, overwritten, size_bytes}`；
  `ok` 取 `validation.passed`（无则 `validation.ok`）；失败回结构化错误信封（保留远端码）。
- `POST /api/schemes/upload` 仍是唯一上传路由。

### 5. `agent/tool_policy.py` — 策略表补齐（测试倒逼）
- 策略表与工具表一一对应，新增 `"upload_scheme": {}`（写侧登记，不占任务锁、不读原始数据）。
  这是全量套件暴露的必需项（`test_tool_policy` / `test_stage2_policy_facts`）。

### 6. `agent/runtime/tool_exec.py`
- 注释 `22 项` → `23 项`（文档一致性，无行为变更）。

## 测试（TDD：先写失败 → 实现 → 转绿；假客户端，不连网）
- `agent/tests/test_mcp_tools.py`：计数 22 → **23**；新增 `upload_scheme` 形状用例
  （source / 非长耗时 / 3 秒 / 必填 `name,content` / `type` 枚举 / `overwrite` 默认 false）。
- `agent/tests/test_tool_specs.py`：总数 23 → **24**（23 远端 + 快速演示）。
- `agent/tests/test_mcp_errors.py`：`NEW_CODES` 增 `SCHEME_EXISTS`；新增
  `describe_error(SCHEME_EXISTS)` 可读且不回退原始 message 的用例。
- `agent/tests/test_resources_api.py`（真 HTTP，假远端客户端）：
  - `test_upload_relays_to_remote`：转发到远端，断言映射后的 `saved/validation/scheme/
    overwritten/size_bytes`，且上送参数恰为 `name`+`content`（未给 `type`/`overwrite` 不上送）；
  - `test_upload_scheme_exists_passthrough`：远端 `ok=false` + `SCHEME_EXISTS` 如实透传同码；
  - `SchemeUploadLocalTest`（`mcp_client=None`）：本机回退仍落盘 + 校验（合法通过；
    非法 JSON 回 `ok=false` 但仍带 `saved/validation`）；
  - `test_upload_non_json_rejected` 保留（扩展名校验在本端先于转发）。
  - 基础类抽出 `_start(runtime)` 以便注入 `None` 客户端；全局 `_RUNTIME` 由 cleanup 还原。

## 验证
- 全量本地套件（repo 根）：
  `& "E:\学\大数据分析\movielens_agent_for_big_data_analysis\.scratch\langgraph-probe\.venv\Scripts\python.exe" -m pytest agent/tests -q`
  → **430 passed, 249 subtests passed**（基线 426；本次净 +4），保持全绿、无网络。
  （中途出现一次 `test_stage3b_stream` 的 sqlite「database is locked」偶发，单测重跑即过，
  与本次改动无关。）
- 令牌未写入任何日志/信封；本端文案不含「冒烟/对账/口径」。

## 影响面
- 仅 `agent/` 源码与测试；未触碰前端、`hadoop/`、`config/`（上传路径的远端能力由对端负责）。
- 前端响应形状向后兼容（至少保留 `saved` + `validation`），新增 `scheme`/`overwritten`/
  `size_bytes` 供前端展示覆盖提示。

## 遗留 / 关注点
- 本端保留一层轻校验（`.json` 扩展名、单层名），远端的 `CONTENT_TOO_LARGE`/
  `FORBIDDEN_PATH`/`CONFIG_INVALID` 等仍按远端原样透传。
- 前端接线（FE-10）与文档同步（DOC）、真实远端联调（VERIFY）为同一计划的其它任务，不在本次范围。

## 提交
一次性本地提交，仅含 agent 源码与测试（及本报告），未推送。
