# -*- coding: utf-8 -*-
"""命名的大模型供应商档案，长期保存在本机 `config/llm.providers.json`。

文件形状：

    {"providers": {"<name>": {"api_base": ..., "api_key": ..., "model": ...,
                              "mode": ..., "updated_at": ...}}}

同名保存即覆盖；写入用临时文件 + `os.replace` 原子替换。读取遇到缺失 / 损坏的
文件一律当作空档案，绝不把设置页面弄崩。API Key 会随档案读回供设置面板展示，
但任何错误信息都不包含它。
"""

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

PROVIDERS_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "llm.providers.json")

_FIELDS = ("api_base", "api_key", "model", "mode", "updated_at")
_LOCK = threading.RLock()


def _usage(message):
    return {"ok": False, "error": {"code": "USAGE", "message": message}}


def _io_error():
    return {"ok": False, "error": {
        "code": "IO_ERROR", "message": "供应商档案写入失败"}}


def _clean_name(name):
    if not isinstance(name, str):
        return None
    value = name.strip()
    if not value or value in (".", ".."):
        return None
    if "/" in value or "\\" in value or ".." in value:
        return None
    return value


def _path():
    return Path(os.fspath(PROVIDERS_PATH))


def _read_providers():
    try:
        with _path().open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    providers = data.get("providers")
    if not isinstance(providers, dict):
        return {}
    return {name: row for name, row in providers.items()
            if isinstance(name, str) and isinstance(row, dict)}


def _provider_view(name, row):
    view = {"name": name}
    for field in _FIELDS:
        value = row.get(field)
        view[field] = value if isinstance(value, str) else (
            "" if value is None else str(value))
    return view


def _write_providers(providers):
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".llm.providers.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"providers": providers}, fh, ensure_ascii=False,
                      indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
        tmp_name = None
    finally:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def list_providers():
    with _LOCK:
        providers = _read_providers()
    return {"ok": True,
            "providers": [_provider_view(name, providers[name])
                          for name in sorted(providers)]}


def save_provider(name, api_base, api_key, model, mode="llm"):
    clean_name = _clean_name(name)
    if clean_name is None:
        return _usage("供应商名称不能为空，且必须是单段名称（不能含 /、\\ 或 ..）")
    for label, value in (("api_base", api_base), ("api_key", api_key),
                         ("model", model)):
        if not isinstance(value, str) or not value.strip():
            return _usage("%s 不能为空" % label)
    if mode is None or mode == "":
        mode = "llm"
    elif not isinstance(mode, str) or not mode.strip():
        return _usage("mode 不能为空")
    else:
        mode = mode.strip()

    row = {
        "api_base": api_base,
        "api_key": api_key,
        "model": model,
        "mode": mode,
        "updated_at": datetime.now(timezone.utc).isoformat(
            timespec="microseconds"),
    }
    with _LOCK:
        providers = _read_providers()
        providers[clean_name] = row
        try:
            _write_providers(providers)
        except (OSError, TypeError, ValueError):
            return _io_error()
    return {"ok": True, "provider": _provider_view(clean_name, row)}


def delete_provider(name):
    clean_name = _clean_name(name)
    if clean_name is None:
        return _usage("供应商名称不能为空，且必须是单段名称（不能含 /、\\ 或 ..）")
    with _LOCK:
        providers = _read_providers()
        if clean_name not in providers:
            return {"ok": False, "error": {
                "code": "FILE_NOT_FOUND",
                "message": "供应商档案不存在"}}
        del providers[clean_name]
        try:
            _write_providers(providers)
        except (OSError, TypeError, ValueError):
            return _io_error()
    return {"ok": True, "name": clean_name}
