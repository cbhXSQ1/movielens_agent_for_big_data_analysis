# Agent Task 19 — 报告

## 标题
修复刷新后远端日志产物消失：为远端来源产条目加 `remote_source` 标记，`strip_internal` 跳过之

## 背景 / 现象
Task 18 已让**收集路径**（`collect_task_artifacts`）不再对远端来源叠加「内部文件」过滤，
远端 `logs/**` 日志（合法产品，类别 `日志`）能进入回合信封与 `artifact_added` 事件。

但 `Runtime.visible_artifacts`（`GET /api/conversations/{cid}/artifacts` 恢复）与
`Runtime._gather_artifact_refs`（下一回合已知产物列表）都对持久化列表再跑一次
`artifacts_ref.strip_internal`，而该函数**对来源无感知**，内部仍用 `is_internal_file`
剔除 `logs/**` / `driver.log` / `metadata.json` / `published_hashes.json`。于是：

- 页面刷新后，恢复接口返回的产物列表里远端 `logs/**` 日志消失（标签消失）；
- 已知列表被剔后，下一回合远端重新收集到这些日志，导致重复发 `artifact_added`。

## 根因
`strip_internal` 的过滤规则是**来源无关**的：它只按 `source == task_file` + 路径判断，
无法区分一条引用来自「服务端产品清单（已过滤）」还是「本机回退（需过滤）」。收集阶段
已经知道来源，但这个信息没有被持久化到引用上，故恢复阶段无从判断。

## 修复（仅 `agent/runtime/artifacts_ref.py`）
1. **加性内部标记**：`build_task_artifact(..., remote_source=False)` 新增关键字参数；
   为 `True` 时在引用对象上追加一个**加性字段** `"remote_source": True`。字段只在远端
   来源出现，本机来源的引用对象**逐字段不变**。
2. **远端路径打标**：`collect_task_artifacts` 远端分支（`client is not None`，来源为
   `get_task_result.artifacts[]`，取不到时退回远端 `list_task_files`）调用
   `build_task_artifact(..., remote_source=True)`。
3. **`strip_internal` 跳过远端来源**：过滤条件增加 `not item.get("remote_source")`
   前置判断 —— 带标记的条目视为服务端产品清单，原样保留；不带标记（本机来源）的条目
   过滤逻辑**与修复前完全一致**。
4. **保持 Task 18 行为**：远端 `category` / `title` 照旧透传，`task_id` 仍取 hadoop 编号。
5. **未改前端**：`remote_source` 为未知字段，前端类型忽略之，展示与类型不受影响。

## 测试（TDD，全部离线，假客户端，不连网）
`agent/tests/test_artifacts_ref.py` 新增 3 例：

- `TestInternalStrippingAndTitles.test_remote_built_log_ref_survives_strip_internal`：
  假远端结果 `artifacts[]` 含 `logs/driver.log` → `collect_task_artifacts` 造出的引用带
  `remote_source`，经 `strip_internal` 后**仍保留**。**修复前失败**（引用无标记；即使有也
  会被删）。
- `TestInternalStrippingAndTitles.test_local_built_log_ref_still_stripped`：
  本机构造（无标记）的 `logs/driver.log` / `driver.log` 经 `strip_internal` **仍被剔除**，
  `status.json` 保留。**修复前后皆通过**（守 Local 行为不变）。
- `TestRemoteArtifactCollection.test_remote_log_artifact_survives_restore`：
  跑一轮远端长任务后，`Runtime.visible_artifacts(cid)` 恢复出的任务产物里仍含
  `driver.log` 且类别为 `日志`。**修复前失败**（恢复列表无 `driver.log`）。

前两例与第三例中「远端保留」「恢复保留」两条在修复前按预期失败（RED），修复后全绿。

## 验证
- 相关用例：`agent/tests/test_artifacts_ref.py` → `40 passed`。
- 全量本地套件（repo 根）：
  `& "...\.scratch\langgraph-probe\.venv\Scripts\python.exe" -m pytest agent/tests -q`
  → `417 passed, 240 subtests passed`（基线 414，本次净 +3），保持全绿、无网络。

## 影响面
- 仅 `agent/runtime/artifacts_ref.py` 与配套测试。
- 远端产物引用多一个加性字段 `remote_source`（前端忽略），刷新恢复与下一回合已知列表
  不再误删远端日志；本机回退（`client=None`）过滤行为不变。

## 遗留 / 关注点
- `remote_source` 随引用写入对话 `context_json`，老对话中已持久化的远端产条目**不含**该
  字段，故恢复时仍按旧规则过滤（老数据保持既有表现）；新收集的远端产条目端到端一致。
- 方案「依据」引用（`source=scheme`）不参与内部过滤，未加该字段，行为不变。

## 提交
一次性本地提交，仅含 agent 文件（`agent/runtime/artifacts_ref.py`、
`agent/tests/test_artifacts_ref.py`），未推送。
