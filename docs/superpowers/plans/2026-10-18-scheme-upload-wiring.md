# 方案上传：Agent 侧接线（远端能力已就绪）

> 远端已实现 `upload_scheme`（提交 `6baf7f7`）。本计划把 Agent 与前端接上，让"前端上传方案 → 远端落盘 → 可用于校验/发起任务"真正生效。

**Goal:** 前端上传的自定义方案经 Agent 带口令转发到远端能力服务并落盘；方案列表/校验/任务随之生效。

**当场契约（已在远端验收）**
- 工具 `upload_scheme(name, content, type?, overwrite?)`；`name` 单层 `.json`，`content` 为方案 JSON 文本，`type ∈ {cleaning_rules, scoring_scheme}`，`overwrite` 默认 false。
- 成功信封：`{ok, interface_version, scheme:{scheme_id,type,version,status,path,description}, validation:{passed,errors,warnings}, overwritten, size_bytes}`。
- 新错误码：`SCHEME_EXISTS`（同名且未允许覆盖）；另有 `CONFIG_INVALID`/`CONTENT_TOO_LARGE`/`FORBIDDEN_PATH`/`USAGE`/`UNAUTHORIZED`。
- 默认方案 `cleaning_rules.v1.json`、`scoring_scheme.v1.json` **不允许被上传覆盖**（即使 overwrite=true）。

## Global Constraints
- 口令绝不写入日志/信封；只经请求头。
- 前端看到的响应形状保持兼容（至少保留 `saved` 与 `validation`）。
- 失败如实转述远端 `error.message`，不改写结论。
- 用户可见文案不得出现「冒烟/对账/口径」。

---

### Task AG-21：Agent 侧（工具表 + 错误码 + 转发）

**Files:**
- Modify: `agent/mcp_tools.py`（新增 `upload_scheme`，source=mcp；总数 22→23）
- Modify: `agent/mcp_contract.py`（`STRUCTURED_ERROR_CODES` 增 `SCHEME_EXISTS`；`describe_error` 增文案）
- Modify: `agent/resources.py`（`upload_scheme(client, name, content, scheme_type=None, overwrite=False)`：有 client 走远端 `upload_scheme`；无 client 退回本机落盘）
- Modify: `agent/http_api.py`（`_post_upload`：读 multipart 的 `file`/`name` → 解码为文本 → 调 `resources.upload_scheme`；响应保留 `saved`+`validation`，并附 `scheme`/`overwritten`/`size_bytes`）
- Test: `agent/tests/test_mcp_tools.py`（计数 23、含 upload_scheme）、`agent/tests/test_resources_api.py`（假 client 转发成功/`SCHEME_EXISTS` 透传/无 client 退回本机）、`agent/tests/test_mcp_errors.py`（新码）

**Produces:** `resources.upload_scheme(...)` 返回 `{saved:{name,path}, validation:{...}, scheme:{...}, overwritten, size_bytes}`。

- [ ] 写失败测试 → 跑失败 → 实现 → 跑通过 → 提交（`python -m pytest agent/tests -q` 全绿）。

### Task FE-10：前端

**Files:**
- Modify: `frontend/src/api/http.ts`（`ERROR_CODE_TEXT` 增 `SCHEME_EXISTS`）
- Modify: `frontend/src/api/types.ts`（`SchemeUploadResponse` 增可选 `scheme`/`overwritten`/`size_bytes`；`saved`/`validation` 保留）
- Modify: `frontend/src/components/resources/SchemeSection.vue` 与 `frontend/src/stores/resources.ts`（上传成功后刷新方案列表；如响应含 `overwritten` 就提示"已覆盖同名方案"；把任何"仅本机/暂存"措辞改为"已上传到远端能力服务"）
- Test: 前端 `npm run test` 全绿

- [ ] 写/改测试 → 跑 → 实现 → 跑通过 → 提交。

### Task DOC：文档同步
- 根 `README.md`：能力计数 22→23；§六 上传方案一行去掉"本机暂存"表述；§十二 已知限制第 3 条改为"已打通"。
- `docs/agent/Agent_接口文档.md`：`POST /api/schemes/upload` 说明改为"经远端能力服务落盘"。

### Task VERIFY：一次真实远端联调
- 用假客户端以外的方式：直接经 Agent 的 `resources.upload_scheme(client,...)` 上传一份自定义清洗方案（带 `scheme_id`）→ 断言返回成功 → `list_schemes` 能看到它。用后删除该方案文件（避免污染远端），或使用可辨识的一次性文件名。
