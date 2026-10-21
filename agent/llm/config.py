# -*- coding: utf-8 -*-
"""阶段 1 模型接入层：配置读取与脱敏（T1.5）。

在现有 `agent/llm_config.py` 之上**扩展**，不重写、不改动它：
  * 复用 LLMConfig 的三级合并（文件 → 环境变量 → 请求覆盖）与 safe_dict 脱敏；
  * 追加供应商相关字段：provider（openai / opencode）、session_key。

红线：
  * 密钥不落盘（本模块只读不写）、不回显（describe() 不含 api_key）；
  * session_key 不是密钥，但 describe() 也只报「有没有」，不报值；
  * 任何异常都退化成「不可用」，绝不半吊子跑起来（与 llm_config 同约定）。
"""

import json
import os

from .. import llm_config

PROVIDER_OPENAI = "openai"
PROVIDER_OPENCODE = "opencode"

#: 扩展字段的环境变量名（与 llm_config.ENV 并列；后者管基础字段）
EXTRA_ENV = {
    "provider": "AGENT_LLM_PROVIDER",
    "session_key": "AGENT_LLM_SESSION_KEY",
}
EXTRA_KEYS = ("provider", "session_key")


def _read_file():
    """读 config/llm.settings.json（路径复用 llm_config.CONFIG_PATH，可被测试替换）。"""
    path = llm_config.CONFIG_PATH
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


class ModelConfig(object):
    """基础 LLM 配置 + 供应商扩展字段。"""

    def __init__(self, base, raw=None):
        d = dict(raw or {})
        self.base = base
        self.provider = (d.get("provider") or PROVIDER_OPENAI).strip().lower()
        self.session_key = (d.get("session_key") or "").strip()

    @property
    def usable(self):
        """五项齐全（总开关 + enabled + base + key + model）才算可用。"""
        return bool(self.base.usable)

    def endpoint(self):
        return self.base.endpoint()

    def describe(self):
        """脱敏视图：**不含 api_key**；session_key 只报有没有，不报值。"""
        view = self.base.safe_dict()
        view["provider"] = self.provider
        view["session_key_set"] = bool(self.session_key)
        return view


def load(overrides=None):
    """三级合并并返回 ModelConfig。任何异常都返回不可用配置，不抛出。"""
    try:
        base = llm_config.load(overrides)
        raw = {}
        file_data = _read_file()
        for key in EXTRA_KEYS:
            if file_data.get(key) not in (None, ""):
                raw[key] = file_data[key]
        for key, name in EXTRA_ENV.items():
            val = os.environ.get(name)
            if val not in (None, ""):
                raw[key] = val
        if isinstance(overrides, dict):
            for key in EXTRA_KEYS:
                if overrides.get(key) is not None:
                    raw[key] = overrides[key]
        return ModelConfig(base, raw)
    except Exception:
        return ModelConfig(llm_config.LLMConfig({"enabled": False}), {})
