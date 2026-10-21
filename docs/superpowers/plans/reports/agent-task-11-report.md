# Agent 远端 MCP 接入 · Task 11 报告：已发布数据/方案/产物清单改远端来源

计划：`docs/superpowers/plans/2026-10-18-agent-remote-mcp.md` → 「### Task 11: 已发布数据/方案/产物清单改远端来源」

## 目标

把资源/列举类端点的数据来源从**本机文件系统扫描**切到**远端能力服务工具调用**，
同时**保持前端已消费的 HTTP 响应形状不变**（前端本轮零改动）。远端失败如实转述
结构化错误信封，绝不编数字、绝不拿空成功冒充。

## 权威远端形状（读自服务端）

- `hadoop/mcpserver/readers.py`
  - `list_published_versions` → `{published_dir, exists, versions:[{data_version, path,
    tables:[{name,file,size}], other_files, published_at, missing_tables, source_task_id}], count}`
  - `list_task_files` → `{task_id, files:[{path,size,kind}], count}`
  - `read_task_file` → `{task_id, path, kind, size, previewable, content?}`
  - `list_raw_datasets` → `{exists, datasets:[{name, path, files:[{name,size,updated_at,kind}],
    file_count, size, updated_at}], files, count, file_count, empty}`
  - `get_scheme_content` → `{name, path, size, content}`
- `hadoop/mcpserver/writers.py::handle_list_schemes` → `{schemes: [...]}`
- 失败一律 `{"ok": false, "error": {"code", "message", ...细节（如 reason_kind）}}`

## 改动

### 1. `agent/resources.py` —— 远端调用接缝 + 六处来源改写

- 新增 `_remote(client, name, args=None)`：统一远端调用出口。
  - `client is None`（本机未接远端）→ `ResourceError("MCP_UNAVAILABLE", …)`，不假装成功；
  - 远端 `ok=false` → 原样转述 `error.code` / `error.message` 与其余细节（含 `reason_kind`）；
  - 非对象信封 → `DRIVER_BAD_OUTPUT`。
  - 通过 `ResourceError.envelope()` 输出前端既有的 `{"ok":false,"error":{code,message,…}}`。
- `list_raw(client)`：改调 `list_raw_datasets`，映射为既有形状
  `{dir, datasets:[{name,path,files:[{name,size,updated_at}],size,updated_at}]}`；
  `dir` 远端不提供时给空串。删除本地扫描 `_dataset_entry`。
- `list_published(client)`：改调 `list_published_versions`，`versions` → `datasets`，
  仅保留既有字段 `data_version/source_task_id/path/tables[{name,file,size}]/published_at`。
- `list_schemes(client)`（新增）：改调 `list_schemes`，宽松形状（`schemes`/`items`/`list`/`data`
  任一；都没有时回空 `schemes`）。
- `scheme_content(client, name)`：改调 `get_scheme_content`，形状 `{name,path,content}`；
  取代本地 `read_scheme_content`。
- `list_task_files(client, task_id)`：改调远端 `list_task_files`（`task_id` 为 hadoop_task_id），
  形状 `{task_id, files:[{path,size,kind}]}`。原本地扫描更名为 `local_list_task_files`，
  仅供 `runtime/artifacts_ref` 本机收集对话可见产物使用。
- `preview_file(client, task_id, relpath)`：改调 `read_task_file`，形状
  `{path,previewable,kind,size?,content?,truncated?}`；二进制由远端回 `previewable:false`。
- **保留本机**：`save_scheme` / `_validate_saved`（方案上传）、`resolve_file` /
  `download_target` / `task_dir` / `kind_of`（下载中转留待 Task 12 另行改造）。

### 2. `agent/runtime/runtime.py` —— 暴露注入的客户端

- `Runtime.__init__` 增加 `self.mcp_client = mcp_client`：http_api 经
  `self._get_runtime().mcp_client` 取得远端客户端。注入自定义 `tool_backend` 而未给
  `mcp_client` 时为 `None`（资源端点按 `MCP_UNAVAILABLE` 诚实报错）。

### 3. `agent/http_api.py` —— 路由改接远端

- `_resource_call(func, *args)` 改为：先取运行时与 `mcp_client`，再以 `func(client, *args)`
  调用；运行时不可用或资源函数抛 `ResourceError` → 结构化错误信封。
- `_get_resources`：
  - `GET /api/data/raw` → `resources.list_raw`
  - `GET /api/data/published` → `resources.list_published`
  - `GET /api/schemes` → `resources.list_schemes`（新增精确分支，优先于 legacy）
  - `GET /api/schemes/content?name=` → `resources.scheme_content`
  - `GET /api/tasks/{tid}/files` → `resources.list_task_files`（tid 为 hadoop_task_id）
  - `GET /api/files/preview?task_id=&path=` → `resources.preview_file`
- 小清理：删除 `_post_upload` 上已死的 `path` 形参（调用点由 `self._post_upload(u.path, length)`
  改为 `self._post_upload(length)`）。

### 4. `agent/runtime/artifacts_ref.py`

- 默认 `lister` 由 `resources.list_task_files` 改为 `resources.local_list_task_files`
  （本机收集对话可见产物仍读本机 `var/tasks`）；同步更新 docstring。

### 5. `agent/tests/test_resources_api.py` —— 用假客户端（不连网）

- 新增 `FakeMcpClient`（按工具名回罐头信封、记录调用参数）与 `StubRuntime`（只暴露
  `mcp_client`），经 `create_server(runtime=…)` 注入并保存/还原全局 `_RUNTIME`。
- 覆盖：原始数据映射与空、已发布映射与空、方案列表/内容映射、产物清单映射、预览
  可预览/不可预览、下载本机中转；每类均含**远端失败透传**（`DEPENDENCY_MISSING` /
  `DRIVER_UNREACHABLE` / `FILE_NOT_FOUND` / `FORBIDDEN_PATH`（带 `reason_kind`）/
  `TASK_NOT_FOUND`），并断言传给客户端的参数名（`task_id` / `name` / `path`）。
- 方案上传三条用例保留（仍为本机落盘 + 现有校验）。

## 验证

```
python -m pytest agent/tests/test_resources_api.py -q
22 passed
```

```
python -m pytest agent/tests -q
371 passed, 237 subtests passed
```

（基线 366 passed；本轮净增 5。全绿，无失败/报错。）

## 约束核对

- 仅本地改动，**未对远端 `127.0.0.1:8900` 发起任何调用**；测试全部使用假客户端/假信封。
- 出口形状逐项对照计划给定形状，未改前端契约（前端本轮零改动）。
- 远端失败原样转述（码 + 说明 + 细节），未编数字、未拿空成功冒充。
- 未使用「冒烟 / 对账 / 口径」等受限词；口令不涉及（未构造真实客户端）。
- 完成计划点名的小清理：`_post_upload` 死形参 `path` 已删。

## 遗留 / 关注

- `GET /api/schemes/`（带尾斜杠）仍落 legacy `tools.list_schemes()`；前端用无尾斜杠路径，
  未改。
- **下载**（`/api/files/download`）仍是本机中转，待 Task 12 改远端流式。
- `list_schemes` 为宽松形状；若远端将来只回 `schemes`，前端按既有多键回退亦可消费。

## 提交

一个本地提交（不推送）：见提交记录。

---

# 修复补记：产物/结果默认来源切到远端（Task 11 复核项）

任务现跑在**远端**服务，而 Agent 侧仍有代码读**本机**任务目录（`var/tasks/<id>/...`），
导致远端任务静默收不到产物、拿不到结果。本次把产物/结果管线改为**从远端客户端取**，
未配置（`Runtime.mcp_client is None`）时保持优雅降级、不崩。改动保持克制，沿用既有形状。

## 改了什么

### 1. `agent/runtime/artifacts_ref.py`

- 新增 `_call_remote(client, name, args)`：安全调远端，异常 / 非对象信封一律吞掉返回 None。
- 新增 `remote_task_entries(client, task_id, result=None)`：优先取远端 `get_task_result`
  结果信封自带的 `artifacts[]`（服务端已按同一套排除规则过滤控制文件 / 隐藏文件 /
  校验和 / 临时残留）；其结果信封不可用时退回远端 `list_task_files`；都取不到返回空表。
- `collect_task_artifacts(task_id, lister=None, client=None, result=None)`：有 `client`
  时走远端收集，无 `client` 时保持原有本机 `lister`（缺省
  `resources.local_list_task_files`）行为。每条引用仍由 `build_task_artifact` 构造，
  故都带 `task_id`（= 传入任务号，即 hadoop 编号），并继续用 `is_internal_file` 剔除内部文件。
- `read_task_result(task_id, client=None)`：有 `client` 时经远端 `get_task_result` 取结果
  信封；无 `client` 时读本机 `result.json`（既有行为不变）。
- 新增 `remote_scheme_names(client)`：从远端 `list_schemes` 宽松归纳方案名（兼容字符串
  与带 `name`/`file` 的对象）；失败返回 None。
- `scheme_exists(name, client=None, available=None)` / `collect_scheme_artifacts(names,
  client=None, available=None)`：存在性判定优先调用方给的 `available`，其次远端
  `list_schemes`，都取不到才退回本机 `config/`——远端独有方案不再被本机判定丢弃。
- 同步更新模块 docstring 的数据来源说明。

### 2. `agent/runtime/runtime.py`

- `_gather_artifact_refs`：`collect_task_artifacts` / `collect_scheme_artifacts` 均传入
  `client=self.mcp_client`。`LongTaskManager.finalize`/`_collect_artifacts`
  （`long_task.py:457,470`）经 `runtime._finalize_turn` 走的就是这条路径，故一并改为远端来源。
- 新增 `_degrade_result(conversation_id, meta)`：无模型收尾取真实结果时，远端优先用长任务
  记录里已存的远端结果（`RemoteTaskProbe` 取到后写入 `record["result"]`，随 `finalize`
  落盘），取不到再经客户端 `get_task_result` 现取；未接远端时保持读本机 `result.json`。
- `_degrade_continuation`：改调 `_degrade_result`，使确定性完成说明带出远端任务的计数与五维分。

## 新增 / 扩展测试（假客户端，不连网）

`agent/tests/test_artifacts_ref.py`：新增 `FakeRemoteClient` 与 `remote_result_envelope()`，
并给 `ArtifactTestBase` 增加可覆盖的 `_mcp_client()` 钩子。新增：

- `TestRemoteArtifactCollection::test_remote_task_artifacts_from_remote_result`：
  远端任务的产物从远端结果信封收集，每条 `source=task_file` 引用的 `task_id` == hadoop
  编号、`id` 稳定；内部文件（`driver.log`）被剔除；方案依据取自远端 `list_schemes`；
  `artifact_added` 照发。
- `TestRemoteArtifactCollection::test_remote_falls_back_to_list_task_files`：
  `get_task_result` 不可用时退回远端 `list_task_files`。
- `TestRemoteDegradedCompletion::test_degraded_message_includes_remote_counts_and_scores`：
  无模型时经远端探针推进终态，确定性完成消息含远端真实计数与综合分（`0.88` / `50000`），
  远端产物收入产物列表。
- `TestUnconfiguredClientDegrades::test_no_client_degrades_without_artifacts`：
  未接远端（client None）优雅降级——仍给完成消息、无任务产物、不崩。

## 验证

```
& "E:\学\大数据分析\movielens_agent_for_big_data_analysis\.scratch\langgraph-probe\.venv\Scripts\python.exe" -m pytest agent/tests/test_artifacts_ref.py -q
30 passed in 3.94s
```

```
& "E:\学\大数据分析\movielens_agent_for_big_data_analysis\.scratch\langgraph-probe\.venv\Scripts\python.exe" -m pytest agent/tests -q
375 passed, 237 subtests passed in 39.46s
```

（基线 371 passed；本轮净增 4。全绿，无失败/报错。）

## 约束核对

- 未对远端 `127.0.0.1:8900` 发起任何调用：测试全部用假客户端；本次改动只在收到显式
  `mcp_client` 时才触远端。
- 未打印 / 记录任何口令；未构造真实客户端。
- 措辞未使用受限词（冒烟 / 对账 / 口径）。

## 关注 / 遗留

- 远端任务收集时，`_gather_artifact_refs` 会对每个任务号各调一次 `get_task_result`；
  任务多时是 N 次只读调用。当前回合粒度可接受，若后续需要可在回合内缓存结果信封
  （`collect_task_artifacts` 已预留 `result=` 形参）。
- 远端 `list_schemes` 未列出默认方案时，默认方案将不再作为「依据」出现——这是服务端的
  事实来源，符合「以远端为准」的要求。
- 远端结果信封若缺 `counts`/`scores`，确定性完成说明相应字段不写（不编数字，沿用解释层铁律）。
