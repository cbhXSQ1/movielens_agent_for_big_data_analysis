# Agent Task 18 — 报告

## 标题
修复远端产物日志被误删：远端收集路径不再叠加「内部文件」过滤

## 背景 / 现象
`agent/runtime/artifacts_ref.py::collect_task_artifacts` 在收集任务产物时，无论条目
来自何处，都先跑一遍 `is_internal_file`（`_INTERNAL_DIRS = ("logs",)`，
`_INTERNAL_BASENAMES = ("driver.log", "metadata.json", "published_hashes.json")`），
命中即跳过。

但远端 `get_task_result` 返回的 `artifacts[]` **本身就是产品清单**：服务端已按同一
套规则排除控制文件 / 隐藏文件 / 校验和 / 临时残留，并且按
`docs/hadoop/迭代二_对端改造说明.md` §4，这份清单**本就包含日志**（全量任务实测 47
项产物中日志占 29 项，类别 `日志`）；§9.1 要求前端把日志单独折叠成文件区。于是
Agent 侧这层多余的 `logs/**` / `driver.log` 过滤，把远端合法日志产物整条丢弃，前端
文件区里的日志因此缺失。

## 根因
`collect_task_artifacts` 旧实现里，内部文件判断位于远端 / 本机两条分支**之前**：

```python
rel = entry.get("path")
if not rel or is_internal_file(rel):
    continue
if remote:   ...     # 远端：透传 category/title
else:        ...     # 本机：路径派生
```

`is_internal_file` 是本机回退（`client is None`，直接读本机任务目录）时代的脚手架
过滤，用在已由服务端筛过的远端清单上属于重复过滤。

## 修复（仅 `agent/runtime/artifacts_ref.py`）
1. **远端路径不再叠加内部文件过滤**：把 `is_internal_file` 判断**下沉到本机分支**。
   远端分支（`client is not None`，来源为 `get_task_result.artifacts[]`，取不到时退回
   的远端 `list_task_files` 也走这里）原样信任服务端清单，直接 `build_task_artifact`。
2. **保留本机过滤**：本机路径（`client is None`）仍在造引用前用 `is_internal_file`
   剔除 `driver.log` / `logs/**` / `metadata.json` / `published_hashes.json`，行为不变。
3. **保持上一轮修复不变**：远端 `category` / `title` 照旧透传（缺失才按路径派生），
   `task_id` 仍取 `hadoop_task_id`，`id` 仍为 `task:<task_id>:<relpath>`。
4. **未改前端**、未改 `strip_internal` 与其它模块。

## 测试（TDD，全部离线，假客户端，不连网）
`agent/tests/test_artifacts_ref.py`：

- 新增 `TestRemoteCategoryAndTitlePassThrough.test_remote_logs_entry_is_kept_with_hadoop_id`：
  假远端结果 `artifacts[]` 含 `logs/driver.log`、`category="日志"` → 引用**保留**该条，
  类别为 `日志`，`task_id` 为 hadoop 编号，`id` 为 `task:<hadoop>:logs/driver.log`。
  **修复前失败**（返回空表）。
- 改写 `test_internal_files_still_filtered` → `test_remote_logs_are_kept_not_filtered`：
  远端 `driver.log` 与 `logs/publish.log` 均照收、类别透传 `日志`。**修复前失败**。
- 更新 `TestRemoteArtifactCollection.test_remote_task_artifacts_from_remote_result`：
  远端信封里的 `driver.log`（类别 `日志`）现进入结果。**修复前失败**。
- 更新 `TestRemoteArtifactCollection.test_remote_falls_back_to_list_task_files`：
  退回远端 `list_task_files` 时 `driver.log` 也照收。**修复前失败**。
- `TestInternalFileFilter.test_collect_filters_internal_but_keeps_others` 增补
  `logs/driver.log`：本机路径（无客户端）**仍然**过滤 `logs/**`，保持原行为。

修复前 4 例按预期失败（RED），修复后全绿。

## 验证
- 相关用例：`agent/tests/test_artifacts_ref.py` → `37 passed`。
- 全量本地套件（repo 根）：
  `& "...\.scratch\langgraph-probe\.venv\Scripts\python.exe" -m pytest agent/tests -q`
  → `414 passed, 240 subtests passed`（基线 413，本次净 +1），保持全绿。

## 影响面
- 仅 `agent/runtime/artifacts_ref.py` 与配套测试。
- 远端产物列表现在与服务端产品清单一致：`logs/**` 等日志照收并透传类别 `日志`，
  前端可据类别把日志折叠进单独文件区。
- 本机回退（`client=None`）路径派生与内部文件过滤行为未变。

## 遗留 / 关注点
- **恢复接口仍会二次过滤**：`Runtime.visible_artifacts` 与 `_gather_artifact_refs` 在
  读回持久化产物时调用 `strip_internal`，其内部仍用 `is_internal_file`，对**来源无
  感知**。因此远端日志虽在收集 / 回合信封 / 事件中出现，但 `GET …/artifacts` 的恢复
  结果与「下一回合已知列表」仍会把 `logs/**` 当作内部文件剔除（已知列表被剔后，远端
  每回合会重新收集到，故完整列表仍含日志，但会重复发 `artifact_added`）。
  本次按任务要求只改收集路径；`strip_internal` 需按「远端产品类别（如 `日志`）放行」
  另立改动才能端到端一致。**未改前端。**
- `driver.log` 落在任务目录根、且远端将其标为 `日志` 时同样照收；本机路径仍视为
  内部文件（与既有测试一致）。

## 提交
一次性本地提交，仅含 agent 文件（`agent/runtime/artifacts_ref.py`、
`agent/tests/test_artifacts_ref.py`），未推送。
