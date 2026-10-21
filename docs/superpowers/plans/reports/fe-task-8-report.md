# FE-8 报告：错误码中文表扩充

## 状态
完成。TDD（先写失败用例 → 确认失败 → 补表 → 全绿）。

## 提交
- sha: `923fdb4b478cad0bd9ad54ec8edb3ac2f995e47e`
- message: `feat(frontend): 扩充远端错误码中文说明`
- 范围：仅前端两个文件，未推送
  - `frontend/src/api/http.ts`
  - `frontend/src/__tests__/http.test.ts`

## 改动内容
在 `ERROR_CODE_TEXT` 中按文档 §5「建议给用户的话」与 `agent/mcp_contract.py::ERROR_MESSAGES` 的语义补齐/改写：

| 码 | 中文 |
|---|---|
| ABORTED | 任务已中止：不会再变化，没有结果可取 |
| FILE_NOT_FOUND | 文件不存在：可能已被清理 |
| FORBIDDEN_PATH | 路径越界：该路径不在白名单内，不允许访问 |
| CONTENT_TOO_LARGE | 文件太大，无法在界面预览：请下载后查看 |
| NOT_PREVIEWABLE | 该文件不支持在线预览：请下载后查看 |
| PRODUCT_INVALID | 产物内容异常：可以先看其它产物或日志 |
| CANCEL_FAILED | 中止未生效：没能停住这个任务，原因见说明 |
| UNAUTHORIZED | 连接凭据不正确：请检查口令配置 |
| FORBIDDEN_HOST | 访问地址不被允许：通常是端口转发配置有误 |
| WAIT_LIMIT_REACHED | 现在等待的人太多：请缩短等待时间或稍后再试 |
| INTERNAL_ERROR | 服务端内部错误（原有，保留） |
| DRIVER_UNREACHABLE | 无法连接执行端：请确认服务是否已启动 |
| DRIVER_TIMEOUT | 执行端响应超时：请稍后重试 |
| TASK_NOT_FINISHED | 任务尚未产出结果（原有，保留） |

- 未删除任何既有码；保留原有测试断言的语义（如 `FORBIDDEN_PATH` 仍含「白名单」、`DRIVER_TIMEOUT` 仍含「超时」）。
- 文案均使用自然语言描述场景，**不含**「冒烟 / 对账 / 口径」。

## 测试
- `frontend/src/__tests__/http.test.ts` 新增用例「远端新增错误码有自然中文说明」，覆盖全部 14 个码，断言 ABORTED→含「已中止」、WAIT_LIMIT_REACHED→含「等待」等。
- 单文件：`npm run test -- http.test` → 12 passed。
- 全量：`npm run test`（`frontend/`）→ **29 个文件 / 213 用例全部通过**。

## 顾虑 / 备注
1. **ABORTED 语义复用**：前端网络层 `doFetch` 在调用方主动取消请求时也抛 `ABORTED`（`http.ts:185`）。现按远端「任务已中止（终态）」语义给出文案，用户主动取消请求时会看到同样的说明。若后续要区分，需为前端取消另设码（如 `REQUEST_CANCELED`）。本次按任务要求对齐远端语义。
2. 类型检查 `npx vue-tsc --noEmit` 存在报错，但全部来自并发任务的未完成改动（`src/__tests__/composables.test.ts`、`src/__tests__/result.versions.test.ts`，与本任务无关）；本任务改动的 `http.ts` / `http.test.ts` 无类型错误。
3. 提交仅包含本任务两个前端文件，未 push。

---

# FE-8 追加报告：分离本地取消码与远端中止码

## 状态
完成。仅改动前端两个文件，未推送。

## 问题
`frontend/src/api/http.ts` 的 `doFetch` 在调用方主动取消请求（外部 `AbortSignal`）时抛出的错误码是 `ABORTED`，
而 `ERROR_CODE_TEXT` 中 `ABORTED` 现表示远端「任务已中止（终态、无结果可取）」。两个语义冲突，
用户取消一个请求会被描述成「任务已中止」。

## 改动
`frontend/src/api/http.ts`：
- 在 `ERROR_CODE_TEXT` 的前端网络层自有码区域新增 `REQUEST_CANCELLED: '请求已取消'`；
- `ABORTED` 条目保留为远端语义（「任务已中止：不会再变化，没有结果可取」），并更新注释，明确两者语义无关；
- `doFetch` 中 `controller.signal.aborted` 分支由抛 `ABORTED` 改为抛 `REQUEST_CANCELLED`（超时仍为 `TIMEOUT`、
  其它 fetch 失败仍为 `NETWORK_ERROR`，取消行为其它逻辑不变）。

`frontend/src/__tests__/http.test.ts`：
- 新增用例「本地取消码与远端中止码语义分离」：断言 `REQUEST_CANCELLED` 含「已取消」、`ABORTED` 含「任务已中止」且
  `REQUEST_CANCELLED` 不含「任务已中止」；
- 新增用例「外部 signal 取消 → REQUEST_CANCELLED（不是远端 ABORTED）」：用 `AbortController` 触发取消，断言抛出码为 `REQUEST_CANCELLED`；
- 既有「远端新增错误码有自然中文说明」中对 `ABORTED` 含「已中止」的断言保留不动。

`ERROR_CODE_TEXT` 文案均不含禁用词。

## 命令与输出
- 测试：`npm run test`（工作目录 `frontend/`）
  - 输出摘要：`Test Files 1 failed | 29 passed (30)`，`Tests 3 failed | 221 passed (224)`。
  - 通过的 3 个失败全部位于 `src/__tests__/progress.panel.test.ts` 的新增 FH.5 用例（并发任务未提交改动，
    报错 `Cannot call text on an empty DOMWrapper`，与本改动无关）。
  - 本任务文件：`src/__tests__/http.test.ts (14 tests)` 全绿。
- 定向测试：`npm run test -- progress.panel` 复现同为 3 failed / 10 passed，确认与本改动无关。
- 类型检查：`npx vue-tsc --noEmit`
  - 输出：仅 `src/components/progress/TaskDetail.vue(26,3)` 与 `(27,3)` 的 `TS6133`（未使用变量 `abortedHint` / `activeHint`），
    来自并发任务；本任务改动的 `http.ts` / `http.test.ts` 无类型错误。

## 顾虑
1. 前端全量测试当前非全绿，失败与类型报错均来自并发任务对 `progress.panel.test.ts` / `TaskDetail.vue` 的未完成改动；
   建议在并发任务落地后复跑全量。
2. 本提交仅包含 `frontend/src/api/http.ts` 与 `frontend/src/__tests__/http.test.ts` 两个文件。
