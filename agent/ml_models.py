# -*- coding: utf-8 -*-
"""契约 §3.7「列模型 / 取模型」的 Agent 落地层。

`list_ml_models` / `get_ml_model` 的**薄封装 + 产物地址拼装**：只调远端、只做字段
映射，**不产生任何业务数字、也不调用任何模型**。

出口形状与前端契约一致：`list_models` → `{"models":[...]}`，`get_model` →
`{"model":{...}}`。模型产物的可下载地址不靠猜路径，而是由 `model_id` 与
`get_ml_model` 的 `artifacts[].path` 拼成前端模型下载路由
（`agent.http_api` 的 `GET /api/models/download?model_id=&path=`）。

错误语义与 `agent/resources.py` 完全一致（复用其 `_remote`）：
  * `client is None`（本机未接入远端）→ `MCP_UNAVAILABLE`，不发起任何网络请求；
  * 远端 `ok=false` → **原样保留**它的 `error.code` / `message` 与其余细节；
  * 远端返回非对象信封 → `DRIVER_BAD_OUTPUT`。
"""

from urllib.parse import quote

from .resources import ResourceError, _remote

#: 前端模型产物下载路由（契约 §4.1；带口令由 Agent 流式中转）。
MODEL_DOWNLOAD_ROUTE = "/api/models/download"


def list_models(client):
    """列模型（远端 `list_ml_models`）；出口 `{"models":[...]}`。

    逐条保留远端 `models` 数组里每个对象（契约 §3.7：`model_id` / `task_type` /
    `algorithm` / `data_version` / `created_at` / `metrics_summary` 等），过滤非对象项，
    不臆造条目、不补占位值。
    """
    env = _remote(client, "list_ml_models")
    models = []
    for item in env.get("models") or []:
        if isinstance(item, dict):
            models.append(dict(item))
    return {"models": models}


def get_model(client, model_id):
    """取模型详情（远端 `get_ml_model`）；出口 `{"model":{...}}`。

    远端字段与契约 §3.1 对齐（`metrics` / `artifacts` / `params` / `time_boundaries`
    全给）；不存在时原样转述其 `MODEL_NOT_FOUND`。
    """
    env = _remote(client, "get_ml_model", {"model_id": str(model_id)})
    return {"model": env.get("model")}


def model_download_path(model_id, path):
    """拼模型产物下载地址：`/api/models/download?model_id=&path=`（两值 URL 编码）。

    供前端据 `get_ml_model` 的 `artifacts[].path` 拼出可点击地址；口令由 Agent 侧在
    中转时加，绝不出现在地址里。
    """
    return "%s?model_id=%s&path=%s" % (
        MODEL_DOWNLOAD_ROUTE,
        quote("" if model_id is None else str(model_id), safe=""),
        quote("" if path is None else str(path), safe=""))
