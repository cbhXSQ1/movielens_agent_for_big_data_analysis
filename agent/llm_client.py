# -*- coding: utf-8 -*-
"""第 5 节 Agent：大模型可选层 —— HTTP 调用（只用标准库 urllib）。

为什么不用 requests / openai sdk：本工程零第三方依赖、机房离线也要能跑，
引入任何一个 pip 包都会破坏这个性质（也是本节的答辩加分项）。

设计：
  * 单次调用受 cfg.timeout_sec 约束（默认 60s，硬上限 300s）
  * 任何异常（DNS / TLS / 超时 / 非 200 / JSON 坏了 / 字段缺失）都返回 (None, 原因)
  * 不重试：宁可立刻回落规则，也不让用户干等
  * 响应体读取上限 256KB，超限直接丢弃
  * urllib 自动识别 http_proxy / https_proxy 环境变量，校园网代理环境无需额外处理
  * 不做 ssl 免校验 —— 不为了"能跑"而关掉证书检查
"""

import json
import socket
import urllib.error
import urllib.request

from .gateway.curl_gateway import is_opencode_gateway, stream_via_curl

MAX_BYTES = 256 * 1024
_LAST_ERROR = None


def last_error():
    """最近一次失败原因（给上层写进 llm.note，用于如实交代）。"""
    return _LAST_ERROR


def set_last_error(msg):
    """供上层模块在 chat 成功后的**后处理失败**时也留下原因（L4）。

    否则 chat 成功会把 _LAST_ERROR 清空，后续 JSON 解析失败时上层只读到
    None → 界面显示「未知原因」，排查方向丢失。
    """
    global _LAST_ERROR
    _LAST_ERROR = msg


def _upstream_error_text(message, error_type=""):
    """把上游错误信封翻成能照着排查的话（不把网关问题误报成 key 问题）。"""
    text = (message or "").strip()
    etype = (error_type or "").strip()
    lowered = (text + " " + etype).lower()
    if "missingsessionid" in lowered or "x-opencode-session" in lowered:
        return ("网关缺少会话头 x-opencode-session：Agent 已内置 curl 适配，"
                "请确认 api_base 指向 opencode.ai 且由本后端转发")
    if "model" in lowered and (
        "not found" in lowered or "not available" in lowered
        or "no permission" in lowered or "not allowed" in lowered
    ):
        return ("模型不可用：%s（模型名要填 Model ID，如 deepseek-v4.1-flash，不是显示名）"
                % (text or etype))
    if "invalid" in lowered or "unauthorized" in lowered or "permission" in lowered:
        return "上游拒绝：%s" % (text or etype or "无权限")
    return "上游拒绝：%s" % (text or etype or "未知原因")


def _gateway_post(cfg, data, headers):
    """经 curl 适配器请求 opencode 网关（自动补 x-opencode-session）。

    非流式响应对应整段 JSON；curl 适配器会把它包成一帧 `data: ...`，这里剥掉。
    """
    text = "".join(
        stream_via_curl(cfg.endpoint(), headers, data, cfg.timeout_sec)
    ).strip()
    if text.startswith("data:"):
        text = text[len("data:"):].strip()
    return text


def _post_chat(cfg, payload):
    """POST /v1/chat/completions，返回 (响应 dict, None) 或 (None, 错误原因)。

    按 api_base **自动分派**（无需任何额外环境变量）：
      * opencode 网关（host 命中 opencode.ai）→ curl 适配器 + 自动补会话头；
      * 其余标准 OpenAI 兼容端点 → urllib。
    错误按上游真实原因分类（鉴权 / 模型 / 路径 / 限流 / 会话头），不把网关问题
    误报成"key 无效"。
    """
    global _LAST_ERROR
    _LAST_ERROR = None

    if cfg is None or not getattr(cfg, "usable", False):
        _LAST_ERROR = "大模型未配置或配置不完整"
        return None, _LAST_ERROR

    try:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": "Bearer " + cfg.api_key,
        }
        url = cfg.endpoint()
        if is_opencode_gateway(url):
            text = _gateway_post(cfg, data, headers)
        else:
            req = urllib.request.Request(
                url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=cfg.timeout_sec) as resp:
                text = resp.read(MAX_BYTES + 1).decode("utf-8", errors="replace")

        if len(text.encode("utf-8")) > MAX_BYTES:
            _LAST_ERROR = "响应超过 %d 字节，已丢弃" % MAX_BYTES
            return None, _LAST_ERROR

        body = json.loads(text)
        # 上游错误信封：网关 4xx 时 curl 不抛异常，body 本身就是错误 JSON。
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict) and not body.get("choices"):
            _LAST_ERROR = _upstream_error_text(
                error.get("message"), error.get("type") or error.get("code"))
            return None, _LAST_ERROR

        choices = body.get("choices") or []
        if not choices:
            _LAST_ERROR = "返回了空的 choices"
            return None, _LAST_ERROR
        return body, None

    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read(300).decode("utf-8", errors="replace")
        except Exception:
            pass
        parsed = None
        try:
            parsed = json.loads(detail)
        except (ValueError, TypeError):
            parsed = None
        upstream = parsed.get("error") if isinstance(parsed, dict) else None
        if isinstance(upstream, dict):
            _LAST_ERROR = _upstream_error_text(
                upstream.get("message"), upstream.get("type") or upstream.get("code"))
            return None, _LAST_ERROR
        # 常见状态码说人话，让「测试连接」能一眼分清锅在哪（R15，2026-09-29）
        human = {
            401: "API key 无效或没权限（检查 key，重新生成后要等几分钟生效）",
            403: "API key 无效或没权限（检查 key，或账号没开通这个模型）",
            404: "路径不存在（检查 api_base 是否需要带 /v1 结尾）",
            429: "被限流或额度用完（等一会儿再试，或去后台看额度）",
        }.get(exc.code)
        if human:
            _LAST_ERROR = "HTTP %s：%s" % (exc.code, human)
        else:
            _LAST_ERROR = "HTTP %s：%s" % (exc.code, detail or exc.reason)
        return None, _LAST_ERROR
    except (TimeoutError, socket.timeout):
        # 超时 ≠ key/url 错。能拿到 HTTP 状态的错误已经在上面分好类了，
        # 走到这里说明请求发出去后对方一直没回——锅在网络/网关慢。
        _LAST_ERROR = "%s 秒内未收到响应（网络或网关慢；key 和 url 多半没问题，多重试两次）" % cfg.timeout_sec
        return None, _LAST_ERROR
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", "")
        if isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in str(reason).lower():
            _LAST_ERROR = "%s 秒内未收到响应（网络或网关慢；key 和 url 多半没问题，多重试两次）" % cfg.timeout_sec
        else:
            _LAST_ERROR = "网络不可达：%s（检查 api_base 域名写没写对）" % (reason,)
        return None, _LAST_ERROR
    except OSError as exc:
        _LAST_ERROR = "网关转发失败：%s" % (exc,)
        return None, _LAST_ERROR
    except Exception as exc:
        _LAST_ERROR = "调用异常：%s" % (exc,)
        return None, _LAST_ERROR


def chat(cfg, system, user, max_tokens=None, temperature=None):
    """调一次 OpenAI 兼容的 chat/completions（单轮 system + user）。

    返回 (content_str, None) 或 (None, 错误原因字符串)。**不抛异常。**
    """
    payload = {
        "model": cfg.model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": cfg.temperature if temperature is None else temperature,
        "max_tokens": int(max_tokens or cfg.max_tokens),
        "stream": False,
    }
    body, err = _post_chat(cfg, payload)
    if body is None:
        return None, err
    msg = ((body.get("choices") or [{}])[0] or {}).get("message") or {}
    content = msg.get("content")
    if not isinstance(content, str) or not content.strip():
        # 推理型模型常见：max_tokens 被推理段耗尽（finish_reason=length）
        finish = (body.get("choices") or [{}])[0].get("finish_reason") or ""
        if finish == "length":
            _LAST_ERROR = ("返回的 content 为空（max_tokens 被推理过程耗尽，"
                           "把 max_tokens 调大，如 AGENT_LLM_MAX_TOKENS=1024）")
        else:
            _LAST_ERROR = "返回的 content 为空"
        return None, _LAST_ERROR
    return content.strip(), None


def chat_messages(cfg, messages, max_tokens=None, temperature=None, tools=None):
    """全消息形态调用：直接传完整 messages（可含 assistant/tool 轮与原生 tool_calls）。

    返回 (响应 dict, None) 或 (None, 错误原因)；调用方自取 choices[0].message
    （需看 tool_calls）。tools 存在时原生 function calling 可用；错误文案与
    chat() 完全一致（共用 _post_chat）。
    """
    payload = {
        "model": cfg.model,
        "messages": messages,
        "temperature": cfg.temperature if temperature is None else temperature,
        "max_tokens": int(max_tokens or cfg.max_tokens),
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
    return _post_chat(cfg, payload)
