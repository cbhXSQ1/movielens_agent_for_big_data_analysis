# -*- coding: utf-8 -*-
"""tests/test_mcp_contract.py —— 契约层测试（工具表、错误映射、信封、参数校验）。

覆盖三件必须锁死的事：
  1. **工具表不许漂移**：二十项能力齐全、字段完整、枚举取值与 driver 实现一致；
  2. **错误语义无损**：已带结构化码的原样保留，只有退出码可依时才换算并留档；
  3. **参数只有一套校验**：缺省补值、越界拒绝、未知参数拒绝、跨参数条件必填。

本文件只用标准库与仓库内模块 —— 契约层要能在只有系统自带解释器的环境里被验证
（官方协议包只在服务层用到，且必须留在虚拟环境里）。
"""

import os
import subprocess
import sys
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_HADOOP_DIR = os.path.join(_REPO_ROOT, "hadoop")
for _p in (_HADOOP_DIR, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mcpserver import contract, dispatch, tools                    # noqa: E402
from mcpserver.contract import valid_task_id                      # noqa: E402
from mcpserver.contract import StructuredError                    # noqa: E402
from driver import run_task as rt                                 # noqa: E402
from driver.commands import SamplesCommand, ScoreCommand, StartCommand  # noqa: E402


#: 方案文档 §3 列出的二十项能力（顺序即对外展示顺序）。
EXPECTED_TOOLS = (
    "validate_config", "list_schemes", "start_cleaning_task", "start_clean_task",
    "score_task", "get_task_status", "get_task_result", "get_samples", "get_report",
    "list_tasks", "list_published_versions", "list_task_files", "read_task_file",
    "get_job_counters", "get_job_logs", "list_raw_datasets", "upload_scheme",
    "delete_scheme",
    "get_scheme_content",
    "train_classifier", "cluster_users", "reduce_dimensions",
    "predict_high_rating", "list_ml_models", "get_ml_model",
    "describe_capabilities", "wait_for_task",
    "cancel_task", "get_scheduler_state", "health_check",
)


class ToolTableTest(unittest.TestCase):
    """工具表本身的自洽性，以及与 driver 实现的取值一致性。"""

    def test_tool_count_and_order(self):
        self.assertEqual(tools.tool_names(), EXPECTED_TOOLS)

    def test_names_unique(self):
        names = [t["name"] for t in tools.TOOLS]
        self.assertEqual(len(names), len(set(names)))

    def test_every_tool_has_required_fields(self):
        for tool in tools.TOOLS:
            for field in tools.REQUIRED_TOOL_FIELDS:
                self.assertIn(field, tool, "%s 缺字段 %s" % (tool["name"], field))
            self.assertIsInstance(tool["read_only"], bool, tool["name"])
            self.assertIsInstance(tool["long_running"], bool, tool["name"])
            self.assertIsInstance(tool["title"], str)
            self.assertTrue(tool["title"].strip())

    def test_long_running_tools_give_estimate(self):
        for tool in tools.TOOLS:
            self.assertGreaterEqual(tool["estimated_seconds"], 0, tool["name"])
            if tool["long_running"]:
                self.assertGreater(tool["estimated_seconds"], 0, tool["name"])

    def test_param_names_unique_per_tool(self):
        for tool in tools.TOOLS:
            names = [p["name"] for p in tool["params"]]
            self.assertEqual(len(names), len(set(names)), tool["name"])

    def test_enum_params_carry_values(self):
        for tool in tools.TOOLS:
            for param in tool["params"]:
                if param["type"] == "enum":
                    self.assertTrue(param.get("values"), "%s.%s" % (tool["name"], param["name"]))
                    if param.get("default") is not None:
                        self.assertIn(param["default"], param["values"],
                                      "%s.%s 的默认值必须在取值范围内"
                                      % (tool["name"], param["name"]))
                if param["type"] == "int":
                    # 可选的整型参数可以不写默认值（例如簇数、目标维度：由端侧自选）
                    default = param.get("default")
                    self.assertTrue(default is None or isinstance(default, int),
                                    "%s.%s 的默认值应为整数或缺省" % (tool["name"], param["name"]))

    # ---- 与 driver 的取值逐项比对（防止两边漂移）----

    @staticmethod
    def _param(tool_name, param_name):
        for param in tools.get_tool(tool_name)["params"]:
            if param["name"] == param_name:
                return param
        raise AssertionError("%s 没有参数 %s" % (tool_name, param_name))

    def test_scope_values_match_driver(self):
        expected = StartCommand.spec["scope"]["values"]
        self.assertEqual(self._param("start_cleaning_task", "scope")["values"], expected)
        self.assertEqual(self._param("start_clean_task", "scope")["values"], expected)
        self.assertEqual(self._param("start_cleaning_task", "scope")["default"],
                         StartCommand.spec["scope"]["default"])

    def test_task_type_values_match_driver(self):
        # 两个发起工具各自固定一种任务类型，取值集合必须落在 driver 的枚举里
        driver_values = StartCommand.spec["task-type"]["values"]
        self.assertEqual(set(driver_values), set(rt.TASK_TYPES) - {"score"})

    def test_sample_type_and_table_values_match_driver(self):
        # 取值集合必须一致（顺序不同不算漂移：本端把常用项排前面）
        self.assertEqual(sorted(self._param("get_samples", "sample_type")["values"]),
                         sorted(SamplesCommand.spec["type"]["values"]))
        self.assertEqual(sorted(self._param("get_samples", "table")["values"]),
                         sorted(SamplesCommand.spec["table"]["values"]))

    def test_score_source_values_match_driver(self):
        self.assertEqual(self._param("score_task", "source")["values"],
                         ScoreCommand.spec["source"]["values"])

    def test_report_format_values(self):
        # driver 的 report 命令在命令体内校验格式，取值为 md / json
        self.assertEqual(self._param("get_report", "format")["values"], ("md", "json"))

    def test_limits_within_driver_constraints(self):
        # driver 对 samples 的条数只要求非负；本端另加上限，属于在 driver 之上的加性收紧
        n = self._param("get_samples", "limit")
        self.assertEqual(n["minimum"], SamplesCommand.spec["n"]["min"])


class ContractErrorTest(unittest.TestCase):
    """错误码与退出码映射：语义无损。"""

    def test_exit_codes_match_driver(self):
        self.assertEqual(contract.EXIT_OK, rt.EXIT_OK)
        self.assertEqual(contract.EXIT_USAGE, rt.EXIT_USAGE)
        self.assertEqual(contract.EXIT_FAILED, rt.EXIT_FAILED)
        self.assertEqual(contract.EXIT_NOT_FOUND, rt.EXIT_NOT_FOUND)
        self.assertEqual(contract.EXIT_NOT_FINISHED, rt.EXIT_NOT_FINISHED)
        self.assertEqual(contract.EXIT_CONFLICT, rt.EXIT_CONFLICT)

    def test_every_driver_error_code_is_known_here(self):
        # driver 自产的结构化码必须全部被本端认识（否则会被误判成未知码）
        for code in rt.ERROR_CODES:
            self.assertIn(code, contract.STRUCTURED_ERROR_CODES, code)

    def test_native_codes_survive_normalization(self):
        for code in (contract.CODE_CONFIG_INVALID, contract.CODE_TASK_ALREADY_RUNNING,
                     contract.CODE_DEPENDENCY_MISSING):
            out = contract.normalize_error({"code": code, "message": "m"}, 2)
            self.assertEqual(out["code"], code, "原生码被降级了：%s" % code)
            self.assertEqual(out["driver_code"], 2)
            self.assertEqual(out["message"], "m")

    def test_exit_only_maps_and_keeps_driver_code(self):
        cases = {
            3: contract.CODE_TASK_FAILED,
            4: contract.CODE_TASK_NOT_FOUND,
            5: contract.CODE_TASK_NOT_FINISHED,
            6: contract.CODE_VERSION_CONFLICT,
            2: contract.CODE_USAGE,
        }
        for exit_code, expected in cases.items():
            out = contract.normalize_error({"message": "x"}, exit_code)
            self.assertEqual(out["code"], expected)
            self.assertEqual(out["driver_code"], exit_code)

    def test_success_has_no_code(self):
        self.assertIsNone(contract.code_for_exit(0))
        self.assertIsNone(contract.code_for_exit(None))

    def test_unknown_code_preserved(self):
        out = contract.normalize_error({"code": "SOMETHING_NEW"}, None)
        self.assertEqual(out["code"], "SOMETHING_NEW")

    def test_missing_code_falls_back_to_bad_output(self):
        out = contract.normalize_error({"message": "x"}, None)
        self.assertEqual(out["code"], contract.CODE_DRIVER_BAD_OUTPUT)
        self.assertEqual(out["message"], "x")

    def test_non_dict_error_keeps_message(self):
        out = contract.normalize_error("炸了", 3)
        self.assertEqual(out["code"], contract.CODE_TASK_FAILED)
        self.assertEqual(out["message"], "炸了")

    def test_transport_code_mapping_is_identity(self):
        for code in (contract.CODE_DRIVER_NO_OUTPUT, contract.CODE_DRIVER_TIMEOUT):
            self.assertEqual(contract.code_for_transport(code), code)

    def test_structured_error_matches_envelope(self):
        exc = StructuredError(contract.CODE_DEPENDENCY_MISSING, "缺清洗产物",
                              task_id="t1", missing="cleaned/")
        env = exc.to_envelope()
        self.assertFalse(env["ok"])
        self.assertEqual(env["interface_version"], contract.INTERFACE_VERSION)
        self.assertEqual(env["error"]["code"], contract.CODE_DEPENDENCY_MISSING)
        self.assertEqual(env["error"]["task_id"], "t1")
        self.assertEqual(env["error"]["missing"], "cleaned/")


class TaskIdShapeTest(unittest.TestCase):
    def test_valid_and_invalid_shapes(self):
        self.assertTrue(valid_task_id("20261017-201144-2e9153"))
        self.assertTrue(valid_task_id("ok_1"))
        for bad in ("", ".", "..", "../etc", "/etc", "a/b", "a\\b", "x" * 80, "a b"):
            self.assertFalse(valid_task_id(bad), bad)


class StatusTest(unittest.TestCase):
    def test_aborted_is_a_terminal_status(self):
        self.assertIn(contract.STATUS_ABORTED, contract.STATUSES)
        self.assertIn(contract.STATUS_ABORTED, contract.TERMINAL_STATUSES)
        self.assertEqual(contract.STATUS_LABELS[contract.STATUS_ABORTED], "已中止")

    def test_statuses_cover_driver_statuses(self):
        # driver 会写出的四种状态都必须在本端清单里
        for status in ("queued", "running", "succeeded", "failed"):
            self.assertIn(status, contract.STATUSES)

    def test_describe_capabilities_lists_statuses_with_labels(self):
        cap = tools.describe_capabilities()
        listed = {item["value"]: item["label"] for item in cap["statuses"]}
        self.assertEqual(listed[contract.STATUS_ABORTED], "已中止")
        self.assertEqual(set(listed), set(contract.STATUSES))


class DescribeCapabilitiesTest(unittest.TestCase):
    def test_lists_all_tools_with_source_mcp(self):
        cap = tools.describe_capabilities()
        self.assertTrue(cap["ok"])
        self.assertEqual(cap["interface_version"], contract.INTERFACE_VERSION)
        self.assertEqual([t["name"] for t in cap["tools"]], list(EXPECTED_TOOLS))
        for entry in cap["tools"]:
            self.assertEqual(entry["source"], "mcp")
            self.assertIn("params", entry)

    def test_read_write_split_matches_table(self):
        cap = tools.describe_capabilities()
        read_only = {t["name"] for t in tools.TOOLS if t["read_only"]}
        self.assertEqual(set(cap["read_only_tools"]), read_only)
        self.assertEqual(set(cap["write_tools"]), set(EXPECTED_TOOLS) - read_only)
        self.assertEqual(set(cap["read_only_tools"]) | set(cap["write_tools"]),
                         set(EXPECTED_TOOLS))

    def test_long_running_list(self):
        cap = tools.describe_capabilities()
        self.assertIn("start_cleaning_task", cap["long_running_tools"])
        self.assertIn("wait_for_task", cap["long_running_tools"])

    def test_server_info_only_present_when_supplied(self):
        self.assertNotIn("server", tools.describe_capabilities())
        cap = tools.describe_capabilities({"name": "srv", "version": "0.1"})
        self.assertEqual(cap["server"]["name"], "srv")

    def test_carries_all_error_codes(self):
        cap = tools.describe_capabilities()
        for code in contract.STRUCTURED_ERROR_CODES:
            self.assertIn(code, cap["error_codes"])

    def test_entry_level_codes_are_advertised(self):
        """口令与 Host 是对端最容易撞到的两个，必须出现在对外清单里。"""
        cap = tools.describe_capabilities()
        self.assertIn(contract.CODE_UNAUTHORIZED, cap["error_codes"])
        self.assertIn(contract.CODE_FORBIDDEN_HOST, cap["error_codes"])

    def test_reserved_code_is_not_advertised(self):
        cap = tools.describe_capabilities()
        self.assertNotIn(contract.CODE_NOT_PREVIEWABLE, cap["error_codes"])


class ArgValidationTest(unittest.TestCase):
    """参数校验只有一套实现 —— 二十个工具共用。"""

    def setUp(self):
        self.ctx = dispatch.ToolContext(repo_root=_REPO_ROOT)

    def test_defaults_filled(self):
        # 默认值与执行侧保持一致：缺省取"清洗后"的"电影表"
        args = dispatch.validate_args(tools.get_tool("get_samples"), {"task_id": "t1"})
        self.assertEqual(args["sample_type"], "cleaned")
        self.assertEqual(args["table"], "movies")
        self.assertEqual(args["limit"], 5)

    def test_required_missing_rejected(self):
        with self.assertRaises(StructuredError) as cm:
            dispatch.validate_args(tools.get_tool("get_task_status"), {})
        self.assertEqual(cm.exception.code, contract.CODE_USAGE)

    def test_empty_string_means_missing(self):
        args = dispatch.validate_args(tools.get_tool("start_cleaning_task"),
                                      {"tag": "", "data_version": ""})
        self.assertNotIn("tag", args)
        self.assertNotIn("data_version", args)
        self.assertEqual(args["scope"], "full")

    def test_unknown_param_rejected(self):
        with self.assertRaises(StructuredError) as cm:
            dispatch.validate_args(tools.get_tool("get_task_status"),
                                   {"task_id": "t1", "nope": 1})
        self.assertEqual(cm.exception.code, contract.CODE_USAGE)
        self.assertIn("nope", str(cm.exception))

    def test_enum_out_of_range_rejected(self):
        with self.assertRaises(StructuredError) as cm:
            dispatch.validate_args(tools.get_tool("get_samples"),
                                   {"task_id": "t1", "table": "comments"})
        self.assertEqual(cm.exception.code, contract.CODE_USAGE)
        self.assertIn("ratings", str(cm.exception))

    def test_int_coercion_and_bounds(self):
        args = dispatch.validate_args(tools.get_tool("get_samples"),
                                      {"task_id": "t1", "limit": "9"})
        self.assertEqual(args["limit"], 9)
        with self.assertRaises(StructuredError):
            dispatch.validate_args(tools.get_tool("get_samples"),
                                   {"task_id": "t1", "limit": "abc"})
        with self.assertRaises(StructuredError):
            dispatch.validate_args(tools.get_tool("get_samples"),
                                   {"task_id": "t1", "limit": -1})
        with self.assertRaises(StructuredError):
            dispatch.validate_args(tools.get_tool("get_samples"),
                                   {"task_id": "t1", "limit": 10 ** 6})

    def test_bool_coercion(self):
        args = dispatch.validate_args(tools.get_tool("start_cleaning_task"),
                                      {"force": "true"})
        self.assertIs(args["force"], True)
        args = dispatch.validate_args(tools.get_tool("start_cleaning_task"),
                                      {"force": "no"})
        self.assertIs(args["force"], False)
        with self.assertRaises(StructuredError):
            dispatch.validate_args(tools.get_tool("start_cleaning_task"),
                                   {"force": "maybe"})

    def test_task_id_shape_is_checked_centrally(self):
        """任务编号只能是单层编号：路径形态一律拦住（此前只有部分入口做了这件事）。"""
        for bad in ("../etc", "/etc/passwd", "a/b", "a\\b", ".."):
            with self.assertRaises(StructuredError) as cm:
                dispatch.validate_args(tools.get_tool("get_task_status"), {"task_id": bad})
            self.assertEqual(cm.exception.code, contract.CODE_FORBIDDEN_PATH, bad)
        # 形状不合法但不是路径 → 参数错误
        with self.assertRaises(StructuredError) as cm:
            dispatch.validate_args(tools.get_tool("get_task_status"), {"task_id": "x" * 80})
        self.assertEqual(cm.exception.code, contract.CODE_USAGE)
        # 正常编号通过
        args = dispatch.validate_args(tools.get_tool("get_task_status"),
                                      {"task_id": "20261017-201144-2e9153"})
        self.assertEqual(args["task_id"], "20261017-201144-2e9153")

    def test_from_task_is_checked_with_same_rule(self):
        with self.assertRaises(StructuredError) as cm:
            dispatch.validate_args(tools.get_tool("score_task"),
                                   {"source": "task", "from_task": "../../etc"})
        self.assertEqual(cm.exception.code, contract.CODE_FORBIDDEN_PATH)

    def test_scheme_name_must_be_plain_json_filename(self):
        for bad in ("../../etc/passwd", "a.json.bak", "/abs.json"):
            with self.assertRaises(StructuredError) as cm:
                dispatch.validate_args(tools.get_tool("get_scheme_content"), {"name": bad})
            self.assertEqual(cm.exception.code, contract.CODE_FORBIDDEN_PATH, bad)
        args = dispatch.validate_args(tools.get_tool("get_scheme_content"),
                                      {"name": "scoring_scheme.v1.json"})
        self.assertEqual(args["name"], "scoring_scheme.v1.json")

    def test_score_task_has_no_force_parameter(self):
        """执行侧的评分子命令没有强制开关，因此工具表不再声明它 —— 不留假承诺。"""
        names = [p["name"] for p in tools.get_tool("score_task")["params"]]
        self.assertNotIn("force", names)

    def test_report_default_matches_driver(self):
        names = {p["name"]: p for p in tools.get_tool("get_report")["params"]}
        self.assertEqual(names["format"]["default"], "json")

    def test_samples_limit_is_capped_but_documented(self):
        names = {p["name"]: p for p in tools.get_tool("get_samples")["params"]}
        self.assertEqual(names["limit"]["maximum"], 2000)

    def test_score_task_requires_from_task_for_task_source(self):
        with self.assertRaises(StructuredError) as cm:
            dispatch.validate_args(tools.get_tool("score_task"), {"source": "task"})
        self.assertEqual(cm.exception.code, contract.CODE_USAGE)
        args = dispatch.validate_args(tools.get_tool("score_task"),
                                      {"source": "task", "from_task": "t1"})
        self.assertEqual(args["from_task"], "t1")
        # 其它来源不要求来源任务编号
        dispatch.validate_args(tools.get_tool("score_task"), {"source": "raw"})


class DispatchTest(unittest.TestCase):
    """调用入口：信封落地与异常兜底。"""

    def setUp(self):
        dispatch.reset_handlers()
        self.ctx = dispatch.ToolContext(repo_root=_REPO_ROOT,
                                        config={"name": "srv", "version": "0.1"},
                                        started_at="2026-10-17T00:00:00Z")

    def tearDown(self):
        dispatch.reset_handlers()

    def test_unknown_tool(self):
        env = dispatch.call_tool("nope", {}, self.ctx)
        self.assertFalse(env["ok"])
        self.assertEqual(env["error"]["code"], contract.CODE_USAGE)

    def test_unregistered_handler_is_reported_not_faked(self):
        env = dispatch.call_tool("health_check", {}, self.ctx)
        self.assertFalse(env["ok"])
        self.assertEqual(env["error"]["code"], contract.CODE_INTERNAL_ERROR)
        self.assertIn("尚未接入", env["error"]["message"])

    def test_handler_success_wrapped(self):
        dispatch.register("health_check", lambda args, ctx: {"status": "ok"})
        env = dispatch.call_tool("health_check", {}, self.ctx)
        self.assertTrue(env["ok"])
        self.assertEqual(env["interface_version"], contract.INTERFACE_VERSION)
        self.assertEqual(env["status"], "ok")

    def test_handler_structured_error_becomes_envelope(self):
        def boom(args, ctx):
            raise StructuredError(contract.CODE_FILE_NOT_FOUND, "没有这个产物",
                                  path="report.md")

        dispatch.register("get_report", boom)
        env = dispatch.call_tool("get_report", {"task_id": "t1"}, self.ctx)
        self.assertFalse(env["ok"])
        self.assertEqual(env["error"]["code"], contract.CODE_FILE_NOT_FOUND)
        self.assertEqual(env["error"]["path"], "report.md")

    def test_handler_unexpected_exception_keeps_message(self):
        def boom(args, ctx):
            raise ValueError("内部炸了")

        dispatch.register("get_report", boom)
        env = dispatch.call_tool("get_report", {"task_id": "t1"}, self.ctx)
        self.assertFalse(env["ok"])
        self.assertEqual(env["error"]["code"], contract.CODE_INTERNAL_ERROR)
        self.assertIn("ValueError", env["error"]["message"])
        self.assertIn("内部炸了", env["error"]["message"])

    def test_handler_returning_non_dict_is_error(self):
        dispatch.register("get_report", lambda args, ctx: ["不是字典"])
        env = dispatch.call_tool("get_report", {"task_id": "t1"}, self.ctx)
        self.assertFalse(env["ok"])
        self.assertEqual(env["error"]["code"], contract.CODE_INTERNAL_ERROR)

    def test_args_validated_before_handler_runs(self):
        called = []

        def handler(args, ctx):
            called.append(args)
            return {"ok": True}

        dispatch.register("get_samples", handler)
        env = dispatch.call_tool("get_samples", {"task_id": "t1", "table": "comments"},
                                 self.ctx)
        self.assertFalse(env["ok"])
        self.assertEqual(env["error"]["code"], contract.CODE_USAGE)
        self.assertEqual(called, [], "参数没通过校验时不该执行处理器")

    def test_register_rejects_duplicate(self):
        dispatch.register("health_check", lambda args, ctx: {})
        with self.assertRaises(RuntimeError):
            dispatch.register("health_check", lambda args, ctx: {})

    def test_register_rejects_unknown_tool(self):
        with self.assertRaises(RuntimeError):
            dispatch.register("made_up_tool", lambda args, ctx: {})

    def test_available_tools_reports_registered_only(self):
        self.assertEqual(dispatch.available_tools(), ())
        dispatch.register("health_check", lambda args, ctx: {})
        self.assertEqual(dispatch.available_tools(), ("health_check",))


class StdlibOnlyTest(unittest.TestCase):
    """契约层不许把第三方包拖进来 —— 它要能在裸解释器里被验证。"""

    def test_package_import_does_not_pull_protocol_sdk(self):
        """在**干净的半成品进程**里导入契约层，再看有没有拖来官方协议包。

        不能就地查本进程的 sys.modules：discover 会把所有测试模块载进同一进程，
        服务层测试早就把 `mcp` 拖进来了，就地查分不清是谁干的（机器上装了 mcp
        包时必误报，Linux CI 没装才侥幸通过）。
        """
        code = ("import sys; sys.path.insert(0, %r);"
                "import mcpserver.contract, mcpserver.dispatch;"
                "print('mcp' if 'mcp' in sys.modules else 'clean')" % _HADOOP_DIR)
        proc = subprocess.run([sys.executable, "-c", code],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        self.assertEqual(proc.stdout.strip(), "clean",
                         "契约层不应导入官方协议包（只允许服务层导入）")

    def test_tool_context_server_info_skips_empty_values(self):
        ctx = dispatch.ToolContext(repo_root=_REPO_ROOT, config={"name": "srv",
                                                                 "version": ""})
        self.assertEqual(ctx.as_server_info(), {"name": "srv"})


if __name__ == "__main__":
    unittest.main()
