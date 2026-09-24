# MovieLens 1M 数据分析 Agent 系统

大数据分析课程项目（三次迭代）。用户通过前端输入自然语言，Agent 调用 Hadoop、机器学习、图分析等工具完成数据处理与分析，返回真实执行状态、结果与依据。

## 仓库结构

| 目录 | 负责 | 内容 |
|---|---|---|
| `hadoop/` | Hadoop 组 | 迭代一：Hadoop 数据清洗 + 五维质量评分（Streaming + Python） |
| `agent/` | Agent 组 | 自然语言任务组织、工具调用、结果解释 |
| `frontend/` | 前端组 | 问题输入、执行状态、五维对比、报告展示 |
| `config/` | 共用 | 清洗方案 / 评分方案 v1（只读，版本化） |
| `docs/` | 各组 | `hadoop/`、`agent/`、`frontend/` 三组文档 |
| `reference/` | Hadoop 组 | 本地原型参考实现与实测输出（语义依据） |

## 迭代一（Hadoop 侧）

- 实施计划与技术方案：`docs/hadoop/plan.md`
- Agent 接口规范（CLI 契约）：`docs/hadoop/agent-interface.md`
- 配置：`config/cleaning_rules.v1.json`、`config/scoring_scheme.v1.json`（词条说明见同目录 `*.说明.md`）

## 快速校验

```bash
python3 hadoop/tools/validate_configs.py
```

预期输出 `RESULT: PASS`（退出码 0）。
