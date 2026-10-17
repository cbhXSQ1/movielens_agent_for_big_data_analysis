# -*- coding: utf-8 -*-
"""tests/test_mcp_client_cli.py —— 给对端的客户端示例：命令行解析的回归测试。

为什么单独给这个文件写测试：客户端是**对端的对接样板**，它的参数解析出错时会表现成
"调用成功、但服务端说缺参数"——参数被静默丢掉，比直接报错难查得多（实现期实测踩到过：
为下载动作新增第二个位置参数后，调用动作的第一对"键=值"被那个位置参数吃掉）。
这条路径当时没有任何测试覆盖，所以现在把它锁住。

只测解析与分派，不连网、不需要官方协议包（客户端里对协议包的导入放在函数内部）。
"""

import contextlib
import importlib.util
import io
import os
import sys
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CLIENT_PATH = os.path.join(_REPO_ROOT, "hadoop", "tools", "mcp_client.py")


def _load_client():
    """按文件路径加载客户端模块（tools 目录不是包，用路径加载最省事）。"""
    spec = importlib.util.spec_from_file_location("mcp_client_under_test", _CLIENT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


client = _load_client()


def parse(argv):
    """走与 main() 相同的归一化路径，返回解析结果（不执行动作）。"""
    return client._normalize_positionals(client.build_parser().parse_args(argv))


class CallActionTest(unittest.TestCase):
    """调用动作：位置参数是"能力名 + 若干 键=值"。"""

    def test_single_pair_reaches_kv(self):
        args = parse(["call", "get_task_status", "task_id=abc"])
        self.assertEqual(args.target, "get_task_status")
        self.assertEqual(args.kv, ["task_id=abc"])
        self.assertIsNone(args.target2)

    def test_multiple_pairs_all_reach_kv(self):
        args = parse(["call", "get_samples", "task_id=t1", "table=movies", "limit=3"])
        self.assertEqual(args.target, "get_samples")
        self.assertEqual(args.kv, ["task_id=t1", "table=movies", "limit=3"])

    def test_no_pairs_is_allowed(self):
        args = parse(["call", "list_tasks"])
        self.assertEqual(args.target, "list_tasks")
        self.assertEqual(args.kv, [])

    def test_value_may_contain_equals_sign(self):
        # 键=值 只按第一个等号切分，值里的等号要原样保留
        args = parse(["call", "read_task_file", "task_id=t1", "path=a=b.json"])
        arguments = {}
        for pair in args.kv:
            key, value = pair.split("=", 1)
            arguments[key] = value
        self.assertEqual(arguments["path"], "a=b.json")

    def test_options_before_action_still_work(self):
        args = parse(["--url", "http://x/mcp", "--token", "t", "call",
                      "get_task_status", "task_id=abc"])
        self.assertEqual(args.url, "http://x/mcp")
        self.assertEqual(args.kv, ["task_id=abc"])


class DownloadActionTest(unittest.TestCase):
    """下载动作：位置参数是"目标 + 路径/表名"，两段都不能被当成调用参数。"""

    def test_download_task_takes_two_positionals(self):
        args = parse(["download-task", "20261017-201144-2e9153",
                      "cleaned/ml1m-clean-v1/movies.dat", "--out", "/tmp/x.dat"])
        self.assertEqual(args.target, "20261017-201144-2e9153")
        self.assertEqual(args.target2, "cleaned/ml1m-clean-v1/movies.dat")
        self.assertEqual(args.kv, [])
        self.assertEqual(args.out, "/tmp/x.dat")

    def test_download_published_takes_version_and_table(self):
        args = parse(["download-published", "ml1m-clean-v1", "movies"])
        self.assertEqual(args.target, "ml1m-clean-v1")
        self.assertEqual(args.target2, "movies")
        self.assertEqual(args.kv, [])

    def test_missing_second_positional_is_none_not_empty_string(self):
        args = parse(["download-published", "ml1m-clean-v1"])
        self.assertIsNone(args.target2)

    def test_extra_positionals_after_second_are_dropped_not_misparsed(self):
        args = parse(["download-task", "tid", "path", "stray=1"])
        self.assertEqual(args.target2, "path")
        self.assertEqual(args.kv, ["stray=1"])


class OtherActionsTest(unittest.TestCase):
    def test_default_action_is_list(self):
        args = parse([])
        self.assertEqual(args.action, "list")
        self.assertEqual(args.kv, [])

    def test_read_takes_uri(self):
        args = parse(["read", "ml://task/t1/status"])
        self.assertEqual(args.target, "ml://task/t1/status")
        self.assertEqual(args.kv, [])

    def test_wait_takes_task_id(self):
        args = parse(["wait", "t1"])
        self.assertEqual(args.target, "t1")

    def test_describe_has_no_positional_requirement(self):
        args = parse(["describe"])
        self.assertEqual(args.action, "describe")

    def test_call_without_target_is_rejected(self):
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            rc = client.main(["call"])
        self.assertEqual(rc, 2)
        self.assertIn("需要给出", captured.getvalue())

    def test_unknown_action_is_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                client.build_parser().parse_args(["nonsense"])


class KVPairShapeTest(unittest.TestCase):
    """没有等号的参数必须明确报错，不能让服务端去猜。"""

    def test_pair_without_equals_is_rejected(self):
        args = parse(["call", "get_task_status", "task_id"])
        with self.assertRaises(SystemExit):
            client.action_call(args)


if __name__ == "__main__":
    unittest.main()
