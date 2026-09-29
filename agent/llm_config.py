# -*- coding: utf-8 -*-
"""第 5 节 Agent：大模型可选层 —— 配置。

红线落实：
  * 默认关闭：enabled 不为真 / base、key、model 任一为空 ⇒ 视为不存在
  * 零第三方依赖：只用标准库
  * 配置只读：本模块不写任何文件，也不改 config/ 下的清洗与评分方案
  * 绝不"看起来能用"：任何异常都返回不可用，由调用方回落到规则解析

配置来源（后者覆盖前者）：
  1) config/llm.settings.json（可选；已加入 .gitignore，不建议入库）
  2) 环境变量 AGENT_LLM_*
  3) 单次请求的 body.llm（前端设置面板传入，优先级最高，不落盘）
"""

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(os.path.dirname(_HERE), "config", "llm.settings.json")

TRUE_VALUES = ("1", "true", "yes", "on")
DEFAULT_TIMEOUT = 3.0        # 单次调用默认上限（秒）
MAX_TIMEOUT = 5.0            # 硬上限，配得再大也压到这里
DEFAULT_MAX_TOKENS = 400

ENV = {
    # 总开关：决定「这个后端到底有没有大模型能力」。
    #   不开 ⇒ /health 里 llm.supported=false ⇒ 前端把设置入口整块隐藏，
    #   「不配置则完全不存在」这条承诺才成立（否则入口一直在，只是没配）。
    # 注意：这是**后端侧开关**，前端面板不能开它（load() 会剔除请求里传的 supported）。
    "supported":   "AGENT_LLM_SUPPORTED",
    "enabled":     "AGENT_LLM_ENABLED",
    "api_base":    "AGENT_LLM_API_BASE",
    "api_key":     "AGENT_LLM_API_KEY",
    "model":       "AGENT_LLM_MODEL",
    "mode":        "AGENT_LLM_MODE",
    "use_for":     "AGENT_LLM_USE_FOR",
    "timeout_sec": "AGENT_LLM_TIMEOUT",
    "max_tokens":  "AGENT_LLM_MAX_TOKENS",
    "temperature": "AGENT_LLM_TEMPERATURE",
}


def _as_bool(v):
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    return str(v).strip().lower() in TRUE_VALUES


def _as_float(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _as_int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


class LLMConfig(object):
    """一份已合并完毕的大模型配置。"""

    def __init__(self, raw=None):
        d = dict(raw or {})
        # 总开关默认**关**：不开就当作这个后端根本没有大模型能力
        self.supported = _as_bool(d.get("supported", False))
        self.enabled = _as_bool(d.get("enabled", False))
        self.api_base = (d.get("api_base") or "").strip().rstrip("/")
        self.api_key = (d.get("api_key") or "").strip()
        self.model = (d.get("model") or "").strip()
        # fallback：只在规则没把握时才问它（默认，规则仍是主路径）
        # always  ：每句都问它（现场演示大模型能力时用）
        # 其它值  ：当作全关
        self.mode = (d.get("mode") or "fallback").strip().lower()
        use_for = d.get("use_for") or ("intent", "polish")
        if isinstance(use_for, str):
            use_for = [x.strip() for x in use_for.split(",") if x.strip()]
        self.use_for = tuple(use_for)
        self.timeout_sec = min(_as_float(d.get("timeout_sec"), DEFAULT_TIMEOUT), MAX_TIMEOUT)
        self.max_tokens = _as_int(d.get("max_tokens"), DEFAULT_MAX_TOKENS)
        self.temperature = _as_float(d.get("temperature"), 0.0)
        self.source = d.get("_source", "default")     # 仅用于排错

    @property
    def usable(self):
        """**五项齐全**才算可用：总开关 + enabled + base + key + model。

        缺任何一项都当作"不存在"，不会半吊子跑起来。
        """
        return bool(self.supported and self.enabled
                    and self.api_base and self.api_key and self.model)

    def endpoint(self):
        """拼出 /v1/chat/completions。用户填到 …/v1 或 …/v1/ 都能对。"""
        base = self.api_base
        if base.endswith("/v1"):
            return base + "/chat/completions"
        return base + "/v1/chat/completions"

    def safe_dict(self):
        """给前端 /health 与 /api/llm/test 看的脱敏视图：**不含 api_key**。"""
        return {
            # supported = 后端有没有这个能力（总开关）；configured = 当前配好没有
            "supported": bool(self.supported),
            "configured": self.usable,
            "enabled": bool(self.enabled),
            "model": self.model or None,
            "api_base": self.api_base or None,
            "mode": self.mode,
            "use_for": list(self.use_for),
            "timeout_sec": self.timeout_sec,
        }


def _read_file():
    if not os.path.isfile(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (ValueError, OSError):
        return {}


def load(overrides=None):
    """合并三处配置并返回 LLMConfig。**任何异常都不抛出**，最坏返回"不可用"配置。"""
    try:
        raw = {}
        raw.update(_read_file())
        for key, name in ENV.items():
            val = os.environ.get(name)
            if val not in (None, ""):
                raw[key] = val
        if isinstance(overrides, dict):
            # supported 是**后端总开关**，不允许前端请求打开（否则"默认关闭"形同虚设）；
            # mode 允许前端传（设置面板上的下拉）。
            ov = dict(overrides)
            ov.pop("supported", None)
            raw.update(ov)
            raw["_source"] = "request"
        elif raw:
            raw["_source"] = "env/file"
        return LLMConfig(raw)
    except Exception:
        return LLMConfig({"enabled": False})
