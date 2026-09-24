# scoring_scheme.v1.json 词条说明

> 配套文件：`scoring_scheme.v1.json`（版本 1.0.0，状态：registered_default）
> 用途：Hadoop 评分引擎的输入配置；清洗前后使用**同一份配置**计算；前端"评分依据"页由本配置自动渲染。
> 边界：五个维度名称与含义为课程固定（不可增删改）；可自定义的是指标、公式、权重、分值范围、是否综合分。

---

## 1. 顶层字段

| 字段 | 类型 | 含义 |
|---|---|---|
| `config_type` | string | 固定为 `"scoring_scheme"` |
| `scheme_id` | string | 评分方案唯一标识（如 `ml1m-quality-default`） |
| `version` | string | 方案版本号；任何公式/权重改动都必须升版本 |
| `status` | string | `registered_default` / `custom` |
| `created` | string | 创建日期 |
| `description` | string | 方案说明 |
| `engine_compat` | object | 兼容的算子库/评分引擎版本 |
| `customization` | object | 自定义能力开关，见第 2 节 |
| `score_scale` | object | 分值范围：`min`/`max`/`rounding`（默认 0–100，保留 2 位） |
| `comparability` | object | 可比性声明：前后同公式、跨版本不可比、隔离影响披露要求 |
| `field_inventory` | object | 三表字段清单（公式中 `field` 只能引用这里的字段） |
| `reference_domains` | object | 与清洗方案一致的官方口径参照值域（两处必须保持一致） |
| `dimensions` | array | 五个维度的定义与指标，见第 3、4 节 |
| `composite` | object | 综合分：是否启用、维度权重、权重理由 |
| `reporting` | object | 报告与前端展示要求（评分依据、数据量变化、隔离影响、版本标注） |

## 2. customization（自定义接口）

| 字段 | 含义 |
|---|---|
| `allow_metric_toggle` | 允许启用/停用已登记指标（L1） |
| `allow_weight_edit` | 允许修改指标权重（L2） |
| `allow_composite_edit` | 允许自定义综合分开关与维度权重（L2；须归一为 1，修改产生新版本） |
| `allow_metric_compose` | 允许用聚合+谓词组合新指标公式（L3，推荐上限） |
| `allow_new_custom_metric` | 允许新增自定义指标（须登记） |
| `allow_free_code` | 是否允许自由代码（**固定 false**） |
| `registration_required` | 自定义内容必须登记为新版本文件 |
| `default_immutable` | 默认方案不可覆盖，只能新增版本 |
| `dimensions_immutable` | 五个维度不可增删改（**固定 true**） |

## 3. 维度对象字段（dimensions 数组的元素）

| 字段 | 含义 |
|---|---|
| `id` | 维度英文名（Accurate/Complete/Unique/Up-to-date/Consistent，固定） |
| `name_zh` | 维度中文名 |
| `definition` | 课程给定的维度含义原文（固定，不可改写） |
| `weight` | 维度权重（用于综合分；所有维度权重之和为 1） |
| `metrics` | 该维度下的指标数组 |

## 4. 指标对象字段（metrics 数组的元素）

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | string | 指标编号（A1–A6 / C1–C3 / U1–U3 / F1–F2 / S1–S4；自定义用 `CUSTOM-xx`） |
| `name` | string | 指标名称 |
| `enabled` | bool | 是否启用（L1 开关） |
| `measure` | string | 度量类型：`ratio`（比率）或 `freshness`（新鲜度），见第 5 节 |
| `numerator` | object | 分子：一个聚合（agg）定义，见第 6 节 |
| `denominator` | object | 分母：一个聚合定义 |
| `params` | object | 特殊参数（`measure=freshness` 时使用） |
| `weight` | number | 指标在本维度内的权重（同维度内之和为 1） |
| `description` | string | 指标定义（前端"评分依据"直接展示） |
| `limitations` | string | 适用范围与局限（必填） |
| `unverifiable` | array | 无法验证项清单（必填，可为空数组） |

## 5. measure 类型

| measure | 公式 | 说明 |
|---|---|---|
| `ratio` | 分子聚合值 ÷ 分母聚合值 × 100 | 所有计数/比率类指标 |
| `freshness` | `gap = 参照时间 − max(有效时间)`；`gap ≤ full_score_days` → 100 分；`full < gap < zero` 线性衰减；`gap ≥ zero_score_days` → 0 分 | 覆盖新鲜度（F2），参数见指标 `params` |

`params` 字段（freshness）：

| 字段 | 含义 |
|---|---|
| `table` / `field` | 取最大时间的表与字段（F2 为 ratings.Timestamp） |
| `reference` | 参照时间，指向 `reference_domains.timestamp.max`（数据集声明截止 2003-02-28） |
| `full_score_days` | 满分窗口（≤30 天得满分） |
| `zero_score_days` | 归零窗口（180 天线性到 0） |

## 6. 聚合 agg 目录（v1 封闭白名单）

聚合是"对一批记录做统计"的原子操作；`where` 是谓词表达式（复用清洗方案第 7 节的算子库）。

| agg | 参数 | 语义 |
|---|---|---|
| `count` | `on`（`raw_lines` 原始行 / `parsed_records` 可解析记录）、`table` 或 `tables`/`scope: all`、`where` | 计数 |
| `distinct_key_count` | `table` 或 `tables`、`key[]` | 按键去重后的键数 |
| `distinct_row_count` | `scope` | 整行去重后的行数 |
| `nonempty_field_count` | `scope` 或 `table`、`fields[]` 或 `all` | 非空字段数 |
| `field_count` | `scope` 或 `table`、`fields[]` 或 `all` | 字段总数（分母用） |
| `valid_field_count` | `table`、`fields: [{field, condition}]` | 满足各自条件的字段数 |
| `token_count` | `table`、`field`、`sep`、`where` | 多值字段的 token 数（如类型） |
| `record_complete_count` | `scope` | 字段数正确且所有字段非空的记录数 |
| `dup_group_count` | `tables: [{table, key[], value}]`、`conflict_free`（可选 true） | 重复组数；`conflict_free=true` 时只计"组内 value 无矛盾"的组数 |

常用谓词算子（评分公式中出现的）：`is_int`、`in_range`、`in_set`、`year_valid`、`token_in_set`、`ref_exists`、`timestamp_unit_ms`、`contains_any`、`not`、`and`、`or` —— 完整算子目录见 `cleaning_rules.v1.说明.md` 第 7 节（同一算子库）。

## 7. 权重与综合分

- **指标权重**：每个维度内所有启用指标的权重之和必须为 1（校验器检查）
- **维度权重**：见 `composite.weights`（Accurate .25 / Complete .25 / Unique .20 / Up-to-date .10 / Consistent .20）
- **综合分**：五维加权求和；`composite.enabled=false` 时不输出综合分（由使用者选择）
- **综合分权重可自定义**（`composite.customizable=true`，开关 `allow_composite_edit`）：用户可调整维度权重或关闭综合分；修改须归一为 1、登记新版本、在任务元数据与报告中披露；跨版本的维度分与综合分不可比，禁止用调权重的方式美化分数
- 默认权重理由：准确性、完整性对下游模型最关键；时效性因数据固有历史性权重最低

## 8. 如何新增自定义指标（保留接口的用法）

示例：在 Complete 维度新增"邮编非空率"：

```json
{
  "id": "CUSTOM-01",
  "name": "邮编非空率",
  "enabled": true,
  "measure": "ratio",
  "numerator":   { "agg": "nonempty_field_count", "table": "users", "fields": ["Zip-code"] },
  "denominator": { "agg": "field_count", "table": "users", "fields": ["Zip-code"] },
  "weight": 0.10,
  "description": "Zip-code 字段非空的用户记录占比。",
  "limitations": "非空不代表格式正确（格式由 U3 清洗规则与 A3 口径覆盖）。",
  "unverifiable": []
}
```

步骤：
1. 从 agg 目录（第 6 节）选聚合、从算子库选谓词，组合分子/分母
2. 补齐 `description`、`limitations`、`unverifiable`（必填，缺失拒绝加载）
3. **调整同维度权重**使其总和为 1；登记为新版本文件（`status=custom`，`version` 递增）
4. 任务元数据记录 `scoring_scheme_version`；报告标注"本次使用自定义评分方案，与默认方案分数不可比"

## 9. 加载校验清单

- [ ] 五个维度齐全、`definition` 与课程一致（不可改写）
- [ ] 每个维度至少 1 个启用指标；维度内权重之和为 1；维度权重之和为 1
- [ ] `measure`、`agg`、谓词算子均在白名单内；`field` 在 `field_inventory` 内；`set_ref` 存在
- [ ] 每个指标都有 `description`、`limitations`、`unverifiable`
- [ ] `reference_domains` 与清洗方案中的同名值域一致
- [ ] 默认方案不可被同版本覆盖；校验失败 → 拒绝执行

## 10. 可比性与披露要求（防"分数虚高"）

1. 清洗前后必须使用同一份方案文件（同一 `version`）计算
2. 隔离/去重会让比率类指标机械上升（分母变化）：报告必须同时给出**数据量变化**与**隔离影响**，禁止把隔离表述为"问题已修复"
3. 不同 `(rule_version, scoring_scheme_version)` 的分数不可比、不可混用；迭代二、三必须钉住一个版本
4. Agent 不得为提高分数擅自调整指标或权重；用户自定义必须显式、登记、披露

## 11. 附录：默认方案实测分数（本地原型，供核对）

### 逐指标（%）

| 指标 | 清洗前 | 清洗后 | 变化 |
|---|---|---|---|
| A1 评分值域合法率 | 96.14 | 100.00 | +3.86 |
| A2 时间戳可解析率 | 99.70 | 100.00 | +0.30 |
| A3 用户属性编码合法率 | 98.20 | 98.60 | +0.40 |
| A4 跨表引用有效率 | 97.56 | 100.00 | +2.44 |
| A5 电影年份可解析率 | 97.36 | 100.00 | +2.64 |
| A6 类型合法率 | 97.63 | 100.00 | +2.37 |
| C1 字段非空率 | 99.70 | 99.99 | +0.29 |
| C2 记录完整率 | 97.65 | 99.99 | +2.34 |
| C3 解析成功率 | 98.82 | 100.00 | +1.18 |
| U1 评分键唯一率 | 90.81 | 100.00 | +9.19 |
| U2 完全重复行率 | 92.50 | 100.00 | +7.50 |
| U3 维表 ID 唯一率 | 89.50 | 100.00 | +10.50 |
| F1 时间范围合规率 | 97.46 | 100.00 | +2.54 |
| F2 覆盖新鲜度 | 100.00 | 100.00 | 0.00 |
| S1 格式一致率 | 98.82 | 100.00 | +1.18 |
| S2 编码一致率 | 98.91 | 100.00 | +1.09 |
| S3 时间单位一致率 | 98.81 | 100.00 | +1.19 |
| S4 同键矛盾率 | 68.20 | 100.00 | +31.80 |

### 维度分与综合分

| 维度 | 清洗前 | 清洗后 | 变化 |
|---|---|---|---|
| Accurate | 97.71 | 99.79 | +2.08 |
| Complete | 98.82 | 99.99 | +1.17 |
| Unique | 90.90 | 100.00 | +9.10 |
| Up-to-date | 98.22 | 100.00 | +1.78 |
| Consistent | 89.65 | 100.00 | +10.35 |
| **综合分** | **95.07** | **99.95** | **+4.88** |

> 数据量变化（报告必须同屏展示）：评分 1,136,738 → 1,000,209（隔离 87,019 + 去重 49,510 + 修复 13,503）；用户 6,838 → 6,040；电影 4,395 → 3,883。
> 说明：U3/S4 的大幅提升主要来自"隔离出分母"，A3/C1 不封顶是诚实口径（置空字段不算合法/非空），F2 恒定说明时效性不由清洗改善。
> 以上为本地原型实测值；Hadoop 正式运行的权威数字以任务输出为准。
