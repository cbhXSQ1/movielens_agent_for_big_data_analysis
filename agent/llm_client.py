# -*- coding: utf-8 -*-
"""第 5 节 Agent：大模型可选层 —— HTTP 调用（只用标准库 urllib）。

为什么不用 requests / openai sdk：本工程零第三方依赖、机房离线也要能跑，
引入任何一个 pip 包都会破坏这个性质（也是本节的答辩加分项）。

设计：
  * 单次调用受 cfg.timeout_sec 约束（默认 3s，硬上限 5s，配大也没用）
  * 任何异常（DNS / TLS / 超时 / 非 200 / JSON 坏了 / 字段缺失）都返回 (None, 原因)
  * 不重试：宁可立刻回落规则，也不让用户干等
  * 响应体读取上限 256KB，超限直接丢弃
  * urllib 自动识别 http_proxy / https_proxy 环境变量，校园网代理环境无需额外处理
  * 不做 ssl 免校验 —— 不为了"能跑"而关掉证书检查
"""

import json
import urllib.error
import urllib.request

MAX_BYTES = 256 * 1024
_LAST_ERROR = None


def last_error():
    """最近一次失败原因（给上层写进 llm.note，用于如实交代）。"""
    return _LAST_ERROR


def chat(cfg, system, user, max_tokens=None, temperature=None):
    """调一次 OpenAI 兼容的 chat/completions。

    返回 (content_str, None) 或 (None, 错误原因字符串)。**不抛异常。**
    """
    global _LAST_ERROR
    _LAST_ERROR = None

    if cfg is None or not getattr(cfg, "usable", False):
        return None, "大模型未配置或配置不完整"

    try:
        payload = {
            "model": cfg.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": cfg.temperature if temperature is None else temperature,
            "max_tokens": int(max_tokens or cfg.max_tokens),
            "stream": False,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            cfg.endpoint(),
            data=data,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": "Bearer " + cfg.api_key,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=cfg.timeout_sec) as resp:
            raw = resp.read(MAX_BYTES + 1)

        if len(raw) > MAX_BYTES:
            return None, "响应超过 %d 字节，已丢弃" % MAX_BYTES

        body = json.loads(raw.decode("utf-8", errors="replace"))
        choices = body.get("choices") or []
        if not choices:
            return None, "返回了空的 choices"
        msg = (choices[0] or {}).get("message") or {}
        content = msg.get("content")
        if not isinstance(content, str) or not content.strip():
            return None, "返回的 content 为空"
        return content.strip(), None

    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read(300).decode("utf-8", errors="replace")
        except Exception:
            pass
        _LAST_ERROR = "HTTP %s：%s" % (exc.code, detail or exc.reason)
        return None, _LAST_ERROR
    except urllib.error.URLError as exc:
        _LAST_ERROR = "网络不可达：%s" % (exc.reason,)
        return None, _LAST_ERROR
    except Exception as exc:
        _LAST_ERROR = "调用异常：%s" % (exc,)
        return None, _LAST_ERROR
