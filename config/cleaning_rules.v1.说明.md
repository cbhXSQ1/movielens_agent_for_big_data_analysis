# cleaning_rules.v1.json 词条说明

> 配套文件：`cleaning_rules.v1.json`（版本 1.0.0，状态：registered_default）
> 用途：Hadoop 清洗引擎的输入配置；Agent 通过读取本配置向用户解释清洗依据；前端"清洗结果"页从隔离区与报告渲染。
> 原则：**检测（detect）与处置（action/policy）全部是声明式配置**；自定义 = 在封闭算子库上组合表达式并登记新版本。

---

## 1. 顶层字段

| 字段 | 类型 | 含义 |
|---|---|---|
| `config_type` | string | 固定为 `"cleaning_rules"`，用于引擎识别配置类型 |
| `scheme_id` | string | 方案唯一标识（如 `ml1m-cleaning-default`） |
| `version` | string | 方案版本号（语义化，如 `1.0.0`）；任何内容改动都必须升版本 |
| `status` | string | `registered_default`（已登记默认方案）/ `custom`（自定义方案） |
| `created` | string | 创建日期（ISO 格式） |
| `description` | string | 方案的自然语言说明 |
| `engine_compat` | object | 兼容性声明：`operator_library`（算子库版本）、`action_engine`（动作引擎版本） |
| `customization` | object | 自定义能力开关，见第 2 节 |
| `data_version` | object | 本方案产出的数据版本、时间边界 T1/T2、预期规模，见第 3 节 |
| `reference_domains` | object | 官方口径参照值域（ID 范围、评分范围、编码集合、时间范围等），见第 4 节 |
| `processing_conventions` | array | 处理约定（不是规则，是引擎必须遵守的读取/处理方式），见第 5 节 |
| `pipeline` | array | 执行流水线：阶段（stage）与各阶段规则执行顺序 |
| `rules` | array | 规则数组，本方案的核心，见第 6 节 |
| `outputs` | object | 输出路径模板与元数据字段清单 |
| `quarantine_record` | object | 隔离记录的标准字段（保证可审计、可回溯） |
| `reports` | array | 本方案必须产出的报告项（数据量变化、命中汇总、级联影响、样例、版本元数据） |

## 2. customization（自定义接口）

| 字段 | 含义 |
|---|---|
| `allow_rule_toggle` | 允许启用/停用已登记规则（L1） |
| `allow_parameter_edit` | 允许修改规则参数（阈值、边界、集合引用）（L2） |
| `allow_policy_edit` | 允许在已登记策略选项内选择处置口径（含冲突口径，如 `user_conflict_resolution`、`movie_conflict_resolution`）（L2） |
| `allow_expression_compose` | 允许用算子组合新的检测表达式（L3，推荐上限） |
| `allow_new_custom_rule` | 允许新增自定义规则（须登记 `origin.custom=true`） |
| `allow_free_code` | 是否允许自由代码（**固定 false**：不可复现、无法版本化） |
| `registration_required` | 自定义内容必须登记为带版本号的新方案文件 |
| `default_immutable` | 默认方案不可被覆盖，只能新增版本 |

## 3. data_version（数据版本与时间边界）

| 字段 | 含义 |
|---|---|
| `id` | 清洗后数据版本号（如 `ml1m-clean-v1`），任务元数据必须记录 |
| `source` | 原始数据来源标识 |
| `time_boundaries.T1` | 训练期截止时间：`2001-12-31T23:59:59Z` |
| `time_boundaries.T2` | 验证期截止时间：`2002-06-30T23:59:59Z` |
| `time_boundaries.timezone` | 时区：UTC |
| `time_boundaries.definition.train` | 训练期 = `Timestamp <= T1` 的评分（含 T1 时刻） |
| `time_boundaries.definition.validation` | 验证期 = `T1 < Timestamp <= T2` 的评分（含 T2 时刻） |
| `time_boundaries.definition.test` | 测试期 = `Timestamp > T2` 的评分 |
| `time_boundaries.usage_constraints` | 使用约束：训练只用 train 区间；不得用验证/测试期信息构造训练输入；不得混用不同版本/时间范围产物 |
| `time_boundaries.status` | `registered`（候选 A 已确认） |
| `time_boundaries.decision_basis` | 选择依据：train 972,815 条（97.26%）/ validation 14,551 / test 12,843；train 内评分≥20 的用户 6,019/6,040；备选 B（T1=2001-06-30）未采用 |
| `expected_cleaned_size` | 本地原型实测的清洗后预期规模（ratings 1,000,209 / users 6,040 / movies 3,883），供 Hadoop 运行后核对 |

## 4. reference_domains（官方口径参照值域）

所有检测与评分共享的"合法值集合"。`detect` 中的 `set_ref` 只能引用这里定义的集合名。

| 条目 | 含义 |
|---|---|
| `user_id` / `movie_id` | 文档声明的 ID 范围（1–6040 / 1–3952） |
| `rating` | 评分范围 1–5（整数） |
| `gender` / `age` / `occupation` | 官方编码集合（M/F；7 档年龄；0–20 职业） |
| `zip` | 邮编正则（5 位数字；注意按字符串处理，禁止数值化） |
| `genres` | 官方 18 类电影类型 |
| `timestamp` | Unix 秒（UTC）及数据集声明覆盖范围（附 ISO 时间） |
| `title_year` | 标题年份正则与范围 1900–2003 |

## 5. processing_conventions（处理约定）

| 条目 | 含义 |
|---|---|
| `C-ENC` | 按 ISO-8859-1 读取；双重编码只由 P1 修复 |
| `C-HDR` | 文件无表头，不跳首行 |
| `C-ZIP` | 邮编按字符串处理（保留前导零，本数据 729 条） |
| `C-TS` | 时间戳为 Unix 秒（UTC）；毫秒只由 R4 修复 |
| `C-SEP` | 字段分隔符固定 `::` |

## 6. 规则对象字段（rules 数组的元素）

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | string | 规则编号（P/R/M/U/X 前缀 + 数字）；自定义规则用 `CUSTOM-xx` |
| `name` | string | 规则名称 |
| `stage` | string | 所属阶段（与 pipeline 对应） |
| `table` | string | 作用表：`ratings` / `users` / `movies` / `all` |
| `enabled` | bool | 是否启用（L1 开关） |
| `severity` | string | `error`（数据错误）/ `warning`（可疑，需人工判断）/ `info` |
| `detect` | object | 检测表达式（AST），见第 7 节 |
| `action` | string | 处置动作，见第 8 节 |
| `fix` | object | 修复策略列表（action=fix / dedupe_resolve 时） |
| `dedupe` | object | 去重键与保留策略（action=dedupe 时） |
| `mark` | object | 标记字段名（action=mark 时，写入问题标记而不改数据） |
| `policy` | object | 处置策略引用：`ref`（策略名）、`value`（当前取值）、`options`（可选项），见第 9 节 |
| `description` | string | 规则说明与处置理由（前端"评分依据/清洗依据"直接展示） |
| `evidence` | object | 本地原型实测参考（自由结构；最终数字以 Hadoop 任务输出为准） |
| `origin` | object | 来源：`author`、`custom`（是否自定义）、`created` |

## 7. detect 表达式与算子目录（v1 封闭白名单）

表达式是 JSON AST，不是代码字符串；节点形式：`{"op": "算子名", ...参数}`。可用逻辑算子嵌套：`and` / `or` / `not`（参数 `args` 为子表达式数组）。

| 算子 | 参数 | 语义 |
|---|---|---|
| `and` / `or` / `not` | `args` | 逻辑组合 |
| `is_null` / `not_null` | `field` | 字段为空 / 非空 |
| `is_int` / `not_int` | `field` | 可解析为整数 / 不可解析 |
| `in_range` / `out_of_range` | `field, min?, max?` | 落在 / 超出 [min,max]（边界可省略） |
| `in_set` / `not_in_set` | `field, set_ref` 或 `values` | 属于 / 不属于集合 |
| `regex_match` / `regex_mismatch` | `field, pattern` | 正则全匹配 / 不匹配 |
| `contains_any` | `field, values[]` | 字段包含任一子串（如乱码特征 `Ã`、`&#`） |
| `has_leading_trailing_space` | `field` | 存在首尾空白 |
| `year_valid` / `year_invalid` | `field, pattern, min, max` | 标题年份可解析且合法 / 缺失或异常 |
| `timestamp_unit_ms` | `field, threshold` | 时间戳 ≥ threshold（判定为毫秒） |
| `field_count_ne` / `field_count_eq` | `sep, expected` | 按分隔符切分后字段数不等于/等于 expected（`table=all` 时 expected 为按表映射） |
| `foreign_delimiter` | `valid_sep, delimiters[]` | 出现非法分隔符且不具备合法结构 |
| `token_in_set` / `token_not_in_set` | `field, sep, set_ref` | 多值字段的 token 全部属于 / 存在不属于集合 |
| `token_empty_or_duplicate` | `field, sep` | token 存在空项或重复项 |
| `duplicate_by` | `keys[]` | 按键分组后存在多条（重复） |
| `conflict_by` | `key[], value` | 同键下 value 存在多个不同取值（冲突） |
| `value_collision` | `value, key, min_keys` | 同一 value 对应 ≥min_keys 个不同 key（如同名不同 ID） |
| `ref_missing` / `ref_exists` | `field, dimension, key_field` | 引用在维表中不存在 / 存在 |
| `ref_unused` | `dimension, key_field` | 维表记录从未被引用 |
| `group_anomaly` | `group_by, where, min_matches` | 按组聚合，满足 where 的条数 ≥ min_matches（行为异常） |

## 8. action 动作与子字段

| action | 含义 | 子字段 | 子字段取值（v1 策略库） |
|---|---|---|---|
| `quarantine` | 移出结果集，进隔离区（保留原始行+原因） | — | — |
| `dedupe` | 删除完全重复的多余副本 | `dedupe` | `keys`、`keep: first/latest` |
| `dedupe_resolve` | 去重 + 冲突解决（维表用） | `fix` | `prefer_valid_record`、`keep_first`、`field_level_merge` |
| `fix` | 原地修复（可判定、可逆） | `fix` | `strip`、`decode_html_entity`、`decode_double_encoding`、`timestamp_ms_to_s`、`zip_plus4_truncate`、`blank_invalid_field` |
| `mark` | 保留数据 + 写入问题标记 | `mark` | `flag`（标记名） |
| `check` | 只检查报告，不改数据 | — | — |
| `report` | 汇总统计进报告 | — | — |

## 9. policy 策略注册表（可自定义的处置口径）

`policy.value` 只能取 `options` 中的值；换值 = 新方案版本，任务元数据记录 `policy_version`。

| ref | options | 默认 | 效果 |
|---|---|---|---|
| `structure_error` | `quarantine_line` | quarantine_line | 结构异常整行隔离 |
| `text_repair` | `fix_in_place` / `quarantine_record` | fix_in_place | 文本乱码修复或整条隔离 |
| `invalid_key` | `quarantine_row` | quarantine_row | 键非法整行隔离 |
| `invalid_rating` | `quarantine_row` | quarantine_row | 非法评分整行隔离（不猜测） |
| `invalid_timestamp` | `quarantine_row` | quarantine_row | 非法时间戳整行隔离 |
| `timestamp_unit` | `fix` / `quarantine_row` | fix | 毫秒修复或隔离 |
| `dedupe_keep` | `first` / `latest` | first | 去重保留哪一条 |
| `conflict_resolution` | `quarantine_all` / `keep_latest` | quarantine_all | 评分同键冲突：整组隔离或保留最新 |
| `multi_rating_event` | `mark_only` / `quarantine_older` | mark_only | 多时间戳：只标记或隔离较早记录 |
| `dimension_record_issue` | `quarantine_record` / `mark_record` | quarantine_record | 维表坏记录：隔离或标记 |
| `movie_conflict_resolution` | `prefer_valid` / `quarantine_all` / `keep_first` | prefer_valid | 电影冲突：保留合法字段最多者 |
| `user_conflict_resolution` | `prefer_valid_then_field_merge` / `quarantine_all` / `keep_first` | prefer_valid_then_field_merge | 用户冲突：优先保留合法者，并列时字段级合并 |
| `invalid_attribute` | `blank_mark` / `quarantine_record` | blank_mark | 非法属性置空+标记或整条隔离 |
| `zip_repair` | `fix_then_blank` / `blank_only` / `quarantine_record` | fix_then_blank | ZIP+4 截取修复，其余置空 |
| `bad_year` / `unknown_genre` | `mark_only` / `quarantine_record` | mark_only | 可疑信息标记不删 |
| `same_title` | `mark_only` | mark_only | 同名电影不自动合并 |
| `behavior_anomaly` | `mark_only` / `quarantine_user_rows` | mark_only | 行为异常只标记（不整人删除） |
| `orphan_ref` | `quarantine_row` / `mark_row` | quarantine_row | 孤儿引用隔离或标记 |
| `unused_object` | `keep_and_report` | keep_and_report | 无评分对象保留并报告 |

> **冲突口径说明**：本方案默认值即团队推荐值（如 `user_conflict_resolution = prefer_valid_then_field_merge`、`movie_conflict_resolution = prefer_valid`、`conflict_resolution = quarantine_all`）。
> 用户可在 `options` 内切换（`allow_policy_edit=true`），但必须：登记为新版本方案、任务元数据记录 `policy_version`、报告中披露；跨版本结果不可比。
> 不推荐的口径（如用户冲突整组隔离）会级联移除大量有效评分，切换前应评估影响。

## 10. pipeline 执行流水线

| 阶段 | 规则（按序执行） | 说明 |
|---|---|---|
| `parse` | P1 → P2 → P3 | 读取、编码修复、结构校验 |
| `validate_repair` | R1 → R2 → R3 → R4 → R5；M1 → M2；U1 → U2 → U3 | 校验与修复（R4 在 R5 之前：先修单位再查范围） |
| `dedupe_resolve` | R6；M4；U5 | 去重与冲突解决 |
| `residual_checks` | R7 → R8；M3 → M6 → M7 → M9 | 兜底检查（本数据多数有效命中为 0） |
| `cross_table` | X1 → X2 | 跨表引用校验（依赖清洗后的维表） |
| `report` | R9；M8；X3 | 标记与报告类规则 |

## 11. 如何新增自定义规则（保留接口的用法）

1. **选算子**：只能使用第 7 节算子目录中的算子（禁止自由代码/LLM 现编逻辑）
2. **写表达式**：如检测占位邮编：
   ```json
   {
     "id": "CUSTOM-01",
     "name": "占位邮编检测",
     "stage": "residual_checks",
     "table": "users",
     "enabled": true,
     "severity": "warning",
     "detect": { "op": "regex_match", "field": "Zip-code", "pattern": "^0{5}$" },
     "action": "mark",
     "mark": { "flag": "placeholder_zip" },
     "policy": { "ref": "same_title", "value": "mark_only", "options": ["mark_only"] },
     "description": "全零邮编疑似占位值，标记待人工判断。",
     "evidence": {},
     "origin": { "author": "user", "custom": true, "created": "2026-09-23" }
   }
   ```
3. **组合表达式示例**（L3）：`{"op":"or","args":[{"op":"not_int","field":"Rating"},{"op":"out_of_range","field":"Rating","min":1,"max":5}]}`
4. **登记版本**：另存为 `cleaning_rules.v1.1.json` 或 `custom_rules.<name>.v1.json`，`status=custom`，`version` 递增；默认方案文件不动
5. **执行与披露**：任务元数据记录所用方案文件与版本；报告必须标注"本次使用自定义规则集，结果与默认方案不可比"

## 12. 加载校验清单（引擎启动时逐项检查）

- [ ] `config_type`、`version`、`status` 合法；默认方案不可被同版本覆盖
- [ ] `detect` 中所有算子存在于算子库；参数类型/数量正确
- [ ] `field` 在表字段清单内；`set_ref` 指向 `reference_domains` 中存在的集合
- [ ] `action` 在允许集合内；`fix` 策略、`dedupe.keep`、`policy.value` 均在注册范围内
- [ ] `pipeline` 引用的规则 id 全部存在，且所有 enabled 规则都被 pipeline 覆盖
- [ ] T1 < T2 且落在 `timestamp` 声明范围内
- [ ] 校验失败 → 拒绝执行（不得带着坏规则跑数据）

## 13. 版本与可比性规则

- 任何改动（开关、参数、表达式、策略值）都必须产生新版本文件并记录
- 换规则/策略 → 产出新的 `data_version`；不同版本的清洗产物**不得混用**
- 迭代二、三必须钉住一个 `(data_version, rule_version)` 组合
- Agent 不得擅自修改规则或策略；用户自定义必须显式、登记、在报告中披露

## 14. 默认规则速查（含原型实测命中）

| id | 名称 | 动作 | 实测命中（参考） |
|---|---|---|---|
| P1 | 文本编码修复 | fix | 48 |
| P2 | 异常分隔符行 | quarantine | 6,075 |
| P3 | 字段数异常行 | quarantine | 7,606 |
| R1 | 评分键格式校验 | quarantine | 6,752 |
| R2 | 评分值域校验 | quarantine | 43,885 |
| R3 | 时间戳格式校验 | quarantine | 3,375 |
| R4 | 时间戳单位修复 | fix | 13,503 |
| R5 | 时间戳范围校验 | quarantine | 12,003 |
| R6 | 评分重复去重 | dedupe | 有效去重 49,510 |
| R7 | 同键评分冲突（兜底） | quarantine | 原始 4,200 组；有效 0 |
| R8 | 多时间戳检查 | mark | 原始 4,323 对；有效 0 |
| R9 | 行为异常标记 | mark | 30 个用户 |
| M1 | 电影 ID 范围校验 | quarantine | 58 |
| M2 | 标题空白修复 | fix | 47 |
| M3 | 空标题检查（兜底） | quarantine | 原始 58；有效 0 |
| M4 | 电影重复与冲突处理 | dedupe_resolve | 70 重复行 + 384 冲突组（隔离 337） |
| M6 | 年份异常检查（兜底） | mark | 原始 58；有效 0 |
| M7 | 类型合法性检查（兜底） | mark | 原始 174；有效 0 |
| M8 | 同名电影标记 | mark | 218 组 |
| M9 | 类型结构检查 | check | 0 |
| U1 | 用户 ID 范围校验 | quarantine | 72 |
| U2 | 用户属性编码校验 | fix + mark | 123 条记录 / 369 处字段 |
| U3 | 邮编格式修复 | fix + mark | 202（修复 73 / 置空 129） |
| U5 | 用户重复与冲突处理 | dedupe_resolve | 118 重复行 + 608 冲突 ID（479 保留合法 / 129 字段级合并） |
| X1 | 评分用户引用校验 | quarantine | 原始 13,878；有效 10,502 |
| X2 | 评分电影引用校验 | quarantine | 原始 13,878；有效 10,502 |
| X3 | 无评分对象报告 | report | 电影 177 部 / 用户 0 |

> 注：M5（标题乱码修复）已并入 P1；U4（邮编按字符串处理）为处理约定 `C-ZIP`。
> `evidence` 中的数字为本地原型在课程提供版数据上的实测值；Hadoop 正式运行的权威数字以任务输出为准。
