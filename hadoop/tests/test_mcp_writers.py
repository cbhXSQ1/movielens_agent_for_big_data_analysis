# -*- coding: utf-8 -*-
"""tests/test_mcp_writers.py —— 工具层「写操作 + driver 桥接」单测。

覆盖三件事：
  1. **桥接层的失败分类**：成功信封、非 JSON 输出、无输出、超时、退出码 2/3/4/5/6 的换算、
     driver 自产结构化码原样保留、跑不起来；
  2. **六个处理器的参数搬运与说明补全**：选项映射（范围/任务类型/执行方式/版本/标签/
     两份方案/强制开关/评分来源）、占用冲突与版本冲突的补充信息、依赖缺失的透传；
     自定义方案上传（先校验后落盘、失败不留半成品、默认方案不可覆盖）与删除
     （默认方案永不可删、占用时不许删、只删方案目录内的普通文件）；
  3. **受理成功后留下的提交凭据**：三条发起/评分能力要往任务目录写一份
     提交标记（文件名引用契约层唯一来源；中止能力靠它区分「本服务起的任务」与「别人在
     终端里起的任务」），
     写不进去时不得影响受理结论。

做法：在临时目录里搭一个**假仓库根**（只放一个假 driver 脚本，需要时再放一份假 env.sh），
用 `ctx.repo_root` + `ctx.config["driver_script"]` 指过去。这样测的是真实的子进程调用路径
（参数列表、退出码、stdout/stderr 分流、超时终止），而不是把执行层打桩掉 ——
打桩掉的桥接层单测只能证明「桩返回什么就透传什么」，证明不了它会用参数列表调命令。

假脚本的行为（模式与返回值）全部由环境变量驱动，通过 `ctx.env` 传下去；
收到的参数会追加写进一份日志文件，供断言选项映射用。产物目录（`ML_VAR_DIR`）默认指到
临时目录，提交凭据因此也落在临时目录里，不会污染仓库的真实产物。

关于「本段不依赖执行层」这条：四个测试模块在同一个进程里跑，别的段落为了读写任务状态
本来就要导入执行层，所以只能断言**本段自己导入与调用时新增了什么**（模块表差异），
不能断言整个进程的模块表。

只用标准库；不启集群、不碰仓库里的真实产物。
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_HADOOP_DIR = os.path.join(_REPO_ROOT, "hadoop")
for _path in (_HADOOP_DIR, _REPO_ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)


def _exec_chain_modules():
    """当前进程里已经存在的 driver 执行链模块名。

    只用来做「导入前后差异」的比对：四个测试模块在同一个进程里跑，别的段落为了读写
    任务状态本来就要导入执行层，所以**不能**断言全局模块表里没有它 —— 那会互相踩。
    能断言的是「导入本段的模块没有新增这类依赖」。
    """
    return frozenset(name for name in sys.modules
                     if name == "driver" or name.startswith("driver."))


#: 导入本段之前/之后的模块表快照（差异即本段自己带来的依赖）
_MODULES_BEFORE_IMPORT = frozenset(sys.modules)
_EXEC_CHAIN_BEFORE_IMPORT = _exec_chain_modules()

from mcpserver import contract, dispatch, writers                     # noqa: E402
from mcpserver import driver_bridge as bridge                        # noqa: E402
from mcpserver.contract import StructuredError                       # noqa: E402
from mcpserver.tools import get_tool as mcpserver_tool                # noqa: E402

_MODULES_ADDED_AT_IMPORT = frozenset(sys.modules) - _MODULES_BEFORE_IMPORT
_EXEC_CHAIN_ADDED_AT_IMPORT = _exec_chain_modules() - _EXEC_CHAIN_BEFORE_IMPORT

#: 假 driver：用一个只用标准库的小脚本模拟「stdout 一个信封、日志走 stderr」的约定。
#: 行为由 FAKE_MODE 选择，信封与退出码由 FAKE_ENVELOPE / FAKE_EXIT 给。
#: 注意：这里是**测试夹具**，与业务无关；它不产出任何业务数字。
FAKE_DRIVER_SOURCE = '''
import json
import os
import sys
import time

argv = sys.argv[1:]
log_path = os.environ.get("FAKE_ARGV_LOG")
if log_path:
    handle = open(log_path, "a")
    handle.write(json.dumps(argv, ensure_ascii=False))
    handle.write(chr(10))
    handle.close()

mode = os.environ.get("FAKE_MODE", "envelope")
stderr_text = os.environ.get("FAKE_STDERR", "")

if mode == "sleep":
    time.sleep(float(os.environ.get("FAKE_SLEEP", "5")))
    sys.stdout.write(json.dumps({"ok": True, "slept": True}))
elif mode == "empty":
    pass
elif mode == "garbage":
    sys.stdout.write(os.environ.get("FAKE_STDOUT", "这不是一个信封"))
elif mode == "echo":
    payload = {
        "ok": True,
        "argv": argv,
        "env": {key: os.environ.get(key, "")
                for key in ("ML_VAR_DIR", "HDFS_BASE", "ML_RAW_DIR")},
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
else:
    sys.stdout.write(os.environ.get("FAKE_ENVELOPE", json.dumps({"ok": True})))

if stderr_text:
    sys.stderr.write(stderr_text)

sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
'''

#: 假 env.sh：用来验证「三个变量确实是从这个脚本读出来的」，而不是写死在代码里。
FAKE_ENV_SH = '''#!/usr/bin/env bash
export ML_VAR_DIR="/fake-envsh/var"
export HDFS_BASE="/fake-envsh/data"
export ML_RAW_DIR="/fake-envsh/raw"
export PATH="${PATH}"
'''

#: 本段负责的六条工具（其余由别的段落实现，这里不该出现）。
WRITER_TOOLS = (
    "validate_config",
    "list_schemes",
    "start_cleaning_task",
    "start_clean_task",
    "score_task",
    "upload_scheme",
    "delete_scheme",
)


def envelope(**payload):
    """构造一个成功信封（假 driver 用）。"""
    body = {"ok": True, "interface_version": "1.1"}
    body.update(payload)
    return json.dumps(body, ensure_ascii=False)


def error_envelope(code=None, message="出错了", **extra):
    """构造一个失败信封（假 driver 用）。"""
    err = {}
    if code:
        err["code"] = code
    err["message"] = message
    err.update(extra)
    return json.dumps({"ok": False, "error": err}, ensure_ascii=False)


#: 区分「没传这个参数」与「显式传 None」—— 两者的含义在夹具里不一样。
_UNSET = object()


def parse_option_argv(argv):
    """把 `[子命令, 选项…]` 解析成 `{选项: 取值}`（开关记 True）。

    **严格**：取值必须与键写在**同一个**元素里（`--键=值`），裸元素只允许是开关。
    这条严格性本身就是安全约束 —— 一旦有人把取值拆成独立元素，取值里的连字符就可能被
    下游解析成开关。审查实测过三种后果：`--force` 绕过单任务互斥、`--exec` 切到另一条
    执行路径、`--foreground` 把任务跑成前台并被桥接的超时连同进程组杀掉。
    """
    out = {}
    for item in list(argv)[1:]:
        if not isinstance(item, str) or not item.startswith("--"):
            raise AssertionError("参数列表里出现了不是选项的元素：%r" % (item,))
        body = item[2:]
        if "=" in body:
            key, value = body.split("=", 1)
            out[key] = value
        else:
            out[body] = True
    return out


class FakeRepoCase(unittest.TestCase):
    """每个用例一个临时「仓库根」与一份临时产物目录，内含假 driver；需要时补假 env.sh。

    产物目录（`ML_VAR_DIR`）默认指向临时目录：受理成功后处理器会往任务目录写提交标记，
    让它落到仓库的 `var/` 里会污染别处的任务列表，也违背「测试不碰真实产物」。
    """

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="mcp-writers-")
        self.addCleanup(shutil.rmtree, self.repo, True)
        os.makedirs(os.path.join(self.repo, "hadoop", "driver"))
        os.makedirs(os.path.join(self.repo, "hadoop", "scripts"))
        self.driver_path = os.path.join(self.repo, "hadoop", "driver", "run_task.py")
        self._write(self.driver_path, FAKE_DRIVER_SOURCE)
        self.argv_log = os.path.join(self.repo, "argv.log")
        self.var_dir = os.path.join(self.repo, "var")
        bridge.clear_env_cache()
        self.addCleanup(bridge.clear_env_cache)

    # ---- 夹具小工具 ----

    @staticmethod
    def _write(path, text):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def write_env_sh(self, text=FAKE_ENV_SH):
        self._write(os.path.join(self.repo, "hadoop", "scripts", "env.sh"), text)

    def make_ctx(self, mode="envelope", envelope_text=None, exit_code=None,
                 stderr="", sleep=None, env=None, config=None, var_dir=_UNSET):
        """按需要在假仓库根上组装一次调用的上下文。

        `var_dir` 默认用本用例的临时目录；显式传 `None` 就不注入 `ML_VAR_DIR`，
        留给「环境来源优先级」那几个用例自己决定（它们要验证 env.sh 兜底与外部覆盖）。
        """
        child_env = {"FAKE_ARGV_LOG": self.argv_log, "FAKE_MODE": mode}
        if envelope_text is not None:
            child_env["FAKE_ENVELOPE"] = envelope_text
        if exit_code is not None:
            child_env["FAKE_EXIT"] = str(exit_code)
        if stderr:
            child_env["FAKE_STDERR"] = stderr
        if sleep is not None:
            child_env["FAKE_SLEEP"] = str(sleep)
        if var_dir is _UNSET:
            child_env["ML_VAR_DIR"] = self.var_dir
        elif var_dir is not None:
            child_env["ML_VAR_DIR"] = var_dir
        child_env.update(env or {})
        cfg = {"driver_python": sys.executable}
        cfg.update(config or {})
        return dispatch.ToolContext(repo_root=self.repo, env=child_env, config=cfg)

    def marker_path(self, task_id):
        return os.path.join(self.var_dir, "tasks", task_id, writers.SUBMIT_MARKER)

    def read_marker(self, task_id):
        """读回提交标记；没有就返回 None（用例里区分「没写」与「写错」）。"""
        path = self.marker_path(task_id)
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    #: 造「可评分的来源任务」时用的默认编号。
    READY_TASK_ID = "20261017-090000-clean0"

    def make_task(self, task_id=None, status="succeeded", data_version="ml1m-clean-v1",
                  cleaned=None, status_text=None):
        """在临时产物目录里造一个任务：状态文件 + 可选的清洗产物。

        `cleaned=None` 表示三份表齐全；传空列表就是一份产物都没有；
        `status_text` 用来造假一份读不出来的状态文件。
        """
        task_id = task_id or self.READY_TASK_ID
        directory = os.path.join(self.var_dir, "tasks", task_id)
        os.makedirs(directory)
        if status_text is not None:
            self._write(os.path.join(directory, "status.json"), status_text)
        else:
            self._write(os.path.join(directory, "status.json"),
                        json.dumps({"task_id": task_id, "status": status,
                                    "data_version": data_version}, ensure_ascii=False))
        if cleaned is None:
            cleaned = [filename for _table, filename in writers.CLEANED_TABLE_FILES]
        cleaned_dir = os.path.join(directory, "cleaned", data_version)
        os.makedirs(cleaned_dir, exist_ok=True)
        for filename in cleaned:
            self._write(os.path.join(cleaned_dir, filename), "x\n")
        return task_id

    def recorded_argv(self):
        """读回假 driver 收到的参数列表（每次调用一条）。"""
        if not os.path.isfile(self.argv_log):
            return []
        with open(self.argv_log, "r", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def last_argv(self):
        calls = self.recorded_argv()
        self.assertTrue(calls, "假 driver 没被调用到")
        return calls[-1]

    def last_options(self):
        """最后一次调用的选项映射（严格解析：取值必须与键同在一个元素里）。"""
        return parse_option_argv(self.last_argv())

    def register_writers(self):
        dispatch.reset_handlers()
        self.addCleanup(dispatch.reset_handlers)
        for name, handler in writers.HANDLERS.items():
            dispatch.register(name, handler)


# ---------------------------------------------------------------------------
# 模块契约
# ---------------------------------------------------------------------------

class SharedConstantsTest(unittest.TestCase):
    """控制文件名与状态取值只认契约层那一份，写入方不再写字面量。"""

    def test_submit_marker_name_comes_from_the_contract(self):
        self.assertIs(writers.SUBMIT_MARKER, contract.CONTROL_FILE_SUBMIT)
        self.assertIn(writers.SUBMIT_MARKER, contract.CONTROL_FILE_NAMES)

    def test_cleaned_products_are_looked_up_only_for_succeeded_tasks(self):
        # 「成功」这个判断只有一种写法：契约层的状态常量
        import inspect
        source = inspect.getsource(writers.check_task_dependency)
        self.assertIn("STATUS_SUCCEEDED", source)
        self.assertNotIn('"succeeded"', source)


class HandlerSurfaceTest(FakeRepoCase):
    def test_exports_exactly_the_expected_tools(self):
        self.assertEqual(sorted(writers.HANDLERS), sorted(WRITER_TOOLS))

    def test_every_handler_is_registered_in_tool_table(self):
        for name in writers.HANDLERS:
            self.assertIsNotNone(dispatch.get_tool(name), name)

    def test_handlers_can_be_loaded_by_dispatch(self):
        self.register_writers()
        for name in WRITER_TOOLS:
            self.assertIn(name, dispatch.available_tools())

    def test_importing_writers_adds_no_execution_chain_dependency(self):
        # 本段只做搬运：执行体一律经统一调用通道，不直接引用 driver 执行层。
        # 断言的是**本段自己导入时新增了什么**（差异），不是整个进程的模块表 ——
        # 四个测试模块同进程运行，别的段落为了读写任务状态本来就要导入执行层。
        self.assertEqual(set(), set(_EXEC_CHAIN_ADDED_AT_IMPORT),
                         "导入 mcpserver.writers 时新增了执行链依赖：%s"
                         % sorted(_EXEC_CHAIN_ADDED_AT_IMPORT))

    def test_importing_writers_pulls_no_protocol_package(self):
        # 同样只看差异：官方协议包只允许服务层用到，且必须留在独立环境里
        added = [name for name in _MODULES_ADDED_AT_IMPORT
                 if name == "mcp" or name.startswith("mcp.")]
        self.assertEqual([], added, "导入 mcpserver.writers 时引入了协议包：%s" % added)

    def test_calling_handlers_adds_no_execution_chain_dependency(self):
        # 调用五个处理器会起子进程，但**不该**在本进程里拉起 driver 执行链
        before = _exec_chain_modules()
        ctx = self.make_ctx(envelope_text=START_OK)
        writers.handle_validate_config({}, self.make_ctx(envelope_text=envelope(errors=[])))
        writers.handle_list_schemes({}, self.make_ctx(envelope_text=envelope(schemes=[])))
        writers.handle_start_cleaning_task({"scope": "full"}, ctx)
        writers.handle_start_clean_task({"scope": "full"}, self.make_ctx(envelope_text=START_OK))
        writers.handle_score_task({"source": "raw"},
                                  self.make_ctx(envelope_text=envelope(task_id="t1")))
        self.assertEqual(set(), set(_exec_chain_modules() - before),
                         "调用处理器时新增了执行链依赖：%s"
                         % sorted(_exec_chain_modules() - before))


# ---------------------------------------------------------------------------
# 仓库根与解释器：不写死路径
# ---------------------------------------------------------------------------

class PathDerivationTest(unittest.TestCase):
    def test_repo_root_defaults_to_this_checkout(self):
        self.assertEqual(bridge.repo_root_of(None), _REPO_ROOT)

    def test_driver_script_sits_under_repo_root(self):
        self.assertEqual(bridge.driver_script(None),
                         os.path.join(_REPO_ROOT, bridge.DRIVER_RELPATH))

    def test_context_overrides_both(self):
        # 注意：实现会把这两个路径归一化成绝对路径（Windows 上 `/tmp/x` 会补上当前
        # 盘符变成 `D:\tmp\x`），期望值按同一条规则算，不写死某一种分隔符
        ctx = dispatch.ToolContext(repo_root="/tmp/fake-repo",
                                   config={"driver_script": "/tmp/fake-driver.py",
                                           "driver_python": "/tmp/fake-python"})
        self.assertEqual(bridge.repo_root_of(ctx), os.path.abspath("/tmp/fake-repo"))
        self.assertEqual(bridge.driver_script(ctx),
                         os.path.abspath("/tmp/fake-driver.py"))
        self.assertEqual(bridge.python_for(ctx), "/tmp/fake-python")

    def test_default_python_is_current_interpreter(self):
        self.assertEqual(bridge.python_for(None), sys.executable)


# ---------------------------------------------------------------------------
# 命令行装配：只走参数列表，且取值必须与键同在一个元素里
# ---------------------------------------------------------------------------

class ArgvBuildingTest(FakeRepoCase):
    def test_argv_is_a_plain_list(self):
        argv = bridge.build_argv("validate", {"rules": "config/a.json"}, self.make_ctx())
        self.assertIsInstance(argv, list)
        self.assertEqual(argv[0], sys.executable)
        self.assertEqual(argv[1], self.driver_path)
        self.assertEqual(argv[2:], ["validate", "--rules=config/a.json"])

    def test_values_are_carried_in_the_same_element(self):
        # 带空格与引号的路径必须原样作为一个元素传下去（没有 shell 解释）
        weird = "config/dir with space/it's a scheme.json"
        argv = bridge.build_argv("start", {"rules": weird}, self.make_ctx())
        self.assertEqual(argv[2:], ["start", "--rules=%s" % weird])
        self.assertEqual(parse_option_argv(["start"] + argv[3:]), {"rules": weird})

    def test_no_bare_value_element_is_ever_emitted(self):
        # 这条是安全约束的守门人：一旦有人把「键、值」拆成两个元素，取值里的连字符
        # 就可能被下游当成开关，这里必须炸。
        argv = bridge.build_argv("start", {"tag": "demo", "task-type": "full"},
                                 self.make_ctx())
        for item in argv[3:]:                      # 跳过解释器、入口脚本与子命令
            if item.startswith("--"):
                continue
            self.fail("参数列表里出现了独立的取值元素：%r" % (item,))

    def test_none_and_empty_are_skipped(self):
        argv = bridge.build_argv("start", {"tag": None, "scoring": "", "scope": "full"},
                                 self.make_ctx())
        self.assertEqual(argv[2:], ["start", "--scope=full"])

    def test_true_becomes_flag_and_false_disappears(self):
        argv = bridge.build_argv("start", {"force": True, "foreground": False},
                                 self.make_ctx())
        self.assertEqual(argv[2:], ["start", "--force"])

    def test_non_string_values_are_stringified(self):
        argv = bridge.build_argv("samples", {"n": 5}, self.make_ctx())
        self.assertEqual(argv[2:], ["samples", "--n=5"])

    def test_option_like_values_are_rejected(self):
        # 取值以两个连字符开头 → 拒绝，而不是想办法转义后放行
        for dangerous in ("--force", "--exec", "--foreground", "--task-type=clean"):
            with self.subTest(value=dangerous):
                with self.assertRaises(StructuredError) as caught:
                    bridge.option_argv({"tag": dangerous})
                self.assertEqual(caught.exception.code, contract.CODE_USAGE)
                self.assertEqual(caught.exception.extra["param"], "tag")

    def test_single_dash_value_is_carried_safely(self):
        # 单个连字符开头的取值不构成选项（执行侧只认两个连字符），照常等号携带
        self.assertEqual(bridge.option_argv({"tag": "-draft"}), ["--tag=-draft"])

    def test_structural_guard_catches_a_regressed_bare_value(self):
        # 直接喂一个「键、值分开」的列表，护栏必须拦下（模拟将来有人改回旧写法）
        with self.assertRaises(StructuredError) as caught:
            bridge._assert_option_argv({"tag": "demo"}, ["--tag", "demo"])
        self.assertEqual(caught.exception.code, contract.CODE_DRIVER_BAD_OUTPUT)
        self.assertIn("--tag", caught.exception.message)

    def test_structural_guard_allows_the_intended_shapes(self):
        bridge._assert_option_argv({"force": True, "tag": "demo"},
                                   ["--force", "--tag=demo"])

    def test_structural_guard_rejects_unknown_option_key(self):
        # 参数列表里出现"没人要的选项键"，说明拼接逻辑跑偏了（例如取值里混进了等号）
        with self.assertRaises(StructuredError) as caught:
            bridge._assert_option_argv({"tag": "demo"}, ["--other=x"])
        self.assertEqual(caught.exception.code, contract.CODE_DRIVER_BAD_OUTPUT)

    def test_real_execution_side_parser_agrees(self):
        """用**真实执行侧解析器**确认：等号写法里取值不会被当成开关。

        顺手把旧写法的后果演示出来（`["--tag", "--force"]` → force 变成独立选项），
        这条用例的意义是留档：为什么必须用等号写法，而不是"风格问题"。
        """
        from driver.cli import parse_args
        old_opts, _ = parse_args(["--tag", "--force"])
        self.assertEqual(old_opts["tag"], "1")          # 取值丢了，变成了开关
        self.assertEqual(old_opts["force"], "1")        # 凭空多出一个强制开关

        new_opts, _ = parse_args(["--tag=--force"])
        self.assertEqual(new_opts["tag"], "--force")    # 取值原样保留
        self.assertNotIn("force", new_opts)             # 选项集合没被数据改变


# ---------------------------------------------------------------------------
# 失败分类
# ---------------------------------------------------------------------------

class BridgeSuccessTest(FakeRepoCase):
    def test_envelope_is_parsed_and_shell_stripped(self):
        text = envelope(errors=[], warnings=[], versions={"rule": "1.0.0"})
        result = bridge.run_driver(bridge.CMD_VALIDATE, None,
                                   self.make_ctx(envelope_text=text))
        self.assertTrue(result.ok)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.payload["versions"], {"rule": "1.0.0"})
        self.assertNotIn("ok", result.payload)
        self.assertNotIn("interface_version", result.payload)

    def test_require_ok_returns_payload(self):
        result = bridge.run_driver(bridge.CMD_SCHEMES, None,
                                   self.make_ctx(envelope_text=envelope(schemes=[{"a": 1}])))
        self.assertEqual(bridge.require_ok(result), {"schemes": [{"a": 1}]})

    def test_stderr_does_not_leak_into_payload(self):
        result = bridge.run_driver(bridge.CMD_VALIDATE, None,
                                   self.make_ctx(envelope_text=envelope(errors=[]),
                                                 stderr="日志走这里"))
        self.assertNotIn("日志走这里", json.dumps(result.payload, ensure_ascii=False))
        self.assertIn("日志走这里", result.stderr)

    def test_cwd_is_the_repo_root(self):
        # 假 driver 在 stdout 里报不出 cwd，用「相对路径能写到仓库根」间接确认：
        # 这里直接看 argv 与调用成功，cwd 由 build/run 的约定保证（见 test_cwd_via_marker）
        ctx = self.make_ctx(mode="echo")
        result = bridge.run_driver(bridge.CMD_VALIDATE, None, ctx)
        self.assertTrue(result.ok)


class BridgeTransportFailureTest(FakeRepoCase):
    def test_empty_stdout_is_no_output(self):
        with self.assertRaises(StructuredError) as caught:
            bridge.run_driver(bridge.CMD_VALIDATE, None,
                              self.make_ctx(mode="empty", exit_code=1, stderr="炸在启动里"))
        exc = caught.exception
        self.assertEqual(exc.code, contract.CODE_DRIVER_NO_OUTPUT)
        self.assertEqual(exc.extra["driver_code"], 1)
        self.assertIn("炸在启动里", exc.extra["stderr_tail"])
        self.assertIn("validate", exc.message)

    def test_non_json_stdout_is_bad_output(self):
        with self.assertRaises(StructuredError) as caught:
            bridge.run_driver(bridge.CMD_VALIDATE, None,
                              self.make_ctx(mode="garbage", exit_code=0,
                                            env={"FAKE_STDOUT": "FATAL: 不是信封"}))
        exc = caught.exception
        self.assertEqual(exc.code, contract.CODE_DRIVER_BAD_OUTPUT)
        self.assertIn("FATAL", exc.extra["stdout_preview"])

    def test_json_that_is_not_an_object_is_bad_output(self):
        with self.assertRaises(StructuredError) as caught:
            bridge.run_driver(bridge.CMD_VALIDATE, None,
                              self.make_ctx(envelope_text="[1, 2, 3]"))
        self.assertEqual(caught.exception.code, contract.CODE_DRIVER_BAD_OUTPUT)

    def test_envelope_without_ok_field_is_bad_output(self):
        with self.assertRaises(StructuredError) as caught:
            bridge.run_driver(bridge.CMD_VALIDATE, None,
                              self.make_ctx(envelope_text=json.dumps({"errors": []})))
        self.assertEqual(caught.exception.code, contract.CODE_DRIVER_BAD_OUTPUT)
        self.assertIn("ok", caught.exception.message)

    def test_ok_envelope_with_nonzero_exit_is_reported_as_contradiction(self):
        with self.assertRaises(StructuredError) as caught:
            bridge.run_driver(bridge.CMD_VALIDATE, None,
                              self.make_ctx(envelope_text=envelope(errors=[]), exit_code=3))
        exc = caught.exception
        self.assertEqual(exc.code, contract.CODE_DRIVER_BAD_OUTPUT)
        self.assertEqual(exc.extra["driver_code"], 3)
        self.assertIn("矛盾", exc.message)

    def test_timeout_kills_the_call_and_says_so(self):
        ctx = self.make_ctx(mode="sleep", sleep=5, config={"driver_timeouts": {"validate": 0.4}})
        with self.assertRaises(StructuredError) as caught:
            bridge.run_driver(bridge.CMD_VALIDATE, None, ctx)
        exc = caught.exception
        self.assertEqual(exc.code, contract.CODE_DRIVER_TIMEOUT)
        self.assertEqual(exc.extra["timeout_seconds"], 0.4)
        self.assertIn("没有返回", exc.message)

    def test_missing_interpreter_is_unreachable(self):
        ctx = self.make_ctx(config={"driver_python": "/nonexistent/python3"})
        with self.assertRaises(StructuredError) as caught:
            bridge.run_driver(bridge.CMD_VALIDATE, None, ctx)
        self.assertEqual(caught.exception.code, contract.CODE_DRIVER_UNREACHABLE)

    def test_transport_failures_do_not_become_task_failures(self):
        # 通道故障必须与「任务失败」区分开，否则用户会被引导去查作业日志
        ctx = self.make_ctx(mode="empty", exit_code=3)
        with self.assertRaises(StructuredError) as caught:
            bridge.run_driver(bridge.CMD_START, None, ctx)
        self.assertNotEqual(caught.exception.code, contract.CODE_TASK_FAILED)


class BridgeExitCodeMappingTest(FakeRepoCase):
    """信封里没有结构化码时，按退出码换算，并把原退出码留档。"""

    CASES = (
        (2, contract.CODE_USAGE),
        (3, contract.CODE_TASK_FAILED),
        (4, contract.CODE_TASK_NOT_FOUND),
        (5, contract.CODE_TASK_NOT_FINISHED),
        (6, contract.CODE_VERSION_CONFLICT),
    )

    def test_exit_codes_map_to_structured_codes(self):
        for exit_code, expected in self.CASES:
            ctx = self.make_ctx(envelope_text=error_envelope(message="原话"),
                                exit_code=exit_code)
            with self.assertRaises(StructuredError) as caught:
                bridge.require_ok(bridge.run_driver(bridge.CMD_VALIDATE, None, ctx))
            exc = caught.exception
            self.assertEqual(exc.code, expected, exit_code)
            self.assertEqual(exc.extra["driver_code"], exit_code)
            self.assertEqual(exc.message, "原话")

    def test_driver_native_codes_survive_normalization(self):
        for native in (contract.CODE_CONFIG_INVALID, contract.CODE_TASK_ALREADY_RUNNING,
                       contract.CODE_DEPENDENCY_MISSING):
            ctx = self.make_ctx(envelope_text=error_envelope(code=native, message="原话"),
                                exit_code=2)
            with self.assertRaises(StructuredError) as caught:
                bridge.require_ok(bridge.run_driver(bridge.CMD_START, None, ctx))
            exc = caught.exception
            self.assertEqual(exc.code, native)
            self.assertEqual(exc.extra["driver_code"], 2)
            self.assertEqual(exc.message, "原话")

    def test_details_are_preserved(self):
        text = error_envelope(code=contract.CODE_CONFIG_INVALID, message="配置校验失败",
                              details=["第 1 条", "第 2 条"])
        ctx = self.make_ctx(envelope_text=text, exit_code=2)
        with self.assertRaises(StructuredError) as caught:
            bridge.require_ok(bridge.run_driver(bridge.CMD_VALIDATE, None, ctx))
        self.assertEqual(caught.exception.extra["details"], ["第 1 条", "第 2 条"])


# ---------------------------------------------------------------------------
# 超时口径
# ---------------------------------------------------------------------------

class TimeoutPolicyTest(FakeRepoCase):
    def test_sync_commands_get_a_shorter_limit_than_acceptance(self):
        self.assertLess(bridge.DEFAULT_TIMEOUTS[bridge.CMD_VALIDATE],
                        bridge.DEFAULT_TIMEOUTS[bridge.CMD_START])
        self.assertLess(bridge.DEFAULT_TIMEOUTS[bridge.CMD_SCHEMES],
                        bridge.DEFAULT_TIMEOUTS[bridge.CMD_SCORE])

    def test_override_only_affects_the_named_command(self):
        # 给 validate 一个极短上限，同时让 start 跑得比它久：start 不该被牵连
        ctx = self.make_ctx(mode="sleep", sleep=0.8,
                            config={"driver_timeouts": {"validate": 0.3}})
        with self.assertRaises(StructuredError) as caught:
            bridge.run_driver(bridge.CMD_VALIDATE, None, ctx)
        self.assertEqual(caught.exception.code, contract.CODE_DRIVER_TIMEOUT)
        started = bridge.run_driver(bridge.CMD_START, None, ctx)
        self.assertTrue(started.ok)

    def test_timeout_for_reads_config_and_falls_back(self):
        ctx = dispatch.ToolContext(repo_root=self.repo,
                                   config={"driver_timeouts": {"score": 7}})
        self.assertEqual(bridge.timeout_for("score", ctx), 7.0)
        self.assertEqual(bridge.timeout_for("validate", ctx),
                         bridge.DEFAULT_TIMEOUTS["validate"])


# ---------------------------------------------------------------------------
# 环境变量：从 env.sh 读，不写死
# ---------------------------------------------------------------------------

class EnvironmentTest(FakeRepoCase):
    def test_env_sh_values_are_read(self):
        self.write_env_sh()
        values = bridge.read_env_sh(self.repo)
        self.assertEqual(values["ML_VAR_DIR"], "/fake-envsh/var")
        self.assertEqual(values["HDFS_BASE"], "/fake-envsh/data")
        self.assertEqual(values["ML_RAW_DIR"], "/fake-envsh/raw")

    def test_only_whitelisted_keys_are_kept(self):
        self.write_env_sh('#!/usr/bin/env bash\nexport ML_VAR_DIR="/v"\n'
                          'export SECRET_TOKEN="不要抄进来"\n')
        # 开发机上常 export 过白名单里的键（如 JAVA_HOME），source 后会被一起读进来，
        # 污染"只留白名单键"的断言。先把白名单键从进程环境请出去，让用例只测 env.sh。
        quiet = {k: v for k, v in os.environ.items() if k not in bridge.ENV_KEYS}
        with mock.patch.dict(os.environ, quiet, clear=True):
            values = bridge.read_env_sh(self.repo)
        self.assertEqual(values, {"ML_VAR_DIR": "/v"})

    def test_missing_env_sh_is_not_fatal(self):
        self.assertEqual(bridge.read_env_sh(self.repo), {})
        result = bridge.run_driver(bridge.CMD_VALIDATE, None, self.make_ctx())
        self.assertTrue(result.ok)

    def test_child_process_sees_the_three_locations(self):
        self.write_env_sh()
        with mock.patch.dict(os.environ, {"ML_VAR_DIR": "", "HDFS_BASE": "",
                                          "ML_RAW_DIR": ""}):
            # var_dir=None：不注入上下文那一份，让 env.sh 的兜底作用显出来
            result = bridge.run_driver(bridge.CMD_VALIDATE, None,
                                       self.make_ctx(mode="echo", var_dir=None))
        self.assertEqual(result.payload["env"]["ML_VAR_DIR"], "/fake-envsh/var")
        self.assertEqual(result.payload["env"]["HDFS_BASE"], "/fake-envsh/data")
        self.assertEqual(result.payload["env"]["ML_RAW_DIR"], "/fake-envsh/raw")

    def test_context_env_wins_over_env_sh(self):
        self.write_env_sh()
        ctx = self.make_ctx(mode="echo", env={"ML_VAR_DIR": "/from/context"}, var_dir=None)
        result = bridge.run_driver(bridge.CMD_VALIDATE, None, ctx)
        self.assertEqual(result.payload["env"]["ML_VAR_DIR"], "/from/context")

    def test_env_value_helper_reads_the_same_sources(self):
        self.write_env_sh()
        with mock.patch.dict(os.environ, {"HDFS_BASE": ""}):
            self.assertEqual(bridge.env_value("HDFS_BASE", self.make_ctx()),
                             "/fake-envsh/data")
        self.assertEqual(bridge.env_value("NOT_SET_ANYWHERE", self.make_ctx(), "兜底"),
                         "兜底")

    def test_real_env_sh_provides_the_three_locations(self):
        # 真仓库的 env.sh 必须能给出这三个位置（验证「来源」这件事本身成立）
        values = bridge.read_env_sh(_REPO_ROOT)
        for key in ("ML_VAR_DIR", "HDFS_BASE", "ML_RAW_DIR"):
            self.assertIn(key, values, key)
            self.assertTrue(values[key].strip(), key)


# ---------------------------------------------------------------------------
# validate_config / list_schemes
# ---------------------------------------------------------------------------

class ValidateConfigHandlerTest(FakeRepoCase):
    def test_returns_errors_warnings_versions_as_is(self):
        text = envelope(errors=[], warnings=["方案里有未使用的规则"],
                        versions={"rule": "1.0.0", "scoring": "1.0.0"})
        ctx = self.make_ctx(envelope_text=text)
        payload = writers.handle_validate_config({}, ctx)
        self.assertEqual(payload["errors"], [])
        self.assertEqual(payload["warnings"], ["方案里有未使用的规则"])
        self.assertEqual(payload["versions"], {"rule": "1.0.0", "scoring": "1.0.0"})
        self.assertNotIn("ok", payload)
        self.assertNotIn("interface_version", payload)

    def test_missing_optional_fields_are_not_invented(self):
        ctx = self.make_ctx(envelope_text=envelope(errors=[]))
        payload = writers.handle_validate_config({}, ctx)
        self.assertEqual(payload, {"errors": []})
        self.assertNotIn("warnings", payload)
        self.assertNotIn("versions", payload)

    def test_scheme_paths_are_passed_through(self):
        ctx = self.make_ctx(envelope_text=envelope(errors=[]))
        writers.handle_validate_config({"rules": "config/r.json", "scoring": "config/s.json"},
                                       ctx)
        self.assertEqual(self.last_options(),
                         {"rules": "config/r.json", "scoring": "config/s.json"})

    def test_no_scheme_paths_means_no_options(self):
        ctx = self.make_ctx(envelope_text=envelope(errors=[]))
        writers.handle_validate_config({}, ctx)
        self.assertEqual(self.last_argv(), ["validate"])

    def test_invalid_scheme_fails_with_config_invalid_and_details(self):
        text = error_envelope(code=contract.CODE_CONFIG_INVALID, message="配置校验失败",
                              details=["rules.thresholds 越界"])
        ctx = self.make_ctx(envelope_text=text, exit_code=2)
        with self.assertRaises(StructuredError) as caught:
            writers.handle_validate_config({}, ctx)
        exc = caught.exception
        self.assertEqual(exc.code, contract.CODE_CONFIG_INVALID)
        self.assertEqual(exc.extra["details"], ["rules.thresholds 越界"])
        self.assertEqual(exc.extra["driver_code"], 2)
        self.assertIn("配置校验失败", exc.message)
        self.assertIn("details", exc.message)


class ListSchemesHandlerTest(FakeRepoCase):
    def test_returns_scheme_list_as_is(self):
        schemes = [{"scheme_id": "ml1m-cleaning-default", "type": "cleaning"}]
        ctx = self.make_ctx(envelope_text=envelope(schemes=schemes))
        self.assertEqual(writers.handle_list_schemes({}, ctx), {"schemes": schemes})
        self.assertEqual(self.last_argv(), ["schemes"])

    def test_empty_list_is_returned_not_filled(self):
        ctx = self.make_ctx(envelope_text=envelope(schemes=[]))
        self.assertEqual(writers.handle_list_schemes({}, ctx), {"schemes": []})

    def test_missing_list_is_a_protocol_problem(self):
        ctx = self.make_ctx(envelope_text=envelope())
        with self.assertRaises(StructuredError) as caught:
            writers.handle_list_schemes({}, ctx)
        self.assertEqual(caught.exception.code, contract.CODE_DRIVER_BAD_OUTPUT)


# ---------------------------------------------------------------------------
# start_cleaning_task / start_clean_task
# ---------------------------------------------------------------------------

START_OK = envelope(task_id="20261017-120000-abcdef", status="queued",
                    task_dir="/home/somebody/var/tasks/20261017-120000-abcdef",
                    started_at="2026-10-17T12:00:00Z")


class StartOptionsTest(FakeRepoCase):
    def test_full_run_maps_every_option(self):
        ctx = self.make_ctx(envelope_text=START_OK)
        writers.handle_start_cleaning_task({
            "scope": "full", "data_version": "ml1m-clean-v1", "tag": "演示",
            "force": False, "rules": "config/r.json", "scoring": "config/s.json",
        }, ctx)
        self.assertEqual(self.last_options(), {
            "scope": "full",
            "task-type": "full",
            "exec": "cluster",
            "data-version": "ml1m-clean-v1",
            "tag": "演示",
            "rules": "config/r.json",
            "scoring": "config/s.json",
        })

    def test_clean_run_uses_clean_task_type(self):
        ctx = self.make_ctx(envelope_text=START_OK)
        writers.handle_start_clean_task({"scope": "full"}, ctx)
        self.assertEqual(self.last_options(),
                         {"scope": "full", "task-type": "clean", "exec": "cluster"})

    def test_force_adds_the_flag_and_nothing_else(self):
        ctx = self.make_ctx(envelope_text=START_OK)
        writers.handle_start_cleaning_task({"scope": "sample", "force": True}, ctx)
        self.assertEqual(self.last_options(),
                         {"scope": "sample", "task-type": "full", "exec": "cluster",
                          "force": True})

    def test_option_like_tag_never_reaches_the_execution_side(self):
        """最严重的那条：取值里的开关语义必须在**到达执行侧之前**就被挡住。

        审查实测的后果是 `tag="--force"` 能绕过单任务互斥。现在这种取值在桥接层
        直接拒绝，因此执行侧一次都没被调用 —— 也就没有"绕过"这回事。
        """
        for dangerous in ("--force", "--exec", "--foreground"):
            with self.subTest(value=dangerous):
                ctx = self.make_ctx(envelope_text=START_OK)
                with self.assertRaises(StructuredError) as caught:
                    writers.handle_start_cleaning_task({"scope": "full", "tag": dangerous},
                                                       ctx)
                self.assertEqual(caught.exception.code, contract.CODE_USAGE)
                self.assertEqual(self.recorded_argv(), [],
                                 "被拒绝的请求不该已经在执行侧跑过一次")

    def test_exec_is_always_cluster(self):
        # 本机本地执行是排障手段，不对 Agent 开放：两条发起路径都必须钉住集群
        for handler in (writers.handle_start_cleaning_task, writers.handle_start_clean_task):
            ctx = self.make_ctx(envelope_text=START_OK)
            handler({"scope": "full"}, ctx)
            self.assertEqual(self.last_options()["exec"], "cluster")

    def test_foreground_is_never_requested(self):
        ctx = self.make_ctx(envelope_text=START_OK)
        writers.handle_start_cleaning_task({"scope": "full"}, ctx)
        self.assertNotIn("foreground", self.last_options())


class StartResultTest(FakeRepoCase):
    def test_returns_task_id_status_and_acceptance_time(self):
        ctx = self.make_ctx(envelope_text=START_OK)
        payload = writers.handle_start_cleaning_task({"scope": "full"}, ctx)
        self.assertEqual(payload["task_id"], "20261017-120000-abcdef")
        self.assertEqual(payload["status"], "queued")
        self.assertEqual(payload["started_at"], "2026-10-17T12:00:00Z")
        self.assertEqual(payload["task_type"], "full")
        self.assertEqual(payload["scope"], "full")

    def test_absolute_task_dir_is_not_exposed(self):
        ctx = self.make_ctx(envelope_text=START_OK)
        payload = writers.handle_start_cleaning_task({"scope": "full"}, ctx)
        self.assertNotIn("task_dir", payload)
        self.assertNotIn("/home/somebody", json.dumps(payload, ensure_ascii=False))

    def test_no_progress_or_duration_is_estimated(self):
        # 受理阶段只回「已受理」：进度只能来自真实状态，不在这一层估
        ctx = self.make_ctx(envelope_text=START_OK)
        payload = writers.handle_start_cleaning_task({"scope": "full"}, ctx)
        for key in ("progress_percent", "stage", "counts", "scores"):
            self.assertNotIn(key, payload)

    def test_estimated_seconds_comes_from_the_tool_table_and_is_labelled(self):
        # 预计耗时是"给对端挂起展示用的参考值"，必须与实测区分开
        ctx = self.make_ctx(envelope_text=START_OK)
        payload = writers.handle_start_cleaning_task({"scope": "full"}, ctx)
        self.assertEqual(payload["estimated_seconds"], 1500)
        self.assertIn("不是本次实测", payload["estimated_seconds_note"])
        self.assertEqual(payload["estimated_seconds"],
                         mcpserver_tool("start_cleaning_task")["estimated_seconds"])

    def test_clean_task_carries_its_own_estimate(self):
        ctx = self.make_ctx(envelope_text=START_OK)
        payload = writers.handle_start_clean_task({"scope": "full"}, ctx)
        self.assertEqual(payload["estimated_seconds"],
                         mcpserver_tool("start_clean_task")["estimated_seconds"])

    def test_requested_data_version_is_echoed(self):
        ctx = self.make_ctx(envelope_text=START_OK)
        payload = writers.handle_start_cleaning_task(
            {"scope": "full", "data_version": "ml1m-clean-v1"}, ctx)
        self.assertEqual(payload["data_version"], "ml1m-clean-v1")

    def test_force_success_carries_a_risk_note(self):
        ctx = self.make_ctx(envelope_text=START_OK)
        payload = writers.handle_start_cleaning_task({"scope": "full", "force": True}, ctx)
        self.assertIn("warnings", payload)
        self.assertIn("强制开关", payload["warnings"][0])

    def test_plain_success_has_no_warning(self):
        ctx = self.make_ctx(envelope_text=START_OK)
        payload = writers.handle_start_cleaning_task({"scope": "full"}, ctx)
        self.assertNotIn("warnings", payload)

    def test_effective_values_come_from_the_status_file(self):
        """取向以**真实生效**的那一份为准：用户没写版本时，用的是方案声明的那一版。

        状态文件里记着执行侧真正采用的范围、版本与任务类型，理应优先于"请求里写了什么"。
        """
        ctx = self.make_ctx(envelope_text=START_OK)
        directory = os.path.join(self.var_dir, "tasks", "20261017-120000-abcdef")
        os.makedirs(directory)
        self._write(os.path.join(directory, "status.json"), json.dumps({
            "task_id": "20261017-120000-abcdef", "status": "queued",
            "scope": "sample", "data_version": "ml1m-clean-v9",
            "task_type": "clean"}, ensure_ascii=False))
        payload = writers.handle_start_cleaning_task(
            {"scope": "full", "data_version": "ml1m-clean-v1"}, ctx)
        self.assertEqual(payload["scope"], "sample")
        self.assertEqual(payload["data_version"], "ml1m-clean-v9")
        self.assertEqual(payload["task_type"], "clean")

    def test_effective_values_fall_back_to_the_normalized_request(self):
        """状态文件还没落盘时，回退到分派层归一后的请求值（绝不回未校验的原始输入）。"""
        ctx = self.make_ctx(envelope_text=START_OK)
        payload = writers.handle_start_cleaning_task(
            {"scope": "sample", "data_version": "ml1m-clean-v1"}, ctx)
        self.assertEqual(payload["scope"], "sample")
        self.assertEqual(payload["data_version"], "ml1m-clean-v1")
        self.assertEqual(payload["task_type"], "full")

    def test_missing_task_id_is_a_protocol_problem(self):
        ctx = self.make_ctx(envelope_text=envelope(status="queued"))
        with self.assertRaises(StructuredError) as caught:
            writers.handle_start_cleaning_task({"scope": "full"}, ctx)
        self.assertEqual(caught.exception.code, contract.CODE_DRIVER_BAD_OUTPUT)


class StartFailureTest(FakeRepoCase):
    def test_busy_without_force_names_the_occupier_and_the_way_out(self):
        text = error_envelope(code=contract.CODE_TASK_ALREADY_RUNNING,
                              message="已有运行中的任务：20261017-aaa（如需并行请加 --force）",
                              task_id="20261017-aaa")
        ctx = self.make_ctx(envelope_text=text, exit_code=2)
        with self.assertRaises(StructuredError) as caught:
            writers.handle_start_cleaning_task({"scope": "full", "force": False}, ctx)
        exc = caught.exception
        self.assertEqual(exc.code, contract.CODE_TASK_ALREADY_RUNNING)
        self.assertEqual(exc.extra["occupier"], "20261017-aaa")
        self.assertFalse(exc.extra["force"])
        self.assertTrue(exc.extra["force_supported"])
        self.assertIn("20261017-aaa", exc.message)
        self.assertIn("强制开关", exc.extra["remedy"])
        # driver 的原话必须保留，不能被我方措辞覆盖
        self.assertIn("已有运行中的任务", exc.message)

    def test_version_conflict_explains_both_sides_and_the_fix(self):
        text = error_envelope(code=contract.CODE_VERSION_CONFLICT,
                              message="请求的 data_version=ml1m-clean-v2 与配置声明的 "
                                      "ml1m-clean-v1 不一致")
        ctx = self.make_ctx(envelope_text=text, exit_code=6)
        with self.assertRaises(StructuredError) as caught:
            writers.handle_start_cleaning_task(
                {"scope": "full", "data_version": "ml1m-clean-v2"}, ctx)
        exc = caught.exception
        self.assertEqual(exc.code, contract.CODE_VERSION_CONFLICT)
        self.assertEqual(exc.extra["requested_data_version"], "ml1m-clean-v2")
        self.assertIn("ml1m-clean-v2", exc.message)
        self.assertIn("ml1m-clean-v1", exc.message)
        self.assertIn("升数据版本", exc.extra["remedy"])

    def test_clean_rejection_by_driver_is_passed_through_verbatim(self):
        # driver 对「只清洗 + 非集群执行」会直接拒绝；这类结论一律如实透传
        driver_message = "--task-type clean 仅支持集群模式（--exec cluster）"
        text = error_envelope(code=contract.CODE_USAGE, message=driver_message)
        ctx = self.make_ctx(envelope_text=text, exit_code=2)
        with self.assertRaises(StructuredError) as caught:
            writers.handle_start_clean_task({"scope": "full"}, ctx)
        exc = caught.exception
        self.assertEqual(exc.code, contract.CODE_USAGE)
        self.assertEqual(exc.message, driver_message)

    def test_unknown_failure_keeps_driver_message(self):
        text = error_envelope(message="任务目录创建失败")
        ctx = self.make_ctx(envelope_text=text, exit_code=3)
        with self.assertRaises(StructuredError) as caught:
            writers.handle_start_cleaning_task({"scope": "full"}, ctx)
        self.assertEqual(caught.exception.code, contract.CODE_TASK_FAILED)
        self.assertEqual(caught.exception.message, "任务目录创建失败")


# ---------------------------------------------------------------------------
# score_task
# ---------------------------------------------------------------------------

SCORE_OK = envelope(task_id="20261017-130000-fedcba", status="queued",
                    task_type="score", source="task",
                    started_at="2026-10-17T13:00:00Z")


class ScoreOptionsTest(FakeRepoCase):
    def test_task_source_maps_from_task(self):
        ctx = self.make_ctx(envelope_text=SCORE_OK)
        self.make_task("20261017-aaa")
        writers.handle_score_task({"source": "task", "from_task": "20261017-aaa",
                                   "scoring": "config/s.json", "tag": "回看"},
                                 ctx)
        self.assertEqual(self.last_options(), {"source": "task",
                                               "from-task": "20261017-aaa",
                                               "scoring": "config/s.json",
                                               "tag": "回看"})

    def test_raw_source_sends_only_the_source(self):
        ctx = self.make_ctx(envelope_text=envelope(task_id="t", status="queued"))
        writers.handle_score_task({"source": "raw"}, ctx)
        self.assertEqual(self.last_options(), {"source": "raw"})

    def test_published_source_is_passed_through(self):
        ctx = self.make_ctx(envelope_text=envelope(task_id="t", status="queued"))
        writers.handle_score_task({"source": "published"}, ctx)
        self.assertEqual(self.last_options(), {"source": "published"})

    def test_force_is_not_a_score_parameter_any_more(self):
        """评分没有强制开关：分派层会直接拒绝，所以"静默忽略"那条老逻辑不存在了。"""
        ctx = self.make_ctx(envelope_text=SCORE_OK)
        self.register_writers()
        with self.assertRaises(StructuredError) as caught:
            dispatch.validate_args(mcpserver_tool("score_task"),
                                   {"source": "raw", "force": True})
        self.assertEqual(caught.exception.code, contract.CODE_USAGE)
        self.assertIn("force", str(caught.exception))
        self.assertEqual(self.recorded_argv(), [],
                         "参数不合法的请求不该被送到执行侧")

    def test_foreground_is_never_requested(self):
        ctx = self.make_ctx(envelope_text=SCORE_OK)
        writers.handle_score_task({"source": "raw"}, ctx)
        self.assertNotIn("foreground", self.last_options())


class ScoreResultTest(FakeRepoCase):
    def test_returns_task_identity_and_source(self):
        ctx = self.make_ctx(envelope_text=SCORE_OK)
        self.make_task("20261017-aaa")
        payload = writers.handle_score_task({"source": "task", "from_task": "20261017-aaa"},
                                            ctx)
        self.assertEqual(payload["task_id"], "20261017-130000-fedcba")
        self.assertEqual(payload["status"], "queued")
        self.assertEqual(payload["started_at"], "2026-10-17T13:00:00Z")
        self.assertEqual(payload["task_type"], "score")
        self.assertEqual(payload["source"], "task")
        self.assertEqual(payload["from_task"], "20261017-aaa")
        self.assertNotIn("task_dir", payload)

    def test_source_echoes_the_request_not_a_stale_driver_value(self):
        """执行侧回传的来源与请求不一致时，取向必须以请求为准，并如实提示。

        这是审查点出的错位：如果照抄回传值，"请求了已发布、返回里却写原始"就会被当成
        事实包装出去。这里造一个回传错位的信封，断言返回取自请求、并且有提示。
        """
        mismatched = envelope(task_id="20261017-130000-fedcba", status="queued",
                              task_type="score", source="raw",
                              started_at="2026-10-17T13:00:00Z")
        ctx = self.make_ctx(envelope_text=mismatched)
        payload = writers.handle_score_task({"source": "published"}, ctx)
        self.assertEqual(payload["source"], "published")
        self.assertIn("warnings", payload)
        self.assertIn("不一致", payload["warnings"][0])
        self.assertIn("published", payload["warnings"][0])

    def test_task_type_echo_mismatch_is_flagged(self):
        mismatched = envelope(task_id="t", status="queued", task_type="full",
                              source="raw", started_at="2026-10-17T13:00:00Z")
        ctx = self.make_ctx(envelope_text=mismatched)
        payload = writers.handle_score_task({"source": "raw"}, ctx)
        self.assertEqual(payload["task_type"], "score")
        self.assertTrue(any("任务类型" in item for item in payload["warnings"]))

    def test_estimate_is_present_but_progress_is_not(self):
        ctx = self.make_ctx(envelope_text=SCORE_OK)
        payload = writers.handle_score_task({"source": "raw"}, ctx)
        self.assertEqual(payload["estimated_seconds"],
                         mcpserver_tool("score_task")["estimated_seconds"])
        self.assertIn("不是本次实测", payload["estimated_seconds_note"])
        for key in ("progress_percent", "scores", "counts"):
            self.assertNotIn(key, payload)


class ScoreFailureTest(FakeRepoCase):
    def test_dependency_missing_is_passed_through_with_remedy(self):
        driver_message = "目标任务未成功（当前 failed），不能评分：20261017-aaa"
        text = error_envelope(code=contract.CODE_DEPENDENCY_MISSING,
                              message=driver_message)
        ctx = self.make_ctx(envelope_text=text, exit_code=2)
        self.make_task("20261017-aaa")          # 前置检查通过，交回 driver 兜底判定
        with self.assertRaises(StructuredError) as caught:
            writers.handle_score_task({"source": "task", "from_task": "20261017-aaa"}, ctx)
        exc = caught.exception
        self.assertEqual(exc.code, contract.CODE_DEPENDENCY_MISSING)
        self.assertIn(driver_message, exc.message)
        self.assertEqual(exc.extra["source"], "task")
        self.assertEqual(exc.extra["from_task"], "20261017-aaa")
        self.assertIn("来源任务", exc.extra["remedy"])

    def test_published_dependency_hint_matches_the_source(self):
        text = error_envelope(code=contract.CODE_DEPENDENCY_MISSING,
                              message="发布区不存在（/data/published/x）")
        ctx = self.make_ctx(envelope_text=text, exit_code=2)
        with self.assertRaises(StructuredError) as caught:
            writers.handle_score_task({"source": "published"}, ctx)
        self.assertIn("发布区", caught.exception.extra["remedy"])

    def test_busy_says_the_switch_does_not_exist_here(self):
        text = error_envelope(code=contract.CODE_TASK_ALREADY_RUNNING,
                              message="已有运行中的任务：20261017-aaa（score 也占用任务锁）",
                              task_id="20261017-aaa")
        ctx = self.make_ctx(envelope_text=text, exit_code=2)
        with self.assertRaises(StructuredError) as caught:
            writers.handle_score_task({"source": "raw"}, ctx)
        exc = caught.exception
        self.assertEqual(exc.code, contract.CODE_TASK_ALREADY_RUNNING)
        self.assertEqual(exc.extra["occupier"], "20261017-aaa")
        self.assertFalse(exc.extra["force_supported"])
        self.assertIn("没有提供", exc.extra["remedy"])

    def test_busy_score_says_there_is_no_bypass(self):
        # 评分没有强制开关（工具表里已删掉这个参数），因此只能说"等占用者结束"
        text = error_envelope(code=contract.CODE_TASK_ALREADY_RUNNING,
                              message="已有运行中的任务：20261017-aaa", task_id="20261017-aaa")
        ctx = self.make_ctx(envelope_text=text, exit_code=2)
        with self.assertRaises(StructuredError) as caught:
            writers.handle_score_task({"source": "raw"}, ctx)
        exc = caught.exception
        self.assertFalse(exc.extra["force"])
        self.assertFalse(exc.extra["force_supported"])
        self.assertEqual(exc.extra["occupier"], "20261017-aaa")
        self.assertIn("没有提供绕过它的开关", exc.extra["remedy"])


# ---------------------------------------------------------------------------
# 指定任务评分的前置检查（提交前同步判定）
# ---------------------------------------------------------------------------

class ScoreTaskPrecheckTest(FakeRepoCase):
    """来源为「指定任务」时，依赖不满足必须在**提交前**就给出结论。

    现象：以前是本端先回「已受理、排队」，几秒后任务变失败、再补一句「其实失败了」——
    信息不假，但用户要听两遍，中间还白等一轮。检查只读任务目录，结论可靠，所以提前判定。
    """

    SOURCE_TASK_ID = FakeRepoCase.READY_TASK_ID

    def score(self, ctx, source="task", from_task=None):
        return writers.handle_score_task(
            {"source": source, "from_task": from_task or self.SOURCE_TASK_ID}, ctx)

    # ---- 防御性形状校验（正常调用走不到：分派层先拦） ----

    def test_path_like_from_task_is_defensive_rejected(self):
        # 分派层会先把这类取值拦掉；这里直接调处理器，验证它自己也不会拼出越界路径
        ctx = self.make_ctx(envelope_text=SCORE_OK)
        for bad in ("../other", "a/b", "/abs", "..", "x" * 80):
            with self.subTest(value=bad):
                with self.assertRaises(StructuredError) as caught:
                    writers.handle_score_task({"source": "task", "from_task": bad}, ctx)
                self.assertEqual(caught.exception.code, contract.CODE_FORBIDDEN_PATH)
                self.assertEqual(caught.exception.extra["missing"], "task_id_shape")
        self.assertEqual(self.recorded_argv(), [], "形状不合法时不该去提交")

    def test_empty_from_task_is_reported_as_missing(self):
        ctx = self.make_ctx(envelope_text=SCORE_OK)
        with self.assertRaises(StructuredError) as caught:
            writers.handle_score_task({"source": "task", "from_task": ""}, ctx)
        self.assertEqual(caught.exception.code, contract.CODE_DEPENDENCY_MISSING)
        self.assertEqual(caught.exception.extra["missing"], "task")

    def test_path_like_data_version_in_status_is_rejected(self):
        """状态文件里的数据版本会被拼进 cleaning 目录，形状不合法就不能用来定位产物。"""
        ctx = self.make_ctx(envelope_text=SCORE_OK)
        self.make_task(data_version="../../escape")
        with self.assertRaises(StructuredError) as caught:
            self.score(ctx)
        exc = caught.exception
        self.assertEqual(exc.code, contract.CODE_DEPENDENCY_MISSING)
        self.assertEqual(exc.extra["missing"], "data_version_shape")
        self.assertEqual(self.recorded_argv(), [])

    # ---- 三条阻塞条件 ----

    def test_missing_target_task_is_reported_before_submitting(self):
        ctx = self.make_ctx(envelope_text=SCORE_OK)
        with self.assertRaises(StructuredError) as caught:
            self.score(ctx, from_task="no-such-task")
        exc = caught.exception
        self.assertEqual(exc.code, contract.CODE_DEPENDENCY_MISSING)
        self.assertIn("不存在", exc.message)
        self.assertIn("no-such-task", exc.message)
        self.assertEqual(exc.extra["missing"], "task")
        self.assertTrue(exc.extra["remedy"])
        self.assertEqual(self.recorded_argv(), [],
                         "依赖不满足时不该真的去提交评分任务")

    def test_failed_target_task_shows_its_current_status(self):
        ctx = self.make_ctx(envelope_text=SCORE_OK)
        self.make_task(status="failed")
        with self.assertRaises(StructuredError) as caught:
            self.score(ctx)
        exc = caught.exception
        self.assertEqual(exc.code, contract.CODE_DEPENDENCY_MISSING)
        self.assertIn("失败", exc.message)
        self.assertIn("failed", exc.message)
        self.assertEqual(exc.extra["missing"], "task_not_succeeded")
        self.assertEqual(exc.extra["status"], "failed")
        self.assertEqual(self.recorded_argv(), [])

    def test_running_target_task_is_also_blocked(self):
        ctx = self.make_ctx(envelope_text=SCORE_OK)
        self.make_task(status="running")
        with self.assertRaises(StructuredError) as caught:
            self.score(ctx)
        self.assertIn("运行", caught.exception.message)
        self.assertEqual(caught.exception.extra["status"], "running")
        self.assertEqual(self.recorded_argv(), [])

    def test_missing_cleaned_products_are_named(self):
        ctx = self.make_ctx(envelope_text=SCORE_OK)
        self.make_task(cleaned=["users.dat", "movies.dat"])
        with self.assertRaises(StructuredError) as caught:
            self.score(ctx)
        exc = caught.exception
        self.assertEqual(exc.code, contract.CODE_DEPENDENCY_MISSING)
        self.assertEqual(exc.extra["missing"], "cleaned_products")
        self.assertEqual(exc.extra["missing_files"], ["ratings.dat"])
        self.assertIn("ratings.dat", exc.message)
        self.assertEqual(self.recorded_argv(), [])

    def test_outdated_data_version_directory_counts_as_missing(self):
        # 状态文件写的版本与目录里的版本对不上：产物按状态里的版本找，找不到就算缺
        ctx = self.make_ctx(envelope_text=SCORE_OK)
        self.make_task(data_version="ml1m-clean-v1")
        status_path = os.path.join(self.var_dir, "tasks", self.SOURCE_TASK_ID, "status.json")
        self._write(status_path, json.dumps({"task_id": self.SOURCE_TASK_ID,
                                             "status": "succeeded",
                                             "data_version": "ml1m-clean-v9"}))
        with self.assertRaises(StructuredError) as caught:
            self.score(ctx)
        self.assertEqual(caught.exception.extra["missing"], "cleaned_products")
        self.assertEqual(caught.exception.extra["relative_dir"],
                         os.path.join("cleaned", "ml1m-clean-v9"))

    def test_unreadable_status_file_is_not_guessed(self):
        ctx = self.make_ctx(envelope_text=SCORE_OK)
        self.make_task(status_text="{ 这不是合法 JSON")
        with self.assertRaises(StructuredError) as caught:
            self.score(ctx)
        exc = caught.exception
        self.assertEqual(exc.code, contract.CODE_DEPENDENCY_MISSING)
        self.assertEqual(exc.extra["missing"], "task_status_unreadable")
        self.assertEqual(self.recorded_argv(), [])

    # ---- 正常情况照旧受理 ----

    def test_ready_task_is_submitted_normally(self):
        ctx = self.make_ctx(envelope_text=SCORE_OK)
        self.make_task()
        payload = self.score(ctx)
        self.assertEqual(payload["task_id"], "20261017-130000-fedcba")
        self.assertEqual(self.last_options(),
                         {"source": "task", "from-task": self.SOURCE_TASK_ID})

    def test_ready_task_still_leaves_the_submit_marker(self):
        ctx = self.make_ctx(envelope_text=SCORE_OK)
        self.make_task()
        self.score(ctx)
        self.assertIsNotNone(self.read_marker("20261017-130000-fedcba"))

    def test_driver_dependency_error_still_passes_through_after_precheck(self):
        # 前置检查通过、driver 仍报依赖缺失（竞态：检查之后任务被清理）→ 照样如实透传
        ctx = self.make_ctx(envelope_text=error_envelope(
            code=contract.CODE_DEPENDENCY_MISSING,
            message="目标任务缺少维表产物（dims/）：20261017-090000-clean0"), exit_code=2)
        self.make_task()
        with self.assertRaises(StructuredError) as caught:
            self.score(ctx)
        exc = caught.exception
        self.assertEqual(exc.code, contract.CODE_DEPENDENCY_MISSING)
        self.assertIn("维表产物", exc.message)
        self.assertTrue(self.recorded_argv(), "前置检查通过后应该照常提交，由 driver 兜底判定")

    # ---- 边界：另外两条来源不加前置检查 ----

    def test_published_source_is_still_judged_by_the_driver(self):
        # 已发布要问集群，集群没起来时不能把「连不上」说成「依赖缺失」，所以不预判
        ctx = self.make_ctx(envelope_text=error_envelope(
            code=contract.CODE_DEPENDENCY_MISSING,
            message="发布区不存在（/data/published/x）"), exit_code=2)
        with self.assertRaises(StructuredError) as caught:
            writers.handle_score_task({"source": "published"}, ctx)
        self.assertEqual(caught.exception.code, contract.CODE_DEPENDENCY_MISSING)
        self.assertEqual(self.last_options(), {"source": "published"},
                         "已发布这条必须照常提交，结论由 driver 给")

    def test_raw_source_is_still_judged_by_the_driver(self):
        ctx = self.make_ctx(envelope_text=envelope(task_id="t-raw", status="queued"))
        writers.handle_score_task({"source": "raw"}, ctx)
        self.assertEqual(self.last_options(), {"source": "raw"})

    def test_published_source_does_not_look_at_local_task_dirs(self):
        # 已发布与来源任务没关系：任务目录一概不存在时也不该被本端拦下
        ctx = self.make_ctx(envelope_text=envelope(task_id="t-pub", status="queued"))
        payload = writers.handle_score_task({"source": "published"}, ctx)
        self.assertEqual(payload["task_id"], "t-pub")
        self.assertTrue(self.recorded_argv())

    # ---- 与执行层的常量对齐（本段手写了三份表文件名）----

    def test_cleaned_file_names_match_driver(self):
        from engine.pipeline import TABLE_FILES
        self.assertEqual(dict(writers.CLEANED_TABLE_FILES), TABLE_FILES)

    def test_source_constants_match_driver(self):
        from driver.commands import ScoreCommand
        self.assertEqual((writers.SOURCE_RAW, writers.SOURCE_PUBLISHED, writers.SOURCE_TASK),
                         ScoreCommand.spec["source"]["values"])


# ---------------------------------------------------------------------------
# 「本次任务由本服务提交」的标记
# ---------------------------------------------------------------------------

class SubmitMarkerTest(FakeRepoCase):
    """受理成功后要留下凭据：报告里写清是哪个能力、什么时候、按什么取向提交的。

    这份凭据是中止能力判断「这台机器上的任务是不是本服务起的」的唯一依据 ——
    没有它，遇到「状态写着在跑、进程却已经没了」只能保守拒绝，执行位就被挂住。
    """

    START_TASK = "20261017-120000-abcdef"
    SCORE_TASK = "20261017-130000-fedcba"

    def assert_marker_shape(self, marker, task_id):
        self.assertEqual(marker["submitted_by"], writers.SUBMIT_BY)
        self.assertEqual(marker["task_id"], task_id)
        self.assertTrue(marker["tool"])
        stamp = marker["submitted_at"]
        self.assertEqual(len(stamp), 20, stamp)
        self.assertIn("T", stamp)
        self.assertTrue(stamp.endswith("Z"), stamp)

    def test_full_run_leaves_a_marker_next_to_the_task(self):
        ctx = self.make_ctx(envelope_text=START_OK)
        writers.handle_start_cleaning_task({"scope": "full"}, ctx)
        marker = self.read_marker(self.START_TASK)
        self.assertIsNotNone(marker, "受理成功后没有留下提交凭据")
        self.assert_marker_shape(marker, self.START_TASK)
        self.assertEqual(marker["tool"], "start_cleaning_task")
        self.assertEqual(marker["task_type"], "full")
        self.assertEqual(marker["scope"], "full")

    def test_clean_run_records_its_own_tool_and_task_type(self):
        ctx = self.make_ctx(envelope_text=START_OK)
        writers.handle_start_clean_task({"scope": "sample"}, ctx)
        marker = self.read_marker(self.START_TASK)
        self.assert_marker_shape(marker, self.START_TASK)
        self.assertEqual(marker["tool"], "start_clean_task")
        self.assertEqual(marker["task_type"], "clean")
        self.assertEqual(marker["scope"], "sample")

    def test_scoring_records_source_and_from_task(self):
        ctx = self.make_ctx(envelope_text=SCORE_OK)
        self.make_task("20261017-aaa")
        writers.handle_score_task({"source": "task", "from_task": "20261017-aaa"}, ctx)
        marker = self.read_marker(self.SCORE_TASK)
        self.assert_marker_shape(marker, self.SCORE_TASK)
        self.assertEqual(marker["tool"], "score_task")
        self.assertEqual(marker["task_type"], "score")
        self.assertEqual(marker["source"], "task")
        self.assertEqual(marker["from_task"], "20261017-aaa")

    def test_marker_lands_where_the_driver_puts_the_task(self):
        # 标记必须落在 driver 用的那个产物目录下，否则中止能力在那边看不到它
        other = os.path.join(self.repo, "another-var")
        ctx = self.make_ctx(envelope_text=START_OK, var_dir=other)
        writers.handle_start_cleaning_task({"scope": "full"}, ctx)
        expected = os.path.join(other, "tasks", self.START_TASK, writers.SUBMIT_MARKER)
        self.assertTrue(os.path.isfile(expected), "标记没落在解析出的产物目录下")
        self.assertFalse(os.path.isfile(self.marker_path(self.START_TASK)))

    def test_falls_back_to_repo_var_when_no_variable_is_set(self):
        # 一处都没配时，driver 会落在仓库根的 var 下，标记必须跟着一起去
        with mock.patch.dict(os.environ, {"ML_VAR_DIR": ""}):
            ctx = self.make_ctx(envelope_text=START_OK, var_dir=None)
            writers.handle_start_cleaning_task({"scope": "full"}, ctx)
        expected = os.path.join(self.repo, "var", "tasks", self.START_TASK,
                                writers.SUBMIT_MARKER)
        self.assertTrue(os.path.isfile(expected), "标记没有跟随 driver 的默认产物目录")

    def test_marker_has_no_temporary_leftover(self):
        ctx = self.make_ctx(envelope_text=START_OK)
        writers.handle_start_cleaning_task({"scope": "full"}, ctx)
        directory = os.path.dirname(self.marker_path(self.START_TASK))
        self.assertEqual(sorted(os.listdir(directory)), [writers.SUBMIT_MARKER])

    def test_rejected_submission_leaves_no_marker(self):
        # 没被受理就没有任务目录，也不该有「本服务提交」的凭据
        text = error_envelope(code=contract.CODE_TASK_ALREADY_RUNNING,
                              message="已有运行中的任务：20261017-aaa", task_id="20261017-aaa")
        ctx = self.make_ctx(envelope_text=text, exit_code=2)
        with self.assertRaises(StructuredError):
            writers.handle_start_cleaning_task({"scope": "full"}, ctx)
        self.assertIsNone(self.read_marker(self.START_TASK))

    def test_write_failure_does_not_change_the_acceptance_result(self):
        # 把产物目录的位置先占成一个普通文件：标记必然写不进去
        os.makedirs(self.var_dir)
        with open(os.path.join(self.var_dir, "tasks"), "w", encoding="utf-8") as handle:
            handle.write("占位")
        ctx = self.make_ctx(envelope_text=START_OK)
        with contextlib.redirect_stderr(io.StringIO()):
            payload = writers.handle_start_cleaning_task({"scope": "full"}, ctx)
        self.assertEqual(payload["task_id"], self.START_TASK)
        self.assertEqual(payload["status"], "queued")
        self.assertIsNone(self.read_marker(self.START_TASK))

    def test_marker_writer_reports_failure_without_raising(self):
        os.makedirs(self.var_dir)
        with open(os.path.join(self.var_dir, "tasks"), "w", encoding="utf-8") as handle:
            handle.write("占位")
        ctx = self.make_ctx()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(writers.write_submit_marker("20261017-zzz", ctx, "score_task"))

    def test_marker_writer_returns_the_path_it_wrote(self):
        ctx = self.make_ctx()
        path = writers.write_submit_marker("20261017-yyy", ctx, "start_clean_task",
                                           orientation={"task_type": "clean"})
        self.assertEqual(path, self.marker_path("20261017-yyy"))
        with open(path, "r", encoding="utf-8") as handle:
            marker = json.load(handle)
        self.assertEqual(marker["tool"], "start_clean_task")
        self.assertEqual(marker["task_type"], "clean")

    def test_empty_orientation_values_are_not_written(self):
        ctx = self.make_ctx()
        path = writers.write_submit_marker("20261017-xxx", ctx, "score_task",
                                           orientation={"source": None, "from_task": ""})
        with open(path, "r", encoding="utf-8") as handle:
            marker = json.load(handle)
        self.assertNotIn("source", marker)
        self.assertNotIn("from_task", marker)


# ---------------------------------------------------------------------------
# 经分派层的整条链（信封由分派层包，处理器不自造外壳）
# ---------------------------------------------------------------------------

class DispatchIntegrationTest(FakeRepoCase):
    def test_success_is_wrapped_with_version(self):
        self.register_writers()
        ctx = self.make_ctx(envelope_text=START_OK)
        env = dispatch.call_tool("start_cleaning_task", {"scope": "full"}, ctx)
        self.assertTrue(env["ok"])
        self.assertEqual(env["interface_version"], contract.INTERFACE_VERSION)
        self.assertEqual(env["task_id"], "20261017-120000-abcdef")

    def test_failure_envelope_carries_code_and_locating_info(self):
        self.register_writers()
        text = error_envelope(code=contract.CODE_TASK_ALREADY_RUNNING,
                              message="已有运行中的任务：20261017-aaa", task_id="20261017-aaa")
        ctx = self.make_ctx(envelope_text=text, exit_code=2)
        env = dispatch.call_tool("start_clean_task", {"scope": "full"}, ctx)
        self.assertFalse(env["ok"])
        self.assertEqual(env["error"]["code"], contract.CODE_TASK_ALREADY_RUNNING)
        self.assertEqual(env["error"]["occupier"], "20261017-aaa")
        self.assertEqual(env["error"]["driver_code"], 2)

    def test_transport_failure_reaches_the_caller_as_its_own_code(self):
        self.register_writers()
        ctx = self.make_ctx(mode="empty", exit_code=1, stderr="启动就失败了")
        env = dispatch.call_tool("validate_config", {}, ctx)
        self.assertFalse(env["ok"])
        self.assertEqual(env["error"]["code"], contract.CODE_DRIVER_NO_OUTPUT)

    def test_defaults_are_applied_before_the_handler_runs(self):
        self.register_writers()
        ctx = self.make_ctx(envelope_text=START_OK)
        env = dispatch.call_tool("start_cleaning_task", {"tag": ""}, ctx)
        self.assertTrue(env["ok"])
        self.assertEqual(env["scope"], "full")
        self.assertNotIn("--tag", self.last_argv())

    def test_score_task_requires_from_task_before_calling_driver(self):
        self.register_writers()
        ctx = self.make_ctx(envelope_text=SCORE_OK)
        env = dispatch.call_tool("score_task", {"source": "task"}, ctx)
        self.assertFalse(env["ok"])
        self.assertEqual(env["error"]["code"], contract.CODE_USAGE)
        self.assertEqual(self.recorded_argv(), [], "参数不合法时不该真的去调 driver")

    def test_missing_source_task_is_rejected_at_request_time(self):
        # 全量验收里报的那个现象：以前是先回「已受理、排队」，几秒后再报失败。
        # 现在必须是这一次调用就拿到依赖缺失，且压根没提交过评分任务。
        self.register_writers()
        ctx = self.make_ctx(envelope_text=SCORE_OK)
        env = dispatch.call_tool("score_task",
                                 {"source": "task", "from_task": "no-such-task"}, ctx)
        self.assertFalse(env["ok"])
        self.assertEqual(env["error"]["code"], contract.CODE_DEPENDENCY_MISSING)
        self.assertIn("no-such-task", env["error"]["message"])
        self.assertEqual(env["error"]["missing"], "task")
        self.assertEqual(self.recorded_argv(), [], "依赖不满足时不该先受理再报失败")

    def test_ready_source_task_is_accepted_through_dispatch(self):
        self.register_writers()
        ctx = self.make_ctx(envelope_text=SCORE_OK)
        self.make_task(self.READY_TASK_ID)
        env = dispatch.call_tool("score_task",
                                 {"source": "task", "from_task": self.READY_TASK_ID}, ctx)
        self.assertTrue(env["ok"], env)
        self.assertEqual(env["source"], "task")
        self.assertEqual(env["status"], "queued")


# ---------------------------------------------------------------------------
# 上传自定义方案（端侧要求 §22）
# ---------------------------------------------------------------------------

class UploadSchemeTest(FakeRepoCase):
    """先校验后落盘：失败路径不留半成品，默认方案永不被覆盖。"""

    VALIDATE_OK = envelope(errors=[], warnings=[], versions={"rule": "2.0.0"})

    def cleaning_text(self, **over):
        payload = {
            "config_type": "cleaning_rules",
            "scheme_id": "my-cleaning-custom",
            "version": "2.0.0",
            "status": "custom",
            "description": "自定义清洗规则",
        }
        payload.update(over)
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def scoring_text(self, **over):
        payload = {
            "config_type": "scoring_scheme",
            "scheme_id": "my-quality-custom",
            "version": "2.0.0",
        }
        payload.update(over)
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def config_dir(self):
        return os.path.join(self.repo, "config")

    def config_files(self):
        directory = self.config_dir()
        if not os.path.isdir(directory):
            return []
        return sorted(os.listdir(directory))

    def read_text(self, name):
        with open(os.path.join(self.config_dir(), name), "r", encoding="utf-8") as handle:
            return handle.read()

    def write_scheme(self, name, text):
        os.makedirs(self.config_dir(), exist_ok=True)
        with open(os.path.join(self.config_dir(), name), "w", encoding="utf-8") as handle:
            handle.write(text)

    def upload(self, ctx, **kwargs):
        args = {"name": kwargs.pop("name", "my_rules.v2.json"),
                "content": kwargs.pop("content", self.cleaning_text())}
        args.update(kwargs)
        return writers.handle_upload_scheme(args, ctx)

    # ---- 正常路径 ----

    def test_valid_upload_lands_and_returns_a_list_aligned_entry(self):
        ctx = self.make_ctx(envelope_text=self.VALIDATE_OK)
        payload = self.upload(ctx)
        scheme = payload["scheme"]
        # 字段与「列方案」的条目对齐
        self.assertEqual(sorted(scheme),
                         ["description", "path", "scheme_id", "status", "type", "version"])
        self.assertEqual(scheme["scheme_id"], "my-cleaning-custom")
        self.assertEqual(scheme["type"], "cleaning_rules")
        self.assertEqual(scheme["version"], "2.0.0")
        self.assertEqual(scheme["status"], "custom")
        self.assertEqual(scheme["path"], "config/my_rules.v2.json")
        self.assertEqual(payload["validation"], {"passed": True, "errors": [], "warnings": []})
        self.assertFalse(payload["overwritten"])
        self.assertIn(payload["size_bytes"], (len(self.cleaning_text().encode("utf-8")),))
        # 真的落盘，内容与上传一致
        self.assertEqual(self.config_files(), ["my_rules.v2.json"])
        self.assertEqual(self.read_text("my_rules.v2.json"), self.cleaning_text())

    def test_no_absolute_path_is_exposed(self):
        ctx = self.make_ctx(envelope_text=self.VALIDATE_OK)
        payload = self.upload(ctx)
        dumped = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn(self.repo, dumped)
        self.assertNotIn(os.sep + "config" + os.sep, dumped)

    def test_no_temp_file_is_left_behind(self):
        ctx = self.make_ctx(envelope_text=self.VALIDATE_OK)
        self.upload(ctx)
        self.assertEqual([n for n in self.config_files()
                          if n.startswith(writers.SCHEME_TEMP_PREFIX)], [])

    def test_validation_uses_the_pairing_of_defaults(self):
        ctx = self.make_ctx(envelope_text=self.VALIDATE_OK)
        self.upload(ctx)                     # 清洗规则
        options = self.last_options()
        self.assertEqual(options["scoring"],
                         "config/%s" % writers.DEFAULT_SCORING_NAME)
        self.assertTrue(options["rules"].startswith("config/%s" % writers.SCHEME_TEMP_PREFIX))
        self.assertFalse(options["rules"].endswith(".json"),
                         "临时文件不该以 .json 结尾，否则校验窗口里会被列成方案")

    def test_scoring_upload_pairs_the_other_way(self):
        ctx = self.make_ctx(envelope_text=self.VALIDATE_OK)
        payload = self.upload(ctx, name="my_quality.v2.json", content=self.scoring_text())
        options = self.last_options()
        self.assertEqual(options["rules"], "config/%s" % writers.DEFAULT_RULES_NAME)
        self.assertTrue(options["scoring"].startswith("config/%s" % writers.SCHEME_TEMP_PREFIX))
        self.assertEqual(payload["scheme"]["type"], "scoring_scheme")

    def test_declared_type_matching_content_is_accepted(self):
        ctx = self.make_ctx(envelope_text=self.VALIDATE_OK)
        payload = self.upload(ctx, type="cleaning_rules")
        self.assertEqual(payload["scheme"]["type"], "cleaning_rules")

    def test_size_limit_boundary_is_allowed(self):
        # 恰好等于上限应当通过大小检查（内容本身仍要过校验，这里让假 driver 放行）
        pad = " " * (writers.MAX_SCHEME_BYTES - len(self.cleaning_text().encode("utf-8")))
        content = self.cleaning_text() + pad
        self.assertEqual(len(content.encode("utf-8")), writers.MAX_SCHEME_BYTES)
        ctx = self.make_ctx(envelope_text=self.VALIDATE_OK)
        payload = self.upload(ctx, content=content)
        self.assertEqual(payload["size_bytes"], writers.MAX_SCHEME_BYTES)

    # ---- 拒绝路径：都不许留下文件 ----

    def test_oversized_content_is_too_large(self):
        ctx = self.make_ctx(envelope_text=self.VALIDATE_OK)
        content = "x" * (writers.MAX_SCHEME_BYTES + 1)
        with self.assertRaises(StructuredError) as caught:
            self.upload(ctx, content=content)
        exc = caught.exception
        self.assertEqual(exc.code, contract.CODE_CONTENT_TOO_LARGE)
        self.assertEqual(exc.extra["limit_bytes"], writers.MAX_SCHEME_BYTES)
        self.assertEqual(exc.extra["size_bytes"], writers.MAX_SCHEME_BYTES + 1)
        self.assertEqual(self.config_files(), [])
        self.assertEqual(self.recorded_argv(), [], "超限不该先写盘再校验")

    def test_invalid_json_is_config_invalid(self):
        ctx = self.make_ctx(envelope_text=self.VALIDATE_OK)
        with self.assertRaises(StructuredError) as caught:
            self.upload(ctx, content="{不是合法 JSON")
        exc = caught.exception
        self.assertEqual(exc.code, contract.CODE_CONFIG_INVALID)
        self.assertEqual(exc.extra["step"], "parse")
        self.assertTrue(exc.extra["details"])
        self.assertEqual(self.config_files(), [])

    def test_non_object_json_is_config_invalid(self):
        ctx = self.make_ctx(envelope_text=self.VALIDATE_OK)
        with self.assertRaises(StructuredError) as caught:
            self.upload(ctx, content="[1, 2, 3]")
        self.assertEqual(caught.exception.code, contract.CODE_CONFIG_INVALID)
        self.assertEqual(self.config_files(), [])

    def test_unknown_type_in_content_is_config_invalid(self):
        ctx = self.make_ctx(envelope_text=self.VALIDATE_OK)
        with self.assertRaises(StructuredError) as caught:
            self.upload(ctx, content=self.cleaning_text(config_type="something_else"))
        self.assertEqual(caught.exception.code, contract.CODE_CONFIG_INVALID)
        self.assertEqual(caught.exception.extra["step"], "type")
        self.assertEqual(self.config_files(), [])

    def test_missing_type_everywhere_is_config_invalid(self):
        payload = {"scheme_id": "x", "version": "1.0.0"}
        ctx = self.make_ctx(envelope_text=self.VALIDATE_OK)
        with self.assertRaises(StructuredError) as caught:
            self.upload(ctx, content=json.dumps(payload))
        self.assertEqual(caught.exception.code, contract.CODE_CONFIG_INVALID)
        self.assertEqual(self.config_files(), [])

    def test_type_mismatch_between_args_and_content_is_usage(self):
        ctx = self.make_ctx(envelope_text=self.VALIDATE_OK)
        with self.assertRaises(StructuredError) as caught:
            self.upload(ctx, type="scoring_scheme")     # 内容却是 cleaning_rules
        exc = caught.exception
        self.assertEqual(exc.code, contract.CODE_USAGE)
        self.assertEqual(exc.extra["declared_type"], "scoring_scheme")
        self.assertEqual(exc.extra["content_type"], "cleaning_rules")
        self.assertEqual(self.config_files(), [])

    def test_missing_scheme_id_is_config_invalid(self):
        payload = {"config_type": "cleaning_rules", "version": "1.0.0"}
        ctx = self.make_ctx(envelope_text=self.VALIDATE_OK)
        with self.assertRaises(StructuredError) as caught:
            self.upload(ctx, content=json.dumps(payload))
        exc = caught.exception
        self.assertEqual(exc.code, contract.CODE_CONFIG_INVALID)
        self.assertEqual(exc.extra["step"], "identity")
        self.assertIn("scheme_id", exc.message)
        self.assertEqual(self.config_files(), [])

    # ---- 同名冲突与覆盖 ----

    def test_existing_name_is_rejected_without_overwrite(self):
        self.write_scheme("my_rules.v2.json", "旧内容")
        ctx = self.make_ctx(envelope_text=self.VALIDATE_OK)
        with self.assertRaises(StructuredError) as caught:
            self.upload(ctx)
        exc = caught.exception
        self.assertEqual(exc.code, contract.CODE_SCHEME_EXISTS)
        self.assertIn("换个名字", exc.message)
        self.assertEqual(exc.extra["path"], "config/my_rules.v2.json")
        self.assertEqual(self.read_text("my_rules.v2.json"), "旧内容")
        self.assertEqual(self.recorded_argv(), [], "同名冲突不该先去执行侧校验")

    def test_overwrite_replaces_after_validation(self):
        self.write_scheme("my_rules.v2.json", "旧内容")
        ctx = self.make_ctx(envelope_text=self.VALIDATE_OK)
        payload = self.upload(ctx, overwrite=True)
        self.assertTrue(payload["overwritten"])
        self.assertEqual(self.read_text("my_rules.v2.json"), self.cleaning_text())

    def test_failed_validation_keeps_the_existing_file_intact(self):
        self.write_scheme("my_rules.v2.json", "旧内容")
        text = error_envelope(code=contract.CODE_CONFIG_INVALID, message="配置校验失败",
                              details=["pipeline 缺少 step"])
        ctx = self.make_ctx(envelope_text=text, exit_code=2)
        with self.assertRaises(StructuredError) as caught:
            self.upload(ctx, overwrite=True)
        exc = caught.exception
        self.assertEqual(exc.code, contract.CODE_CONFIG_INVALID)
        self.assertEqual(exc.extra["details"], ["pipeline 缺少 step"])
        self.assertEqual(exc.extra["step"], "validate")
        self.assertEqual(self.read_text("my_rules.v2.json"), "旧内容",
                         "校验不通过时不许改动原文件")
        self.assertEqual([n for n in self.config_files()
                          if n.startswith(writers.SCHEME_TEMP_PREFIX)], [],
                         "失败路径必须清理临时文件")

    def test_failed_validation_leaves_no_new_file(self):
        text = error_envelope(code=contract.CODE_CONFIG_INVALID, message="配置校验失败",
                              details=["dimensions 为空"])
        ctx = self.make_ctx(envelope_text=text, exit_code=2)
        with self.assertRaises(StructuredError):
            self.upload(ctx)
        self.assertEqual(self.config_files(), [])

    # ---- 默认方案护栏 ----

    def test_default_scheme_names_are_never_overwritten(self):
        for name in (writers.DEFAULT_RULES_NAME, writers.DEFAULT_SCORING_NAME):
            for overwrite in (False, True):
                with self.subTest(name=name, overwrite=overwrite):
                    self.write_scheme(name, "默认方案原文")
                    ctx = self.make_ctx(envelope_text=self.VALIDATE_OK)
                    with self.assertRaises(StructuredError) as caught:
                        self.upload(ctx, name=name, overwrite=overwrite)
                    exc = caught.exception
                    self.assertEqual(exc.code, contract.CODE_USAGE)
                    self.assertEqual(exc.extra["param"], "name")
                    self.assertIn("默认方案", exc.message)
                    self.assertEqual(self.read_text(name), "默认方案原文",
                                     "默认方案一个字节都不许变")
                    self.assertEqual(self.recorded_argv(), [])

    def test_content_claiming_default_status_gets_a_warning(self):
        # 不解拦：内容归调用方，但要把"这可能被误认为默认方案"说出来
        ctx = self.make_ctx(envelope_text=self.VALIDATE_OK)
        payload = self.upload(ctx, content=self.cleaning_text(status="registered_default"))
        self.assertEqual(payload["scheme"]["status"], "registered_default")
        self.assertTrue(any("registered_default" in item
                            for item in payload["validation"]["warnings"]))

    def test_custom_status_gets_no_such_warning(self):
        ctx = self.make_ctx(envelope_text=self.VALIDATE_OK)
        payload = self.upload(ctx)
        self.assertEqual(payload["validation"]["warnings"], [])

    def test_default_names_still_match_the_execution_side(self):
        from driver.commands import DEFAULT_RULES, DEFAULT_SCORING
        self.assertEqual(writers.DEFAULT_RULES_NAME, os.path.basename(DEFAULT_RULES))
        self.assertEqual(writers.DEFAULT_SCORING_NAME, os.path.basename(DEFAULT_SCORING))

    # ---- 防御性路径校验 ----

    def test_path_like_names_are_defensively_rejected(self):
        ctx = self.make_ctx(envelope_text=self.VALIDATE_OK)
        for name in ("sub/my_rules.json", "../my_rules.json", "/tmp/my_rules.json",
                     "my_rules.txt", ".."):
            with self.subTest(name=name):
                with self.assertRaises(StructuredError) as caught:
                    self.upload(ctx, name=name)
                self.assertEqual(caught.exception.code, contract.CODE_FORBIDDEN_PATH)
        self.assertEqual(self.recorded_argv(), [])

    # ---- 经分派层的整链 ----

    def test_upload_through_dispatch(self):
        self.register_writers()
        ctx = self.make_ctx(envelope_text=self.VALIDATE_OK)
        env = dispatch.call_tool("upload_scheme",
                                 {"name": "via_dispatch.json",
                                  "content": self.cleaning_text()}, ctx)
        self.assertTrue(env["ok"], env)
        self.assertEqual(env["scheme"]["path"], "config/via_dispatch.json")
        self.assertIn("via_dispatch.json", self.config_files())

    def test_dispatch_reports_unknown_param_before_writing(self):
        self.register_writers()
        ctx = self.make_ctx(envelope_text=self.VALIDATE_OK)
        env = dispatch.call_tool("upload_scheme",
                                 {"name": "x.json", "content": self.cleaning_text(),
                                  "path": "nope"}, ctx)
        self.assertFalse(env["ok"])
        self.assertEqual(env["error"]["code"], contract.CODE_USAGE)
        self.assertEqual(self.config_files(), [])


# ---------------------------------------------------------------------------
# 删除自定义方案
# ---------------------------------------------------------------------------

from mcpserver import tasks as task_tools                     # noqa: E402


class DeleteSchemeTest(FakeRepoCase):
    """删除只对"自己上传的自定义方案"开放：默认方案永不可删，占用时先不删。"""

    LIST_OK = envelope(schemes=[
        {"scheme_id": "keep", "type": "cleaning_rules", "version": "1.0.0",
         "status": "custom", "path": "config/keep.json", "description": "保留"},
    ])

    def config_dir(self):
        return os.path.join(self.repo, "config")

    def config_files(self):
        directory = self.config_dir()
        return sorted(os.listdir(directory)) if os.path.isdir(directory) else []

    def write_scheme(self, name, payload=None, text=None):
        os.makedirs(self.config_dir(), exist_ok=True)
        if text is None:
            text = json.dumps({
                "config_type": "cleaning_rules",
                "scheme_id": "mine",
                "version": "3.1.0",
                "status": "custom",
                "description": "我的自定义方案",
            }, ensure_ascii=False)
        with open(os.path.join(self.config_dir(), name), "w", encoding="utf-8") as handle:
            handle.write(text)

    def delete(self, ctx, name="mine.json"):
        return writers.handle_delete_scheme({"name": name}, ctx)

    def idle(self):
        """把"查占用状态"打桩成空闲（唯一接缝；真实判定在真实验证里走一次）。"""
        patcher = mock.patch.object(task_tools, "handle_get_scheduler_state",
                                    return_value={"busy": False, "holder": None})
        patcher.start()
        self.addCleanup(patcher.stop)

    def busy(self, task_id="20261017-000000-busy"):
        payload = {"busy": True, "holder": {"task_id": task_id, "status": "running"}}
        patcher = mock.patch.object(task_tools, "handle_get_scheduler_state",
                                    return_value=payload)
        patcher.start()
        self.addCleanup(patcher.stop)

    # ---- 正常删除 ----

    def test_delete_returns_the_entry_and_the_remaining_list(self):
        self.idle()
        self.write_scheme("mine.json")
        ctx = self.make_ctx(envelope_text=self.LIST_OK)
        payload = self.delete(ctx)
        self.assertTrue(payload["deleted"])
        scheme = payload["scheme"]
        self.assertEqual(sorted(scheme),
                         ["description", "path", "scheme_id", "status", "type", "version"])
        self.assertEqual(scheme["scheme_id"], "mine")
        self.assertEqual(scheme["type"], "cleaning_rules")
        self.assertEqual(scheme["path"], "config/mine.json")
        self.assertEqual(payload["schemes"], json.loads(self.LIST_OK)["schemes"])
        self.assertIn("不可恢复", payload["note"])
        self.assertNotIn("mine.json", self.config_files(), "文件应该已经不在目录里")

    def test_no_absolute_path_is_exposed(self):
        self.idle()
        self.write_scheme("mine.json")
        ctx = self.make_ctx(envelope_text=self.LIST_OK)
        dumped = json.dumps(self.delete(ctx), ensure_ascii=False)
        self.assertNotIn(self.repo, dumped)

    def test_broken_content_still_deletes_with_empty_entry_fields(self):
        # 内容读不出也允许删除（它本来就列不进方案表），但条目字段留空，不编造
        self.idle()
        self.write_scheme("broken.json", text="{不是合法 JSON")
        ctx = self.make_ctx(envelope_text=self.LIST_OK)
        payload = self.delete(ctx, name="broken.json")
        self.assertEqual(payload["scheme"]["scheme_id"], "")
        self.assertEqual(payload["scheme"]["version"], "")
        self.assertTrue(payload["deleted"])
        self.assertNotIn("broken.json", self.config_files())

    def test_remaining_list_is_omitted_when_listing_fails(self):
        self.idle()
        self.write_scheme("mine.json")
        text = error_envelope(code=contract.CODE_TASK_FAILED, message="列方案失败")
        ctx = self.make_ctx(envelope_text=text, exit_code=3)
        payload = self.delete(ctx)
        self.assertTrue(payload["deleted"])
        self.assertNotIn("schemes", payload, "列不出来就不给，别回一份空的当结果")

    # ---- 拒绝路径 ----

    def test_missing_scheme_lists_existing_names(self):
        self.idle()
        ctx = self.make_ctx(envelope_text=self.LIST_OK)
        with self.assertRaises(StructuredError) as caught:
            self.delete(ctx, name="nope.json")
        exc = caught.exception
        self.assertEqual(exc.code, contract.CODE_FILE_NOT_FOUND)
        self.assertIn("keep.json", exc.message)
        self.assertEqual(exc.extra["existing"], ["keep.json"])

    def test_default_schemes_are_never_deleted(self):
        self.idle()
        for name in (writers.DEFAULT_RULES_NAME, writers.DEFAULT_SCORING_NAME):
            with self.subTest(name=name):
                self.write_scheme(name)
                ctx = self.make_ctx(envelope_text=self.LIST_OK)
                with self.assertRaises(StructuredError) as caught:
                    self.delete(ctx, name=name)
                exc = caught.exception
                self.assertEqual(exc.code, contract.CODE_USAGE)
                self.assertIn("默认方案", exc.message)
                self.assertIn(name, self.config_files(), "默认方案一个都不许少")

    def test_default_rejection_happens_before_any_driver_call(self):
        self.idle()
        self.write_scheme(writers.DEFAULT_RULES_NAME)
        ctx = self.make_ctx(envelope_text=self.LIST_OK)
        with self.assertRaises(StructuredError):
            self.delete(ctx, name=writers.DEFAULT_RULES_NAME)
        self.assertEqual(self.recorded_argv(), [])

    def test_busy_task_blocks_deletion_and_names_the_occupier(self):
        self.busy("20261017-000000-abc123")
        self.write_scheme("mine.json")
        ctx = self.make_ctx(envelope_text=self.LIST_OK)
        with self.assertRaises(StructuredError) as caught:
            self.delete(ctx)
        exc = caught.exception
        self.assertEqual(exc.code, contract.CODE_TASK_ALREADY_RUNNING)
        self.assertEqual(exc.extra["occupier"], "20261017-000000-abc123")
        self.assertIn("20261017-000000-abc123", exc.message)
        self.assertIn("等它", exc.message)
        self.assertIn("mine.json", self.config_files(), "占用时不该删")

    def test_busy_check_precedes_existence_check(self):
        # 顺序是刻意的：占用时先告诉用户"等它结束"，比先报"文件不存在"更有用
        self.busy("t-busy")
        ctx = self.make_ctx(envelope_text=self.LIST_OK)
        with self.assertRaises(StructuredError) as caught:
            self.delete(ctx, name="never-existed.json")
        self.assertEqual(caught.exception.code, contract.CODE_TASK_ALREADY_RUNNING)

    def test_symlink_is_not_followed(self):
        self.idle()
        outside = os.path.join(self.repo, "outside.json")
        with open(outside, "w", encoding="utf-8") as handle:
            handle.write("外部文件")
        os.makedirs(self.config_dir(), exist_ok=True)
        try:
            os.symlink(outside, os.path.join(self.config_dir(), "link.json"))
        except OSError:
            self.skipTest("这个环境不允许创建符号链接（Windows 非开发者模式即如此）")
        ctx = self.make_ctx(envelope_text=self.LIST_OK)
        with self.assertRaises(StructuredError) as caught:
            self.delete(ctx, name="link.json")
        self.assertEqual(caught.exception.code, contract.CODE_FORBIDDEN_PATH)
        self.assertTrue(os.path.isfile(outside), "外部文件绝不能被删")
        self.assertTrue(os.path.islink(os.path.join(self.config_dir(), "link.json")))

    def test_directory_is_refused(self):
        self.idle()
        os.makedirs(os.path.join(self.config_dir(), "dir.json"))
        ctx = self.make_ctx(envelope_text=self.LIST_OK)
        with self.assertRaises(StructuredError) as caught:
            self.delete(ctx, name="dir.json")
        exc = caught.exception
        self.assertEqual(exc.code, contract.CODE_USAGE)
        self.assertIn("不是普通文件", exc.message)
        self.assertTrue(os.path.isdir(os.path.join(self.config_dir(), "dir.json")))

    def test_path_like_names_are_defensively_rejected(self):
        self.idle()
        ctx = self.make_ctx(envelope_text=self.LIST_OK)
        for name in ("../mine.json", "sub/mine.json", "/tmp/mine.json", "mine.txt"):
            with self.subTest(name=name):
                with self.assertRaises(StructuredError) as caught:
                    self.delete(ctx, name=name)
                self.assertEqual(caught.exception.code, contract.CODE_FORBIDDEN_PATH)
        self.assertEqual(self.recorded_argv(), [])

    def test_delete_through_dispatch(self):
        self.idle()
        self.write_scheme("mine.json")
        self.register_writers()
        ctx = self.make_ctx(envelope_text=self.LIST_OK)
        env = dispatch.call_tool("delete_scheme", {"name": "mine.json"}, ctx)
        self.assertTrue(env["ok"], env)
        self.assertTrue(env["deleted"])
        self.assertNotIn("mine.json", self.config_files())


if __name__ == "__main__":
    unittest.main()
