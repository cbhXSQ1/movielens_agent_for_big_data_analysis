# -*- coding: utf-8 -*-
import http.client
import json
import sys
import threading
from pathlib import Path
from urllib.parse import quote

import pytest

AGENT_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = AGENT_DIR.parent
sys.path.insert(0, str(REPO_ROOT))

from agent import http_api, llm_providers  # noqa: E402


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "config" / "llm.providers.json"
    monkeypatch.setattr(llm_providers, "PROVIDERS_PATH", path)
    return path


def save(name="work", base="https://api.example.com/v1", key="sk-secret",
         model="model-a", mode="llm"):
    return llm_providers.save_provider(name, base, key, model, mode)


def test_missing_file_lists_empty(store):
    assert llm_providers.list_providers() == {"ok": True, "providers": []}


def test_save_and_list_roundtrip_persists_file_shape(store):
    result = save()
    assert result["ok"] is True
    provider = result["provider"]
    assert provider["name"] == "work"
    assert provider["api_base"] == "https://api.example.com/v1"
    assert provider["api_key"] == "sk-secret"
    assert provider["model"] == "model-a"
    assert provider["mode"] == "llm"
    assert isinstance(provider["updated_at"], str) and provider["updated_at"]

    listed = llm_providers.list_providers()
    assert listed["ok"] is True
    assert listed["providers"] == [provider]
    on_disk = json.loads(store.read_text(encoding="utf-8"))
    assert on_disk == {"providers": {"work": {
        key: value for key, value in provider.items() if key != "name"}}}


def test_save_same_name_overwrites(store):
    save(key="sk-first")
    first = llm_providers.list_providers()["providers"][0]
    on_disk = json.loads(store.read_text(encoding="utf-8"))
    on_disk["providers"]["work"]["updated_at"] = "2000-01-01T00:00:00+00:00"
    store.write_text(json.dumps(on_disk), encoding="utf-8")

    result = save(key="sk-second", model="model-b")
    assert result["ok"] is True
    listed = llm_providers.list_providers()["providers"]
    assert len(listed) == 1
    assert listed[0]["api_key"] == "sk-second"
    assert listed[0]["model"] == "model-b"
    assert listed[0]["updated_at"] != "2000-01-01T00:00:00+00:00"
    assert first["updated_at"] != listed[0]["updated_at"]


def test_list_sorted_by_name(store):
    save(name="zeta")
    save(name="alpha")
    assert [p["name"] for p in llm_providers.list_providers()["providers"]] == [
        "alpha", "zeta"]


def test_delete_removes_only_named_provider(store):
    save(name="alpha")
    save(name="beta")
    assert llm_providers.delete_provider("alpha") == {
        "ok": True, "name": "alpha"}
    assert [p["name"] for p in llm_providers.list_providers()["providers"]] == [
        "beta"]
    on_disk = json.loads(store.read_text(encoding="utf-8"))
    assert list(on_disk["providers"]) == ["beta"]


def test_delete_missing_provider_reports_file_not_found(store):
    result = llm_providers.delete_provider("missing")
    assert result["ok"] is False
    assert result["error"]["code"] == "FILE_NOT_FOUND"


@pytest.mark.parametrize("name", ["", "   ", "a/b", "a\\b", "..", "../x", "a..b"])
def test_save_rejects_invalid_name(store, name):
    result = save(name=name)
    assert result["ok"] is False
    assert result["error"]["code"] == "USAGE"
    assert not store.exists()


@pytest.mark.parametrize("name", ["", "a/b", "a\\b", ".."])
def test_delete_rejects_invalid_name(store, name):
    result = llm_providers.delete_provider(name)
    assert result["ok"] is False
    assert result["error"]["code"] == "USAGE"


@pytest.mark.parametrize("base,key,model", [
    ("", "sk", "m"), ("   ", "sk", "m"),
    ("https://x", "", "m"), ("https://x", "   ", "m"),
    ("https://x", "sk", ""), ("https://x", "sk", "   ")])
def test_save_rejects_empty_required_fields(store, base, key, model):
    result = save(base=base, key=key, model=model)
    assert result["ok"] is False
    assert result["error"]["code"] == "USAGE"
    assert not store.exists()


def test_corrupt_file_lists_empty_and_can_be_overwritten(store):
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text("{not json", encoding="utf-8")
    assert llm_providers.list_providers() == {"ok": True, "providers": []}
    assert save(name="fresh")["ok"] is True
    assert [p["name"] for p in llm_providers.list_providers()["providers"]] == [
        "fresh"]


@pytest.mark.parametrize("payload", [
    [], "text", {"providers": []}, {"providers": {"bad": "text"}}])
def test_unexpected_file_shapes_are_treated_as_empty(store, payload):
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps(payload), encoding="utf-8")
    assert llm_providers.list_providers() == {"ok": True, "providers": []}


def test_io_error_does_not_echo_api_key(store, monkeypatch):
    def fail_replace(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(llm_providers.os, "replace", fail_replace)
    result = save(key="sk-super-secret")
    assert result["ok"] is False
    assert result["error"]["code"] == "IO_ERROR"
    assert "sk-super-secret" not in json.dumps(result)


@pytest.fixture
def server(store):
    srv = http_api.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()


def request(port, method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    try:
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8")
        return resp.status, json.loads(raw) if raw else None, raw
    finally:
        conn.close()


def test_http_crud_roundtrip(server):
    body = {"name": "我的模型", "api_base": "https://api.example.com/v1",
            "api_key": "sk-http", "model": "model-a"}
    status, saved, _ = request(server, "POST", "/api/llm/providers", body)
    assert status == 200
    assert saved["ok"] is True
    assert saved["provider"]["name"] == "我的模型"
    assert saved["provider"]["mode"] == "llm"

    status, listed, _ = request(server, "GET", "/api/llm/providers")
    assert status == 200
    assert listed["providers"] == [saved["provider"]]

    status, overwritten, _ = request(
        server, "POST", "/api/llm/providers",
        dict(body, api_key="sk-new", model="model-b", mode="provider"))
    assert status == 200
    assert overwritten["provider"]["api_key"] == "sk-new"
    assert overwritten["provider"]["model"] == "model-b"
    assert overwritten["provider"]["mode"] == "provider"

    status, listed, _ = request(server, "GET", "/api/llm/providers")
    assert len(listed["providers"]) == 1
    assert listed["providers"][0]["api_key"] == "sk-new"

    status, deleted, _ = request(
        server, "DELETE", "/api/llm/providers/%s" % quote("我的模型"))
    assert status == 200
    assert deleted == {"ok": True, "name": "我的模型"}

    status, listed, _ = request(server, "GET", "/api/llm/providers")
    assert listed == {"ok": True, "providers": []}


def test_http_usage_error_returns_400(server):
    status, payload, _ = request(
        server, "POST", "/api/llm/providers",
        {"name": "", "api_base": "https://x", "api_key": "sk", "model": "m"})
    assert status == 400
    assert payload["ok"] is False
    assert payload["error"]["code"] == "USAGE"

    status, payload, _ = request(
        server, "DELETE", "/api/llm/providers/%s" % quote("../escape"))
    assert status == 400
    assert payload["error"]["code"] == "USAGE"


def test_http_delete_missing_returns_404(server):
    status, payload, _ = request(
        server, "DELETE", "/api/llm/providers/missing")
    assert status == 404
    assert payload["ok"] is False
    assert payload["error"]["code"] == "FILE_NOT_FOUND"


def test_http_error_does_not_echo_api_key(server):
    status, payload, raw = request(
        server, "POST", "/api/llm/providers",
        {"name": "work", "api_base": "https://x",
         "api_key": "sk-never-echo", "model": ""})
    assert status == 400
    assert payload["error"]["code"] == "USAGE"
    assert "sk-never-echo" not in raw
