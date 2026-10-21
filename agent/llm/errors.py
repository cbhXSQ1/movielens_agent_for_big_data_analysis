# -*- coding: utf-8 -*-
"""阶段 1 模型接入层：结构化错误（T1.4）。

模型不可用时**直接返回结构化错误，不静默回退**到其它路径。错误分类互不
重叠，消费方可据此区别对待（健康检查、提示文案、是否值得重试）：

    NOT_CONFIGURED      未配置（或配置不完整）
    PROVIDER_UNKNOWN    未知供应商
    AUTH_FAILED         鉴权失败（401/403）
    BAD_PATH            路径不对（404）
    RATE_LIMITED        限流 / 额度耗尽（429）
    BAD_REQUEST         请求非法（400，多为参数或模型名问题）
    TIMEOUT             超时
    NETWORK_UNREACHABLE 网络不可达
    UPSTREAM_ERROR      上游 5xx
    HTTP_ERROR          其它 HTTP 状态
    BAD_RESPONSE        响应非法（非 JSON / 结构不符）
    MODEL_ERROR         网关在流内返回的显式错误

所有错误都以 ModelError 抛出；to_envelope() 给出接口文档 §3.10 的错误信封，
**信封里绝不含密钥**。
"""

import socket
import urllib.error

NOT_CONFIGURED = "NOT_CONFIGURED"
PROVIDER_UNKNOWN = "PROVIDER_UNKNOWN"
AUTH_FAILED = "AUTH_FAILED"
BAD_PATH = "BAD_PATH"
RATE_LIMITED = "RATE_LIMITED"
BAD_REQUEST = "BAD_REQUEST"
TIMEOUT = "TIMEOUT"
NETWORK_UNREACHABLE = "NETWORK_UNREACHABLE"
UPSTREAM_ERROR = "UPSTREAM_ERROR"
HTTP_ERROR = "HTTP_ERROR"
BAD_RESPONSE = "BAD_RESPONSE"
MODEL_ERROR = "MODEL_ERROR"

#: HTTP 状态码 → (错误码, 面向用户的中文说明)。文案与现有 llm_client.py 一致。
HTTP_CODE_MAP = {
    400: (BAD_REQUEST, "请求非法（多为参数或模型名写错）"),
    401: (AUTH_FAILED, "API key 无效或没权限（检查 key；重新生成后要等几分钟生效）"),
    403: (AUTH_FAILED, "API key 无效或没权限（检查 key，或账号没开通这个模型）"),
    404: (BAD_PATH, "路径不存在（检查 api_base 是否需要带 /v1 结尾）"),
    408: (TIMEOUT, "网关请求超时"),
    429: (RATE_LIMITED, "被限流或额度用完（等一会儿再试，或去后台看额度）"),
}


class ModelError(Exception):
    """结构化的模型接入错误：code 稳定、message 面向用户、detail 供定位。"""

    def __init__(self, code, message, detail="", status=0):
        Exception.__init__(self, message)
        self.code = code
        self.message = message
        self.detail = detail or ""
        self.status = int(status or 0)

    def to_envelope(self):
        """接口文档 §3.10 的错误信封（不含密钥）。"""
        err = {"code": self.code, "message": self.message}
        if self.detail:
            err["detail"] = self.detail
        return {"ok": False, "error": err}

    def __str__(self):
        if self.status:
            return "[%s] %s (HTTP %s)" % (self.code, self.message, self.status)
        return "[%s] %s" % (self.code, self.message)


def _read_body(exc, limit=300):
    """尽力读出错误响应体片段（读不到就空串），用于 detail 定位。"""
    try:
        raw = exc.read(limit)
    except Exception:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", "replace")
    return str(raw or "")


def http_error_to_model_error(exc):
    """urllib.error.HTTPError → ModelError。"""
    status = int(getattr(exc, "code", 0) or 0)
    code, human = HTTP_CODE_MAP.get(status, (None, None))
    detail = _read_body(exc)
    if code is None:
        if 500 <= status < 600:
            code, human = UPSTREAM_ERROR, "上游服务错误（HTTP %s）" % status
        else:
            code, human = HTTP_ERROR, "HTTP %s：%s" % (status, getattr(exc, "reason", ""))
    return ModelError(code, human, detail=detail, status=status)


def classify_http_code(status, detail=""):
    """按 HTTP 状态码归类（供非 urllib 异常或直接码值使用）。"""
    status = int(status or 0)
    code, human = HTTP_CODE_MAP.get(status, (None, None))
    if code is None:
        if 500 <= status < 600:
            return ModelError(UPSTREAM_ERROR, "上游服务错误（HTTP %s）" % status,
                              detail=detail, status=status)
        return ModelError(HTTP_ERROR, "HTTP %s" % status, detail=detail, status=status)
    return ModelError(code, human, detail=detail, status=status)


def classify_exception(exc):
    """把任意传输/解析异常归成 ModelError（已结构化的原样透传）。"""
    if isinstance(exc, ModelError):
        return exc
    if isinstance(exc, urllib.error.HTTPError):
        return http_error_to_model_error(exc)
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return ModelError(TIMEOUT, "在超时时间内未收到响应（网络或网关慢；"
                                   "key 和 url 多半没问题）")
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", "")
        text = str(reason).lower()
        if isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in text:
            return ModelError(TIMEOUT, "在超时时间内未收到响应（网络或网关慢）")
        return ModelError(NETWORK_UNREACHABLE,
                          "网络不可达：%s（检查 api_base 域名写没写对）" % (reason,))
    if isinstance(exc, (ConnectionError, OSError)):
        return ModelError(NETWORK_UNREACHABLE, "网络不可达：%s" % (exc,))
    if isinstance(exc, ValueError):
        return ModelError(BAD_RESPONSE, "响应不是合法 JSON：%s" % (exc,))
    return ModelError(MODEL_ERROR, "模型接入异常：%s" % (exc,))
