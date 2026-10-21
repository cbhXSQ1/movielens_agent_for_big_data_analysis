# Agent Task 17 — 报告

## 标题
修复两端产物类别不一致：远端 `artifacts[]` 的 `category` / `title` 原样透传

## 背景 / 现象
远端 `get_task_result` 返回的 `artifacts[]` 每条带
`{id, title, category, kind, path, size}`，其中 `category` 使用远端词汇：
`数据 / 隔离 / 报告 / 指标 / 元数据 / 日志 / 其它`
（见 `docs/hadoop/迭代一重构_对端改造说明.md` §4 与
`hadoop/mcpserver/readers.py::_task_artifacts`）。

Agent 侧 `agent/runtime/artifacts_ref.py` 在把远端产物转成产物引用时，丢掉了远端的
`category` / `title`，改用**路径重新派生**类别，得到的是 Agent 自己的词汇：
`报告 / 评分 / 证据 / 文件 / 依据 / 其它`（`CATEGORY_*` 常量与 `category_of`）。

结果是前端按远端词汇做的类别徽标映射
（`frontend/src/components/results/artifactCategory.ts`）大多命中不到，
`数据 / 隔离 / 指标 / 元数据 / 日志` 一律落到默认配色；日志折叠也只能靠路径
（`logs/`、`.log`）兜底，而不是靠类别 `日志`。

## 根因
`collect_task_artifacts` 在远端路径上调用
`build_task_artifact(task_id, rel, kind=entry.get("kind"))`，该函数内部固定用
`friendly_title(rel)` / `category_of(rel)` 生成标题与类别，远端条目里的
`title` / `category` 完全没有被读取。

另外，`Runtime` 在返回/持久化前会调用 `disambiguate_titles`，旧实现同样用
`friendly_title(path)` **重算**标题，会把刚透传的远端标题再次覆盖；若只改
`build_task_artifact`，远端标题在端到端链路上仍然看不到。

## 修复（仅 `agent/runtime/artifacts_ref.py`）
1. **类别 / 标题透传**：`build_task_artifact` 新增可选参数 `title` / `category`；
   传入时原样使用，缺省才回落路径派生（本机回退行为不变）。
2. **远端来源识别**：`collect_task_artifacts` 区分远端与本地两条路径。远端路径
   （`client is not None`，来源为 `get_task_result.artifacts[]`，取不到时的
   `list_task_files` 也走这里）把 `entry.get("title")` / `entry.get("category")`
   传给 `build_task_artifact`；本地 `lister` 分支保持原样。
3. **同名去歧义**：新增 `_disambiguation_base`，优先用条目已有标题、缺省才按路径
   派生；已带 `（<短任务号>）` 后缀的先剥掉再判重，保证多回合幂等、不会越加越长。
   `disambiguate_titles` 改用它，远端标题不再被路径标题覆盖。
4. **保持不变的约束**：
   - `is_internal_file` 过滤照旧（`driver.log`、`logs/**`、`metadata.json`、
     `published_hashes.json` 仍不进用户产物列表）；
   - `task_id` 仍取 `hadoop_task_id`；`id` 仍为 `task:<task_id>:<relpath>`；
   - 未改前端词汇与映射。

## 测试（TDD，全部离线，假客户端，不连网）
`agent/tests/test_artifacts_ref.py` 新增 `TestRemoteCategoryAndTitlePassThrough`，
先写用例并确认失败，再实现。用例用 `FakeRemoteClient` 罐头信封，不发真实请求：

- `test_remote_category_and_title_are_preserved`：远端条目分别给
  `category=指标 / 数据 / 日志` 与自定义 `title`，断言引用逐条保留这些类别与标题
  （尤其 `指标` 不再被改写成 `评分`），且 `task_id` 为传入的任务号。
  **修复前失败**（`指标` 变成 `评分`；`数据` 变成 `文件`；`日志` 变成 `其它`）。
- `test_remote_entry_without_category_falls_back_to_path`：无 `category` 的条目
  仍按路径派生为 `评分`，标题仍按远端透传。
- `test_remote_entry_without_title_falls_back_to_path`：无 `title` 的条目回落到
  路径友好标题。
- `test_internal_files_still_filtered`：`driver.log` 与 `logs/publish.log` 仍被
  内部文件规则剔除。
- `test_remote_title_survives_disambiguation`：经 `disambiguate_titles` 后远端
  标题仍在，类别仍为 `数据`。**修复前失败**（标题被改成 `清洗后 ratings（v1）`）。
- `test_remote_title_disambiguation_is_idempotent`：跨任务同名去歧义后再跑一遍，
  标题不再变化，两个任务的标题仍可区分。

## 验证
- 修复前新增用例按预期失败（RED）；修复后相关用例全绿。
- 全量本地套件：
  `413 passed, 240 subtests passed`（基线 407，本次 +6）。

## 影响面
- 仅 `agent/runtime/artifacts_ref.py` 与配套测试。
- 远端产物引用的 `category` / `title` 现在与远端信封一致；本机回退（无客户端）
  的路径派生词汇与行为未变。
- 前端词汇与映射未改；类别徽标与日志折叠直接受益。

## 遗留 / 关注点
- `disambiguate_titles` 现在优先保留已有标题，因此旧对话里已按路径标题持久化的
  产物不会自动改成远端标题；新收集 / 新一轮收集的产物才会带远端标题。
- 内部文件过滤仍然优先于类别：即使远端把某个 `logs/**` 条目标为 `日志`，它依旧
  被当作内部文件剔除。本次按任务要求保持该过滤不变。
- 本机回退（`client=None`）继续使用 Agent 的路径派生类别（`报告/评分/...`）；
  若将来统一到远端词汇，需要另立改动。

## 提交
一次性本地提交，仅含 agent 文件（`agent/runtime/artifacts_ref.py`、
`agent/tests/test_artifacts_ref.py`），未推送。
