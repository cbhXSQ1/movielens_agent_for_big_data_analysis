# -*- coding: utf-8 -*-
"""远端两段只读下载路由的带口令流式取回。

远端 Hadoop 能力服务以普通 HTTP GET 暴露三条只读路由（都要口令）：
    /download/task/{task_id}/{path}
    /download/published/{data_version}/{table}
    /download/model/{model_id}/{path}

浏览器不能持有口令，因此由 Agent 带 `Authorization: Bearer <口令>` 取回，再把响应体
**流式**转给浏览器（一次只在内存里放一块，绝不整份读进内存）。

非 2xx 时读远端信封，**原样保留**它的 `error.code` / `error.message` 与其余细节
（如 `reason_kind`），不把结构化错误改写成泛化错误。

红线：口令只放进请求头，**绝不**写日志 / 信封 / 任何用户可见输出。
"""

import re
from urllib.parse import quote

from .mcp_http_client import explain

#: 下载失败码 → 给浏览器看的 HTTP 状态（与远端 `hadoop/mcpserver/server.py` 的下载
#: 映射同一套；`MCP_UNAVAILABLE` 是 Agent 侧新增的"未接入"，按不可达处理）。
STATUS_BY_CODE = {
    "UNAUTHORIZED": 401,
    "FORBIDDEN_HOST": 403,
    "FORBIDDEN_PATH": 400,
    "USAGE": 400,
    "FILE_NOT_FOUND": 404,
    "PRODUCT_INVALID": 404,
    "TASK_NOT_FOUND": 404,
    "MCP_UNAVAILABLE": 503,
    "DRIVER_UNREACHABLE": 503,
    "DRIVER_TIMEOUT": 504,
}

#: 远端只回了 HTTP 状态、没给结构化信封时的兜底码（保留一层可读语义）。
_CODE_BY_STATUS = {
    400: "USAGE",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN_HOST",
    404: "FILE_NOT_FOUND",
    503: "DRIVER_UNREACHABLE",
    504: "DRIVER_TIMEOUT",
}

#: 既无信封又无已知状态时的兜底码。
DEFAULT_ERROR_CODE = "DRIVER_BAD_OUTPUT"

#: 本机越界时回的结构化错误（与远端 `forbidden_path` 同一契约；不发起请求）。
FORBIDDEN_PATH_ERROR = {"code": "FORBIDDEN_PATH", "message": "该路径不允许访问"}

#: 模型编号形状不合法时回的结构化错误（契约 §4.1；不发请求）。
USAGE_ERROR = {"code": "USAGE", "message": "模型编号不合法"}

#: 模型编号形状：单一分段，只允许字母数字与 `.` `-` `_`（契约 §4.1）。
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _has_forbidden_form(value):
    """值里有没有「本机必须先拒」的形态：NUL / 反斜杠 / 绝对路径。"""
    s = "" if value is None else str(value)
    return "\x00" in s or "\\" in s or s.startswith("/")


def _safe_relpath(relpath):
    """任务产物相对路径：允许多段 `a/b/c`，拒绝对路径 / 反斜杠 / NUL / `..` 段。

    httpx 发送前会按 RFC 3986 归一化 URL 的点段；若放行 `..`，
    `/download/task/{id}/../../etc/passwd` 会被折叠掉路由段（越界被静默改写），
    因此先把 `..` 段挡在本机。隐藏文件/控制文件的判断仍留给服务端（第二道防线）。
    """
    if _has_forbidden_form(relpath):
        return False
    text = "" if relpath is None else str(relpath)
    return ".." not in text.split("/")


def _safe_segment(value):
    """已发布下载的单一路径分段：不得含 `/`，也不得是 `.` / `..` / 绝对 / 反斜杠。"""
    if _has_forbidden_form(value):
        return False
    text = "" if value is None else str(value)
    return "/" not in text and text not in (".", "..")


def _safe_model_id(model_id):
    """模型编号：单一分段 `[A-Za-z0-9._-]+`，且不得是 `.` / `..`（契约 §4.1）。"""
    text = "" if model_id is None else str(model_id)
    if text in (".", ".."):
        return False
    return bool(_MODEL_ID_RE.match(text))



def status_for_code(code, default=502):
    """结构化错误码 → 给浏览器的 HTTP 状态（未知码按 502，如实暴露异常）。"""
    return STATUS_BY_CODE.get(code, default)


def base_url(config):
    """从配置里取服务根地址（去掉协议入口后缀 `/mcp`）。"""
    url = (getattr(config, "url", "") or "").strip()
    for suffix in ("/mcp", "/"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    return url


def _error_from_response(response):
    """非 2xx：读信封 JSON，**保留**服务端的 `error.code` / `message` 与细节。

    读不到合法信封时按 HTTP 状态给兜底码，不臆造成功、不吞掉失败。
    """
    payload = None
    try:
        payload = response.json()
    except Exception:                                # noqa: BLE001 —— 非 JSON 就是没有信封
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            out = {
                "code": error.get("code") or _CODE_BY_STATUS.get(
                    response.status_code, DEFAULT_ERROR_CODE),
                "message": error.get("message") or "远端下载失败",
            }
            for key, value in error.items():
                if key not in ("code", "message"):
                    out[key] = value             # reason_kind / path 等细节原样带出
            return out
    return {"code": _CODE_BY_STATUS.get(response.status_code, DEFAULT_ERROR_CODE),
            "message": "远端下载失败（HTTP %s）" % response.status_code}


def _close_quietly(obj):
    try:
        obj.close()
    except Exception:                            # noqa: BLE001 —— 收尾不掩盖主流程
        pass


def _iter_stream(response, client):
    """逐块吐出响应体；无论正常结束还是对端断开都关掉响应与客户端。"""
    try:
        for chunk in response.iter_bytes():
            if chunk:
                yield chunk
    finally:
        _close_quietly(response)
        _close_quietly(client)


def _open(config, url, transport=None):
    """发起一次流式 GET，返回 `(iterator, headers, error)`。三选一非空。"""
    import httpx

    if config is None or not getattr(config, "usable", False):
        return None, None, {"code": "MCP_UNAVAILABLE",
                            "message": "远端能力服务未接入或未配置：下载"}

    if transport is None:
        transport = getattr(config, "transport", None)   # 仅测试注入用接缝

    headers = {"Authorization": "Bearer %s" % config.token}
    client = httpx.Client(transport=transport,
                          timeout=getattr(config, "timeout_sec", 15.0))
    try:
        request = client.build_request("GET", url, headers=headers)
        response = client.send(request, stream=True)
    except Exception as exc:                         # noqa: BLE001 —— 这里就是要报清楚
        _close_quietly(client)
        return None, None, {"code": "DRIVER_UNREACHABLE",
                            "message": explain(exc, url)}

    if not (200 <= response.status_code < 300):
        error = _error_from_response(response)
        _close_quietly(response)
        _close_quietly(client)
        return None, None, error

    out_headers = {}
    disposition = response.headers.get("content-disposition")
    if disposition:
        out_headers["Content-Disposition"] = disposition
    ctype = response.headers.get("content-type")
    if ctype:
        out_headers["Content-Type"] = ctype
    length = response.headers.get("content-length")
    if length:
        out_headers["Content-Length"] = length
    return _iter_stream(response, client), out_headers, None


def open_task_stream(config, hadoop_task_id, relpath, transport=None):
    """任务产物下载（`/download/task/{task_id}/{path}`）；返回 `(iterator, headers, error)`。"""
    if not _safe_relpath(relpath):
        return None, None, dict(FORBIDDEN_PATH_ERROR)   # 本机先拒：不发请求
    url = "%s/download/task/%s/%s" % (
        base_url(config), quote(str(hadoop_task_id or ""), safe=""),
        quote(str(relpath or ""), safe="/"))
    return _open(config, url, transport)


def open_published_stream(config, data_version, table, transport=None):
    """已发布数据下载（`/download/published/{data_version}/{table}`）。"""
    if not (_safe_segment(data_version) and _safe_segment(table)):
        return None, None, dict(FORBIDDEN_PATH_ERROR)   # 本机先拒：不发请求
    url = "%s/download/published/%s/%s" % (
        base_url(config), quote(str(data_version or ""), safe=""),
        quote(str(table or ""), safe=""))
    return _open(config, url, transport)


def open_model_stream(config, model_id, relpath, transport=None):
    """模型产物下载（`/download/model/{model_id}/{path}`）；返回 `(iterator, headers, error)`。

    与任务/已发布下载同构：同一口令头、同一错误语义与状态映射。`relpath` 是模型目录内的
    **多级**相对路径，沿用同一越界守卫（拒 `..` 段 / 绝对 / 反斜杠 / NUL）；`model_id` 须是
    单一分段，形状不合法回 `USAGE`（契约 §4.1）。
    """
    if not _safe_model_id(model_id):
        return None, None, dict(USAGE_ERROR)            # 本机先拒：不发请求
    if not _safe_relpath(relpath):
        return None, None, dict(FORBIDDEN_PATH_ERROR)   # 本机先拒：不发请求
    url = "%s/download/model/%s/%s" % (
        base_url(config), quote(str(model_id or ""), safe=""),
        quote(str(relpath or ""), safe="/"))
    return _open(config, url, transport)
