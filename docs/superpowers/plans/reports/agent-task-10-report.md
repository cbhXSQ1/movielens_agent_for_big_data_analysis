# Agent 远端 MCP 接入 · Task 10 报告：原始数据上传下线

计划：`docs/superpowers/plans/2026-10-18-agent-remote-mcp.md` → 「### Task 10: 原始数据上传下线，列表改远端只读」

> 本报告只完成 Task 10 的**上传下线**一半；「列表改远端只读」是后续 Task（AG-11），
> 本轮**保持原始数据列举现状不变**（仍走本机 `resources.list_raw`）。

## 目标

远端 Hadoop 能力服务**不对外提供原始数据上传**，Agent 侧随之停止接受与转发原始数据上传；
方案（scheme）上传**保留本机**。原始数据的**列举**本轮不动。

## 改动

### 1. `agent/resources.py`

- 删除 `upload_raw(parts)`：原本地落盘/解包实现。
- 删除 `_extract_zip(filename, data, dest)`：`upload_raw` 专用，删除后不可达。
- 删除随之死掉的常量 `MAX_RAW_UPLOAD_BYTES`、`RAW_EXTS`（经全仓核对，除 `upload_raw`
  外无任何引用）。
- 删除仅被 `_extract_zip` 使用的 `import io`、`import zipfile`。
- 更新模块 docstring：去掉「原始上传」措辞，说明上传已下线、本机只保留方案上传与只读列举。
- **保留**：`list_raw` / `_dataset_entry`（枚举现状未改）、`save_scheme`、`_validate_saved`、
  `kind_of`、任务文件/预览/下载、白名单与大小上限（`MAX_REQUEST_BYTES` /
  `MAX_SCHEME_UPLOAD_BYTES` / `MAX_PREVIEW_BYTES`）、`SCHEME_EXTS` 等。

### 2. `agent/http_api.py`

- 删除 `_post_upload` 里 `path == "/api/data/raw/upload"` 的上传分支（原第 569-571 行）。
- `_post_upload` 现在只处理 `/api/schemes/upload`；更新其 docstring，说明原始上传已下线。
- 路由判断（原第 976 行）由 `("/api/data/raw/upload", "/api/schemes/upload")` 收窄为
  `"/api/schemes/upload"`。此后对 `/api/data/raw/upload` 的 POST 落入未匹配路径 →
  `404`（`未知路径` 信封）。
- 更新文件头端点清单：删去「上传原始数据 POST /api/data/raw/upload」一行。
- **保留**：`/api/data/raw` 列举、`/api/data/published`、`/api/schemes/upload`、
  任务文件、预览、下载等全部不动。

### 3. `agent/tests/test_resources_api.py`

- 删除 5 条原始上传用例：`test_upload_dat_lands_under_name`、`test_upload_zip_is_extracted`、
  `test_upload_default_name_from_filename`、`test_unsupported_extension_rejected`、
  `test_zip_slip_rejected`。
- 新增 `test_raw_upload_route_removed`：向 `/api/data/raw/upload` 发 POST → 断言 `404`
  （路由已不存在，不再被上传处理）。
- 删除随之无用的 `zip_bytes` 辅助函数与 `import io` / `import zipfile`；更新模块 docstring。

## 验证

```
python -m pytest agent/tests/test_resources_api.py -q
17 passed
```

```
python -m pytest agent/tests -q
366 passed, 237 subtests passed
```

（基线 370 passed；本轮净减 4 = 删 5 条上传用例、加 1 条路由下线用例。全绿，无失败/报错。）

## 约束核对

- 仅本地改动，**未对远端 `127.0.0.1:8900` 发起任何调用**；无网络/远端测试。
- 未触碰方案上传、原始列举、已发布列举、任务文件、预览、下载。
- 未使用「冒烟 / 对账 / 口径」等受限词，未改文档口径。
- 全仓 `upload_raw` 残留匹配均为无关项（`hadoop/scripts/upload_raw.sh`、
  任务日志文件名 `logs/upload_raw.log`、前端上传入口），不在本任务范围。

## 遗留 / 关注

- **前端未改**：`frontend/src/api/resources.ts` 与相关测试仍引用 `/api/data/raw/upload`，
  前端「资源」页上传入口需另行下线（本任务限 Agent 侧）。
- **列表改远端只读**（计划 Task 10 后半 / AG-11）未做，`/api/data/raw` 仍读本机原始区。
- `_post_upload(self, path, length)` 的 `path` 形参在下线后已不再被读取，保留以维持调用
  形状（调用点仍传 `u.path`）；如需清理可后续随 AG-11 一并处理。

## 提交

一个本地提交（不推送）：见提交记录。
