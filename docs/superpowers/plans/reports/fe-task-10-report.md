# FE-10 报告 —— 前端对齐「方案上传经远端能力服务」

任务：`docs/superpowers/plans/2026-10-18-scheme-upload-wiring.md` → `### Task FE-10：前端`
范围：仅前端（`frontend/`）。Agent 侧并发改动（`agent/`）未触碰。
背景：`POST /api/schemes/upload`（multipart，字段 `file` + 可选 `name`）继续返回 `{ok, saved:{name,path}, validation}`，并新增 `scheme` / `overwritten` / `size_bytes`；新增错误码 `SCHEME_EXISTS`。

## 1. 改动清单

| 文件 | 改动 |
| --- | --- |
| `frontend/src/api/http.ts` | `ERROR_CODE_TEXT` 增 `SCHEME_EXISTS`：`同名方案已存在：换个文件名，或允许覆盖后再传`。无「冒烟/对账/口径」。 |
| `frontend/src/api/types.ts` | `SchemeUploadResponse` 增可选 `scheme?: SchemeUploadScheme`、`overwritten?: boolean`、`size_bytes?: number`；`saved` / `validation` 原样保留。新增宽松的 `SchemeUploadScheme` 元信息类型。 |
| `frontend/src/components/resources/SchemeSection.vue` | 上传成功回执 `overwritten === true` 时渲染提示 `已覆盖同名方案：原有内容已被替换。`（`data-testid="scheme-overwritten-note"`），`pick()` 时清空；注释改为「经 Agent 转发到远端能力服务落盘」。 |
| `frontend/src/stores/resources.ts` | 上传成功后 `await loadSchemes()` 就地重列（**既有行为，已核对确认无需改**）；注释改为「经 Agent 转发到远端能力服务落盘」。 |
| `frontend/src/api/resources.ts` | 注释更新：方案上传经 Agent 转发到远端能力服务落盘；错误码列表补 `SCHEME_EXISTS`；`uploadScheme` 文档补 `scheme`/`overwritten`/`size_bytes` 与 `SCHEME_EXISTS`。 |

无「仅本机 / 本机暂存」措辞存在于前端方案相关代码；本次顺带把方案上传相关注释统一改述为「远端能力服务」，消除歧义。

## 2. 测试（TDD：先失败后通过）

新增 / 修改用例：

1. `frontend/src/__tests__/http.test.ts` — 新增：`errorCodeText('SCHEME_EXISTS')` 含「同名」「覆盖」。先跑失败（返回 `未知错误（SCHEME_EXISTS）`），加码表后通过。
2. `frontend/src/__tests__/resources.panel.test.ts` — 新增（FG.4）：假后端上传回执带 `overwritten:true, size_bytes:42`；上传前无 `scheme-overwritten-note`，上传后该提示出现且含「覆盖」「同名」。先跑失败（提示不存在），加渲染后通过。既有「上传回显 validation」「查看方案内容」用例保持不动、继续通过。
3. `frontend/src/__tests__/stores.resources.test.ts` — 新增：`uploadScheme` 透传 `overwritten`/`size_bytes` 且仍调用一次 `listSchemes`（就地重列契约守卫）。

## 3. 结果

命令（在 `frontend/` 下）：

```
npm run test      # vitest run
npm run build     # vue-tsc --noEmit && vite build
```

- 测试：**31 个文件 / 233 条用例全部通过**（改动前 31 / 231；净增 3 条）。
- 构建：`vue-tsc` 无类型错误；`vite` 生产构建成功（`✓ built`）。唯一提示为 Vite 的 chunk 体积告警（657 kB > 500 kB），属既有状态，非本次引入。

## 4. 结论与关注点

- 状态：前端全量用例绿（233/233），构建与类型检查干净。
- 内置刷新：方案列表刷新本就在 `stores/resources.ts` 的 `uploadScheme` 内（`await loadSchemes()`），本次核对确认无需改动；面板用例仍断言 `/api/schemes` 被二次请求（`>= 2`）。
- 覆盖提示：`overwritten` 为**可选**字段，仅当远端显式回 `true` 时提示；缺省 / `false` 均不显示，兼容旧后端。
- 未削弱、未删除任何既有用例；仅改动前端文件，未与 Agent 侧改动重叠。
