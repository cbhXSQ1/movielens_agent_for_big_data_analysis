# FE-ML-4 报告：工具表 / 离线表单适配 ML 参数

## 状态
完成。`npm run test` 与 `npm run build` 全绿。

## 交付
- **修改** `frontend/src/components/chat/OfflineToolForm.vue`
  - `controlKind` 新增 `object`：`params` 这类对象参数渲染为键值行编辑器
    （`data-testid="offline-param-<name>-editor"`，含「添加一行 / 删除」按钮与每行 key/value 输入）。
  - 键值行值按字面量还原类型：`true`/`false` → 布尔、整数/小数 → 数字，其余按字符串；
    空键跳过；**未知键原样提交、不硬拦**（软提示：`param__hint` 保留工具表 description 里的可用键清单，
    并加一句「未知键会被端侧拒绝」）。
  - 选中工具时对对象参数预置空对象（契约默认 `{}`），保证提交载荷形状稳定；支持从对象默认值摊成行。
  - `algorithm` 枚举、`k` / `n_components` 整数沿用既有渲染：
    `n-input-number` 带 `min`/`max`，且 `validate()` 在提交前再校验整数与上下界（越界给中文错误）。
- **新建** `frontend/src/__tests__/ml.offline.form.test.ts`（6 用例，全程挂载真实表单 + 假工具表）
  - `train_classifier` 渲染 `algorithm` 枚举控件（含两个选项）与 `params` 键值编辑器 + 可用键软提示；
  - 填写 `algorithm` 与 `params.n_estimators=200` 后提交，载荷为 `{algorithm:'naive_bayes', params:{n_estimators:200}}`；
  - `params` 多行 + 未知键原样提交（`{threshold:0.6, unknown_key:'keep'}`），无错误；
  - `cluster_users` 的 `k` 越界（1、51）被拦、合法值（8）通过；`reduce_dimensions` 的 `n_components` 越界（21）被拦；
  - 既有 `clean_data`（字符串/整数/布尔/枚举）渲染与提交回归不变。

## 测试摘要
- 新增用例：`6 passed`。
- 全量：`39 test files / 298 passed`（含 `chat.test.ts` 既有离线表单用例，无回归）。
- `npm run build`（`vue-tsc --noEmit && vite build`）通过。

## 关注点 / 备注
- 键值编辑器按字面量推断值类型（数字/布尔/字符串）；端侧仍按契约白名单与范围校验，非法时回 `USAGE`。
- 按任务说明，「预填已知键」为可选项，未做：改以 description 软提示 + 空行起步，避免误塞默认值。
- `params` 为空时提交 `{}`（契约默认），与端侧白名单校验不冲突；本改动对无对象参数的既有工具无影响。

## 修复（阻断项）：未填可选参数不得以 null 提交

- **根因**：端侧推送的工具表（`agent/runtime/stream.py:_param_entry`）会给未声明默认值的
  可选参数补 `default: null`；`tools store.defaultsFor` 把 `null` 当真实默认值预填，
  `OfflineToolForm.submit` 又只按 `!== undefined` 过滤，于是把 `params` / `data_version` /
  `tag` 等以 `null` 提交（`params` 契约类型为 object，端侧可能拒收）。
- **修改** `frontend/src/components/chat/OfflineToolForm.vue`
  - `selectTool`：`null`/`undefined` 默认值视同「未设置」，不预填（对象参数改由键值行起步，
    不再强制预置空对象）；有非空默认值的参数照常预填。
  - `submit`：省略值为 `null`/`undefined` 的参数，并省略对象类参数的空对象
    （未编辑的 `params` → 不提交，交给端侧用契约默认 `{}`）；必填/有效值照常提交。
- **新增回归** `frontend/src/__tests__/ml.offline.form.test.ts`
  - 用例「仅填必填项时，null 默认的可选参数在载荷中整体消失（无 null、无空对象）」：
    spec 的可选参数 `data_version` / `tag` / `params`（object）均 `default: null`，
    仅填 `algorithm` 后提交，载荷恰为 `{ algorithm: 'naive_bayes' }`，
    且 `getValues()` 不含这些键。

### 验证
- `npm run test`：**39 文件 / 299 passed**（覆盖用例：`ml.offline.form.test.ts` →「仅填必填项时，null 默认的可选参数在载荷中整体消失（无 null、无空对象）」）。
- `npm run build`（`vue-tsc --noEmit && vite build`）通过。
