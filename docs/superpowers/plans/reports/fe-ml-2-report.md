# FE-ML-2 报告 · 资源页「模型」段（列表 / 详情 / 下载）

## 状态

已完成。`npm run test` 与 `npm run build` 均通过；本地提交，未推送。

## 范围

实施计划 Task **FE-ML-2**（资源页模型区）；字段约定以
`docs/hadoop/迭代二_机器学习_数据契约.md` §3.5（产物条目）、§3.7（列模型 / 取模型）、
§4.1–§4.2（模型下载路由与下载按钮摆法）为准。

## 交付物

### 新增

- `frontend/src/api/models.ts`：模型端点封装，沿用既有 request-helper 风格
  （`getJson` / `buildUrl` / `requestText`，不硬编码主机名）。
  - `listModels()` → `GET /api/models`（契约 §3.7）；`getModel(id)` → `GET /api/models/{id}`
    （编号 `encodeURIComponent`）。
  - `modelDownloadUrl(modelId, path)` → `/api/models/download?model_id=&path=`
    （Agent 中继，基址与既有两段下载一致，可直挂 `<a download>`）。
  - `previewModelArtifact(modelId, path)`：用**同一模型下载地址**取文本，供文本类产物预览。
  - 类型（就地导出，未改动共享的 `types.ts`）：`MlArtifact`（`kind` 格式 + `role` 用途）、
    `MlModelSummary`（§3.7 摘要）、`MlModelDetail`（§3.1/§3.7 详情，含 `artifacts`）。
- `frontend/src/stores/models.ts`：模型 store。`models` / `current` / `loading`
  （`list`/`detail` 两处）/ `errors`（两处**各自隔离**）；动作 `loadModels()` /
  `loadModel(id)` / `clear()`。失败只记对应处错误、不抛，不污染另一处。
- `frontend/src/components/resources/ModelsSection.vue`：资源面板新增段组件（与既有四段
  同构，均为独立 `.vue`）。
  - 列表行：`model_id` / `task_type` / `algorithm` / `data_version` / `created_at` /
    `metrics_summary`（键值摘要，键随任务类型）。
  - 详情：产物**按 `role` 分组**（模型本体 / 群体画像 / 每用户标签 / 用户坐标 / 模型元数据；
    未知 role 归「其它」，仅渲染实际出现的组）。
  - 每个产物：`kind` 徽标 + **下载**按钮（所有 kind 均有，走模型下载路由）。
    `json`/`csv`/`jsonl`/`md` 提供**预览**（取模型下载地址文本，超 8000 字符截断并提示）；
    `joblib` 只下载，显示「不能在线预览，请下载」。

### 修改

- `frontend/src/views/panels/ResourcesPanel.vue`：新增第 5 个页签「模型」
  （`resources-tab-models` / `resources-segment-models`），连接就绪后随
  `refreshAll()` 一并拉取模型列表。

### 测试（`frontend/src/__tests__/`，新增 16 条）

- `models.test.ts`（4）：三个端点路径/方法/编码/下载地址形状与文本预览。
- `stores.models.test.ts`（4）：`loadModels` 填充与失败、`loadModel` 填充与失败、
  列表/详情错误隔离、`clear` 复位。
- `models.panel.test.ts`（8）：页签与列表字段、详情按 role 分组、详情错误只在详情区回显、
  `joblib` 不能预览提示、下载地址形状、文本预览取数（`model_id`/`path` 正确）、
  超长文本截断提示。

## 设计要点

- **契约对齐**：`kind`=格式、`role`=用途，前端严格按此分工——按 role 分组、按 kind 判定
  可否在线预览（§4.2 建议）。
- **单一来源**：可下载清单来自 `getModel` 的 `artifacts[].path`，与 `modelDownloadUrl`
  拼装一致，不会列得出下不来。
- **新增段独立成组件**：与既有四段（raw/published/schemes/files）保持同一模式，宿主仅接线。

## 验证

- `npm run test`：37 个测试文件、285 条用例全部通过（基线 269，+16）。
- `npm run build`（`vue-tsc --noEmit && vite build`）：通过。
- 用户可见文案扫描：`models.ts` / `stores/models.ts` / `ModelsSection.vue` /
  `ResourcesPanel.vue` 及三个测试文件均不含契约禁用的三类措辞。

## 约束遵循

- 仅改本任务拥有的文件（`api/models.ts`、模型 store、`ResourcesPanel.vue` 及其测试），
  另新增同目录段组件 `ModelsSection.vue`；未触碰 `api/http.ts` / `api/types.ts`。
- 未暂存 `agent/*` 的并发改动与 `frontend/.vite/` 构建缓存。
- 本地提交，未推送。

## 提交说明（并发）

本任务与另一并发任务（AG-ML-4，改 `agent/`）同时运行，双方交替读写同一 git 仓库。
并发方曾执行范围较宽的 `git add`，一度把本任务的改动并入其提交，随后又回退了该提交。
本任务**不重写**对方提交；实现代码与报告落在本地提交
`feat(frontend): 模型库（列表/详情/下载）`，未推送。

## 备注 / 待联调

- 端点形状来自实施计划 AG-ML-5（`GET /api/models`、`/api/models/{id}`）与契约 §4.1
  下载路由；Agent 侧（AG-ML-4/5）就绪后即可真数据联调。
- `metrics_summary` 展示按通用键值渲染，键随任务类型变化，不预设固定字段。
