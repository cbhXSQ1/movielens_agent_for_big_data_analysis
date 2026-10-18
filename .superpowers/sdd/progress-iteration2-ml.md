# SDD 进度台账 — 迭代二（机器学习）Agent + 前端

计划：`docs/superpowers/plans/2026-10-18-iteration2-ml-agent-frontend.md`
契约（权威）：`docs/hadoop/迭代二_机器学习_数据契约.md`
基线提交（本计划起点）：cad1871

## 并行波次（按不相交文件划分）
- 波次1：AG-ML-1（工具表）/ AG-ML-6（错误码）/ FE-ML-1（结果三视图·注册表）
- 波次2：AG-ML-2（结果板块）/ AG-ML-4（下载中转）/ FE-ML-2（模型库）
- 波次3：AG-ML-3（长任务）/ AG-ML-5（模型端点）/ FE-ML-3（单位与错误码）
- 波次4：FE-ML-4（离线表单）/ AG-ML-7、FE-ML-5（收口）
- 联调：端侧 ML 就绪后跑真数据

## 记录
- 波次1：AG-ML-1（01633aa）、AG-ML-6（b9f6ddb）、FE-ML-1（e8453b7）完成。
- 波次2：AG-ML-2（abb0201）、AG-ML-4（9bba178）、FE-ML-2（1cfb802 + ab913da）完成。
  - ⚠️ 并发提交曾互相改写历史（AG-ML-4 用了 reset/amend）；已核验：历史线性、文件齐全、后端 476 / 前端 285 全绿。
  - 规则更新：后续子智能体**禁止** reset/amend/rebase/reflog/push；提交**只限自己路径**（`git commit -- <paths>`）；未见外来暂存文件。
- 波次3：AG-ML-3（0b1dd03）、AG-ML-5（0a8c985）、FE-ML-3（d3f073b）完成。
- 波次4：FE-ML-4（85d1102）、AG-ML-7（7da5b67）、FE-ML-5（492c041）完成。
- 全量验证（并发停止后）：后端 **493 passed**；前端 **298 passed** + 构建通过。
- 待办：README 更新、终检全分支复核、推送；端侧 6 项 ML 就绪后真数据联调。


