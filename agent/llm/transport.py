# -*- coding: utf-8 -*-
"""阶段 1 模型接入层：传输层（可注入接缝）。

Provider 只负责「拼请求 + 解析流」；真正把字节送出去的是传输层。这样单测可
注入**假传输**（可控响应流 / 可控异常），**不碰真实网关**。

请求用一个普通 dict 表达：
    {"url": str, "headers": dict, "body": bytes, "timeout": float}

传输层返回一个**逐块产出响应文本**的迭代器（str 片段，不保证按行对齐；
切行由 sse.SseStreamParser 负责缓冲）。URL 层错误（HTTP 状态 / 网络）在此
抛出原始异常，由 Provider 统一分类成 ModelError。

**按 URL 分派**（不再需要独立适配器进程）：
  * host 命中 opencode 网关（opencode.ai）→ 走 curl 流式转发并补会话头
    （Cloudflare 按 TLS 指纹封禁 python-urllib；见 agent/gateway/curl_gateway.py）；
  * 其余标准 OpenAI 兼容端点（DeepSeek 官方等）→ 走标准库 urllib。
"""

import urllib.request

from ..gateway.curl_gateway import is_opencode_gateway, stream_via_curl


class HttpTransport(object):
    """真实传输：按 request["url"] 分派 —— opencode 网关走 curl，其余走 urllib。"""

    def post_stream(self, request):
        url = request.get("url") or ""
        if is_opencode_gateway(url):
            for chunk in stream_via_curl(
                    url, request.get("headers"), request.get("body"),
                    request.get("timeout")):
                yield chunk
            return

        req = urllib.request.Request(
            url, data=request.get("body"),
            headers=request.get("headers") or {}, method="POST")
        resp = urllib.request.urlopen(req, timeout=request.get("timeout") or 30.0)
        try:
            for raw_line in resp:
                yield raw_line.decode("utf-8", "replace")
        finally:
            try:
                resp.close()
            except Exception:
                pass


class FakeStreamTransport(object):
    """假传输（TX.1 假模型脚手架）：按脚本产出响应块，或抛出预置异常。

    参数：
        chunks:  依次产出的文本片段。可故意把一行切成任意边界，
                 用来验证解析器的行缓冲（真实网络本就如此）。
        error:   非 None 时在首次迭代抛出该异常（模拟鉴权 / 超时 / 网络错误）。
        requests: 记录每次收到的请求 dict，供测试断言请求头与请求体。
    """

    def __init__(self, chunks=None, error=None):
        self.chunks = list(chunks or [])
        self.error = error
        self.requests = []

    def post_stream(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        for chunk in self.chunks:
            yield chunk
