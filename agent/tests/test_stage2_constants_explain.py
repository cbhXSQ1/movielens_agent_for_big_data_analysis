# -*- coding: utf-8 -*-
"""阶段 2（ag-tools）· T2.5 常量疏散 + T2.6 解释可流式。

T2.5：常量有中立产地（constants.py）；纯规则意图解析已删除，新模块只认常量家。
T2.6：中文解释能以分块/事件形式产出，且不破坏一次性调用方式。
"""
import os
import subprocess
import sys
import unittest

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent import constants, explain  # noqa: E402


class TestConstantsEvacuation(unittest.TestCase):
    def test_task_id_regex(self):
        m = constants.TASK_ID_RE.search("任务 20260925-184303-379a4b 好了")
        self.assertIsNotNone(m)
        self.assertEqual("20260925-184303-379a4b", m.group(0))

    def test_new_modules_import_cleanly(self):
        """在干净子进程里导入新模块，断言它们可独立导入、不依赖任何旧入口。"""
        code = (
            "import agent.constants, agent.number_guard, agent.protocol, "
            "agent.tool_facts"
        )
        proc = subprocess.run([sys.executable, "-c", code],
                              cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(0, proc.returncode,
                         "新模块应可独立导入：%s" % (proc.stderr,))


class TestExplainChunking(unittest.TestCase):
    def test_chunk_text_reassembles(self):
        text = "\n".join("第 %d 行内容示例，包含一些数字 %d。" % (i, i * 7)
                         for i in range(1, 40))
        chunks = list(explain.chunk_text(text, size=60))
        self.assertGreater(len(chunks), 1)
        self.assertEqual(text, "".join(chunks))

    def test_chunk_text_empty_and_small(self):
        self.assertEqual([], list(explain.chunk_text("")))
        self.assertEqual(["短句"], list(explain.chunk_text("短句", size=100)))

    def test_iter_deltas_marks_last(self):
        events = list(explain.iter_deltas("abcde" * 100, size=50, node_id="n1"))
        self.assertTrue(all(e["event"] == "message_delta" for e in events))
        self.assertEqual("abcde" * 100, "".join(e["text"] for e in events))
        self.assertFalse(events[0]["final"])
        self.assertTrue(events[-1]["final"])
        self.assertEqual("n1", events[0]["node_id"])

    def test_stream_kind_matches_oneshot(self):
        envelope = {"ok": True, "type": "quarantine", "table": "ratings",
                    "total_available": 3,
                    "samples": [{"line_no": 1, "rule_id": "R1",
                                 "reason": "坏", "raw_line": "x"}]}
        one_shot = explain.explain_samples(envelope)
        streamed = "".join(e["text"] for e in
                           explain.stream_kind("samples", envelope, size=20))
        self.assertEqual(one_shot, streamed)

    def test_unknown_kind_falls_back_to_error(self):
        events = list(explain.stream_kind("nope", {"ok": False}))
        self.assertTrue(events)
        self.assertIn("失败", "".join(e["text"] for e in events))

    def test_oneshot_functions_unchanged(self):
        envelope = {"ok": False, "error": {"code": "USAGE", "message": "缺参数"}}
        self.assertIsInstance(explain.explain_error(envelope), str)
        self.assertIsInstance(explain.explain_status(envelope), str)


if __name__ == "__main__":
    unittest.main()
