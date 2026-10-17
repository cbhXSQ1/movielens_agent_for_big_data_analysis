# -*- coding: utf-8 -*-
"""状态五态含「已中止」：解释层中文映射 + 等待终态集合。

覆盖：
  * `explain.status_label("aborted") == "已中止"`，且状态说明文本含「已中止」；
  * `tools.run_and_wait` 的等待终态含 aborted：一遇中止立即返回，不再空转到超时。
"""
import os
import sys

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent import explain  # noqa: E402
from agent import tools  # noqa: E402


def test_explain_maps_aborted():
    assert explain.status_label("aborted") == "已中止"


def test_explain_status_text_shows_aborted():
    text = explain.explain_status({"ok": True, "task_id": "T1",
                                   "status": "aborted"})
    assert "已中止" in text


def test_run_and_wait_stops_on_aborted(monkeypatch):
    monkeypatch.setattr(tools, "start_cleaning_task",
                        lambda **kw: {"ok": True, "task_id": "T1"})
    calls = []

    def fake_status(tid):
        calls.append(tid)
        return {"ok": True, "task_id": tid, "status": "aborted"}

    monkeypatch.setattr(tools, "get_task_status", fake_status)
    out = tools.run_and_wait(poll_seconds=0.001, timeout_seconds=0.2,
                             scope="sample")
    assert out.get("status") == "aborted"
    assert calls == ["T1"]
