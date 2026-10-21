# 软数字护栏（Task A）实施报告

日期：2026-10-20
范围：`agent/number_guard.py`、`agent/runtime/graph.py`、`agent/runtime/runtime.py`、相关测试

## 改动

- `number_guard.find_unauthorized`
  - 记录新增 `start` / `end`（原文中的字符下标），保留 `number` / `snippet`。
  - 行首列表标记（`1.` / `2、` / `(3)` / `（4）`）先替换为**等长空格**再扫描，连续序号（如一行 `1. 2. 3.`）反复替换到稳定；原文与下标不偏移。
  - 数字匹配整段吃下千分位（`100,830`）并保留下标；比较仍用规范化 token（`100830`）。
- `graph.py`
  - `RuntimeState` 增 `guard_warning` / `guard_spans`；`blocked` / `final_retries` 仅保留历史字段。
  - `_guard_final`：`steps` 为空直接返回 `None`；有未溯源数字返回
    `{"guard_warning": "以下数字未能在工具结果中找到：" + describe(records), "guard_spans": [...]}`；
    不再重写、不再拦截。
  - `model_node`：放行最终回答，并带上 `guard_warning` / `guard_spans`。
- `runtime.py` `_finish_turn`
  - 成功信封新增 `guard_warning`（无则 `None`）与 `guard_spans`（无则 `[]`）。
  - 有提示时 `llm.note` 为「由大模型编排 N 步工具调用；部分数字未能在工具结果中找到（已在回答中标红）」。
  - 旧 `blocked` 分支保留，但数字护栏不再走到它。

## 测试

- 编造数字：`ok=True`、`guard_warning` 含 `888888`、`guard_spans` 区间切片等于 `888888`。
- 无工具调用：回答含数字也不提示（`guard_warning=None`、`guard_spans=[]`）。
- 列表序号：不产生 span（含一行 `1. 2. 3.`）。
- 千分位：`100,830` 作为一段匹配；工具结果 `100830` 可正常溯源。
- 全量：`538 passed, 353 subtests passed`（无 sqlite-lock 抖动）。

## 前端契约

成功信封新增：

```json
{
  "guard_warning": "以下数字未能在工具结果中找到：888888（出现在「…」）",
  "guard_spans": [
    {"number": "888888", "start": 10, "end": 16}
  ]
}
```

- 无提示：`guard_warning: null`，`guard_spans: []`。
- `start` / `end` 是**原文**（`final` / `reply`）中的字符下标，左闭右开；前端直接切片标红。
- 仅提示、不修改回答原文，不产生 `FABRICATED_NUMBER` 拦截。
