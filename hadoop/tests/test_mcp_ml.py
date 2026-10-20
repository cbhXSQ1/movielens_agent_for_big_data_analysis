# -*- coding: utf-8 -*-
"""tests/test_mcp_ml.py —— 机器学习能力的**服务侧**测试（受理、状态、取消、下载、资源）。

覆盖四类必须锁住的东西：

  1. **受理**：三个训练能力如何建任务、写状态、留凭据、立即返回；占用与缺数据怎么拒；
  2. **并入现有体系**：任务状态文件与清洗任务同构、阶段序列用它自己那一份、
     取消时认得出机器学习工作进程；
  3. **模型下载路由**：与任务产物同一套判定（越界、不可外发、不存在、不是普通文件）；
  4. **查询与预测**：只读两个能力的字段、预测的错误分类与时长上限。

机器学习核心（`hadoop/ml`）由另一位同事实现，本文件一律用桩替换，**不真的训练**；
需要核心才能跑的用例按条件跳过并写明原因。测试全程用临时目录构造仓库根，
不碰真实的 `var/tasks` 与 `var/ml`。
"""

import argparse
import io
import json
import types
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _path in (os.path.join(_REPO_ROOT, "hadoop"), _REPO_ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from mcpserver import contract, dispatch, ml, server, tasks, writers   # noqa: E402


def _core_available():
    """机器学习核心是否可用（缺依赖时相关用例跳过，而不是假装通过）。"""
    try:
        from ml import api  # noqa: F401
    except Exception:                                  # noqa: BLE001
        return False
    return True


CORE_AVAILABLE = _core_available()


class MlTestBase(unittest.TestCase):
    """公共夹具：临时仓库根 + 产物目录 + 处理器登记。"""

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="ml-repo-")
        os.makedirs(os.path.join(self.repo, "config"), exist_ok=True)
        self.var_dir = os.path.join(self.repo, "var")
        os.makedirs(os.path.join(self.var_dir, "tasks"), exist_ok=True)
        self._env_backup = os.environ.get("ML_VAR_DIR")
        os.environ["ML_VAR_DIR"] = self.var_dir
        self.ctx = dispatch.ToolContext(repo_root=self.repo,
                                        env={"ML_VAR_DIR": self.var_dir}, config={})
        dispatch.reset_handlers()
        dispatch.load_handlers(dispatch.HANDLER_MODULES + ("mcpserver.ml",))

    def tearDown(self):
        dispatch.reset_handlers()
        if self._env_backup is None:
            os.environ.pop("ML_VAR_DIR", None)
        else:
            os.environ["ML_VAR_DIR"] = self._env_backup
        shutil.rmtree(self.repo, ignore_errors=True)


# ---------------------------------------------------------------------------
# 一、登记与工具表一致
# ---------------------------------------------------------------------------

class RegistrationTest(MlTestBase):

    def test_six_handlers_registered(self):
        from mcpserver import tools
        names = set(ml.HANDLERS)
        self.assertEqual(names, {"train_classifier", "cluster_users", "reduce_dimensions",
                                 "predict_high_rating", "list_ml_models", "get_ml_model"})
        for name in names:
            self.assertIsNotNone(tools.get_tool(name), "工具表里没有 %s" % name)

    def test_all_tools_have_handlers(self):
        from mcpserver import tools
        have = set(dispatch.available_tools())
        self.assertEqual(have, set(tools.tool_names()))

    def test_stage_list_shape(self):
        # 首项是起始状态、末项是终态；`queued` 不计入进度（与既有任务同一套算法）
        self.assertEqual(ml.ML_STAGES[0], "queued")
        self.assertEqual(ml.ML_STAGES[-1], "done")
        self.assertGreaterEqual(len(ml.ML_STAGES), 5)

    @unittest.skipUnless(CORE_AVAILABLE, "机器学习核心不可用（缺依赖），跳过跨模块一致性断言")
    def test_model_root_matches_core(self):
        from ml import api
        self.assertEqual(ml.ML_ROOT_REL, api.ML_ROOT_REL)

    def test_tool_task_type_mapping(self):
        self.assertEqual(ml.TOOL_TASK_TYPE["train_classifier"], ml.TASK_CLASSIFICATION)
        self.assertEqual(ml.TOOL_TASK_TYPE["cluster_users"], ml.TASK_CLUSTERING)
        self.assertEqual(ml.TOOL_TASK_TYPE["reduce_dimensions"], ml.TASK_REDUCTION)


# ---------------------------------------------------------------------------
# 二、受理：建任务、写状态、留凭据、立即返回
# ---------------------------------------------------------------------------

class SubmitTest(MlTestBase):

    def _submit(self, tool_name="train_classifier", args=None, busy=None, published=None):
        """走完整受理路径，但把"启动工作进程"换成记录（不真的起进程）。"""
        base = {"algorithm": "random_forest"}
        base.update(args or {})
        spawned = {}

        def fake_spawn(model_id, task_type, algorithm, params, data_version, tag, ctx, task_dir):
            spawned.update({"model_id": model_id, "task_type": task_type,
                            "algorithm": algorithm, "params": params,
                            "data_version": data_version, "tag": tag, "task_dir": task_dir})

        listing = published if published is not None else {
            "exists": True, "versions": [{"data_version": "ml1m-clean-v1"}]}
        with mock.patch.object(ml, "_spawn_worker", side_effect=fake_spawn), \
                mock.patch.object(writers, "_busy_holder", return_value=busy), \
                mock.patch.object(ml.readers, "list_published_versions", return_value=listing):
            payload = dispatch.call_tool(tool_name, base, self.ctx)
        return payload, spawned

    def test_accept_returns_model_id_and_queued(self):
        payload, spawned = self._submit()
        self.assertTrue(payload["ok"], payload)
        self.assertTrue(payload["model_id"].startswith("ml-"))
        self.assertEqual(payload["status"], "queued")
        self.assertEqual(payload["status_cn"], "排队")
        self.assertEqual(payload["task_type"], "classification")
        self.assertEqual(payload["algorithm"], "random_forest")
        self.assertEqual(payload["estimated_seconds"], 480)
        self.assertIn("不是本次实测", payload["estimated_seconds_note"])
        # 受理阶段不许出现指标与产物（那要等任务成功）
        self.assertNotIn("metrics", payload)
        self.assertNotIn("artifacts", payload)
        self.assertEqual(spawned["model_id"], payload["model_id"])

    def test_status_file_is_written_like_other_tasks(self):
        payload, _ = self._submit()
        path = os.path.join(self.var_dir, "tasks", payload["model_id"], "status.json")
        self.assertTrue(os.path.isfile(path), "受理后必须写下状态文件")
        with open(path, encoding="utf-8") as handle:
            status = json.load(handle)
        self.assertEqual(status["status"], "queued")
        self.assertEqual(status["stage"], "queued")
        self.assertEqual(status["task_type"], "classification")
        self.assertEqual(status["algorithm"], "random_forest")
        self.assertEqual(status["stage_total"], len(ml.ML_STAGES) - 1)
        self.assertEqual(status["progress_percent"], 0)

    def test_submit_marker_is_written(self):
        payload, _ = self._submit()
        marker = os.path.join(self.var_dir, "tasks", payload["model_id"],
                              contract.CONTROL_FILE_SUBMIT)
        self.assertTrue(os.path.isfile(marker), "受理成功后必须留下由本服务提交的凭据")
        with open(marker, encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(data["tool"], "train_classifier")
        self.assertEqual(data["task_type"], "classification")

    def test_busy_execution_slot_is_rejected(self):
        payload, spawned = self._submit(busy="20261018-000000-aaaaaa")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], contract.CODE_TASK_ALREADY_RUNNING)
        self.assertEqual(payload["error"]["occupier"], "20261018-000000-aaaaaa")
        self.assertIn("执行位", payload["error"]["message"])
        self.assertEqual(spawned, {}, "被拒时不许启动工作进程")

    def test_missing_data_version_is_rejected(self):
        payload, spawned = self._submit(args={"data_version": "ml1m-clean-v9"})
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], contract.CODE_DEPENDENCY_MISSING)
        self.assertEqual(payload["error"]["data_version"], "ml1m-clean-v9")
        self.assertEqual(spawned, {})

    def test_cluster_unreachable_still_accepts(self):
        """"连不上集群"绝不能报成"缺数据"：照常受理，让任务自己如实失败。"""
        def boom(*_a, **_k):
            raise contract.StructuredError(contract.CODE_DRIVER_UNREACHABLE, "连不上集群")

        with mock.patch.object(ml, "_spawn_worker", return_value=None), \
                mock.patch.object(writers, "_busy_holder", return_value=None), \
                mock.patch.object(ml.readers, "list_published_versions", side_effect=boom):
            payload = dispatch.call_tool("train_classifier",
                                         {"algorithm": "naive_bayes",
                                          "data_version": "ml1m-clean-v1"}, self.ctx)
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["data_check"]["data_check"], "unreachable")

    def test_each_tool_maps_to_its_task_type(self):
        cases = {"train_classifier": "classification",
                 "cluster_users": "clustering",
                 "reduce_dimensions": "dimensionality_reduction"}
        for tool_name, expected in cases.items():
            args = {"algorithm": {"classification": "random_forest",
                                  "clustering": "kmeans",
                                  "dimensionality_reduction": "pca"}[expected]}
            payload, spawned = self._submit(tool_name=tool_name, args=args)
            self.assertTrue(payload["ok"], payload)
            self.assertEqual(spawned["task_type"], expected)
            self.assertEqual(payload["task_type"], expected)

    def test_algorithm_value_is_validated_by_dispatch(self):
        payload, spawned = self._submit(args={"algorithm": "xgboost"})
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], contract.CODE_USAGE)
        self.assertEqual(spawned, {})

    def test_object_params_are_passed_through(self):
        payload, spawned = self._submit(args={"params": {"n_estimators": 30}})
        self.assertTrue(payload["ok"], payload)
        # 转发给工作进程的必须是调用方原样传来的 params（不多不少）
        self.assertEqual(spawned["params"], {"n_estimators": 30})
        if CORE_AVAILABLE:
            # 核心可用时，受理回的是"预计生效参数"：与训练同一套规则把默认值填齐 ——
            # 这样用户在开跑前就看得见"多少棵树、多深、叶子最少几条"
            #（设计意图见 _submit_training 里 payload["params"] 的注释）。
            preview = payload["params"]
            self.assertEqual(preview["n_estimators"], 30)
            self.assertIn("max_depth", preview, "预览要能看出树深，而不是只回原值")
        else:
            # 核心不可用时预览退化为原样回传：预览失败不拖垮受理
            self.assertEqual(payload["params"], {"n_estimators": 30})


# ---------------------------------------------------------------------------
# 三、并入现有体系：状态、阶段、进程定位
# ---------------------------------------------------------------------------

class TaskIntegrationTest(MlTestBase):

    def test_stage_list_for_ml_task_types(self):
        rt = tasks._driver(self.ctx)
        for task_type in ml.ML_TASK_TYPES:
            self.assertEqual(tasks._stage_list_for(rt, {"task_type": task_type}),
                             ml.ML_STAGES, task_type)
        # 既有任务类型不受影响
        self.assertEqual(tasks._stage_list_for(rt, {"task_type": "clean"}), rt.CLEAN_STAGES)
        self.assertEqual(tasks._stage_list_for(rt, {"task_type": "score"}), rt.SCORE_STAGES)
        self.assertEqual(tasks._stage_list_for(rt, {"task_type": "full"}), rt.STAGES)

    def test_matches_ml_worker(self):
        argv = ["/repo/.venv/bin/python", "-m", "mcpserver.ml_worker",
                "--task-id", "ml-20261018-000000-abc123", "--task-type", "classification"]
        self.assertTrue(tasks._matches_ml_worker(argv, "ml-20261018-000000-abc123"))
        # 任务编号不同 → 不是它
        self.assertFalse(tasks._matches_ml_worker(argv, "ml-20261018-000000-ffffff"))
        # 只是恰好命令行里带同样字样（例如查看日志的人）→ 不算
        self.assertFalse(tasks._matches_ml_worker(
            ["/bin/grep", "mcpserver.ml_worker", "--task-id",
             "ml-20261018-000000-abc123"], "ml-20261018-000000-abc123"))
        # 脚本形态（直接跑文件）也要认
        self.assertTrue(tasks._matches_ml_worker(
            ["/usr/bin/python3", "/repo/hadoop/mcpserver/ml_worker.py",
             "--task-id=ml-20261018-000000-abc123"], "ml-20261018-000000-abc123"))
        # 执行侧的清洗任务不该被当成机器学习工作进程
        self.assertFalse(tasks._matches_ml_worker(
            ["/usr/bin/python3", "hadoop/driver/run_task.py", "_run", "--task-id", "t1"], "t1"))

    def test_cancel_finds_ml_worker(self):
        """取消时的进程定位必须同时认得两种形状（否则训练任务取消不掉）。"""
        argv = ["/repo/.venv/bin/python", "-m", "mcpserver.ml_worker",
                "--task-id", "ml-x", "--task-type", "clustering", "--algorithm", "kmeans"]
        with mock.patch.object(tasks, "_read_cmdline", side_effect=lambda pid: argv), \
                mock.patch.object(tasks.os, "listdir", return_value=["1234", "self"]), \
                mock.patch.object(tasks.os.path, "isdir", return_value=True):
            self.assertEqual(tasks._find_driver_processes("ml-x"), [1234])

    def test_occupancy_sees_ml_task(self):
        """占用判定无需改动即可看见训练任务（状态文件与清洗任务同构）。

        这是"并入现有体系"的关键一环：训练一旦跑起来，执行位就应被占住，
        此后发起清洗会得到"已有任务在运行"——而不是两个任务并行。
        """
        task_dir = os.path.join(self.var_dir, "tasks", "ml-20261018-000000-abc123")
        os.makedirs(task_dir)
        status = {"task_id": "ml-20261018-000000-abc123", "status": "running",
                  "stage": "train", "stage_index": 3, "stage_total": len(ml.ML_STAGES) - 1,
                  "progress_percent": 43, "task_type": "classification",
                  "algorithm": "random_forest", "started_at": "2026-10-18T00:00:00Z"}
        with open(os.path.join(task_dir, "status.json"), "w", encoding="utf-8") as handle:
            json.dump(status, handle)

        rt = tasks._driver(self.ctx)
        self.assertEqual(rt.running_task(), "ml-20261018-000000-abc123")
        # 状态摘要按既有字段渲染，训练任务多出来的取向字段也照实带出
        view = tasks._status_view(status)
        self.assertEqual(view["task_type"], "classification")
        self.assertEqual(view["algorithm"], "random_forest")
        self.assertEqual(view["progress_percent"], 43)

    def test_ml_task_releases_slot_when_terminal(self):
        """训练到终态后执行位自动释放（五态与既有任务同一套）。"""
        task_dir = os.path.join(self.var_dir, "tasks", "ml-20261018-000000-abc123")
        os.makedirs(task_dir)
        with open(os.path.join(task_dir, "status.json"), "w", encoding="utf-8") as handle:
            json.dump({"task_id": "ml-20261018-000000-abc123", "status": "succeeded",
                       "task_type": "classification"}, handle)
        rt = tasks._driver(self.ctx)
        self.assertIsNone(rt.running_task())


# ---------------------------------------------------------------------------
# 四、模型目录与下载判定
# ---------------------------------------------------------------------------

class ModelPathTest(MlTestBase):

    def test_model_directory_rejects_bad_ids(self):
        for bad in ("../etc", "/etc", "a/b", "..", ""):
            with self.assertRaises(contract.StructuredError) as cm:
                ml.model_directory(self.ctx, bad)
            self.assertEqual(cm.exception.code, contract.CODE_FORBIDDEN_PATH, bad)

    def test_model_directory_resolves_under_ml_root(self):
        path = ml.model_directory(self.ctx, "ml-20261018-000000-abc123")
        self.assertEqual(os.path.realpath(path),
                         os.path.realpath(os.path.join(self.repo, "var", "ml",
                                                       "ml-20261018-000000-abc123")))

    def test_new_model_id_shape(self):
        model_id = ml._new_model_id()
        self.assertTrue(model_id.startswith("ml-"))
        self.assertTrue(contract.valid_task_id(model_id), model_id)


class ModelDownloadTest(MlTestBase):

    MODEL_ID = "ml-20261018-000000-abc123"

    def setUp(self):
        MlTestBase.setUp(self)
        self.model_dir = os.path.join(self.repo, "var", "ml", self.MODEL_ID)
        os.makedirs(self.model_dir, exist_ok=True)
        self._write("model.joblib", b"binary-model-bytes")
        self._write("meta.json", b'{"model_id": "x"}')
        self._write("labels.csv", b"user_id,cluster\n1,0\n")

    def _write(self, relpath, data):
        path = os.path.join(self.model_dir, relpath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def _code(self, relpath):
        try:
            server.downloadable_model_file(self.ctx, self.MODEL_ID, relpath)
            return None
        except contract.StructuredError as exc:
            return exc

    def test_normal_files_are_downloadable(self):
        path, size = server.downloadable_model_file(self.ctx, self.MODEL_ID, "model.joblib")
        self.assertEqual(size, len(b"binary-model-bytes"))
        self.assertTrue(path.endswith("model.joblib"))
        self.assertEqual(self._code("meta.json"), None)

    def test_traversal_and_absolute_are_forbidden(self):
        for bad in ("../outside.txt", "/etc/passwd", "sub/../../x"):
            exc = self._code(bad)
            self.assertIsNotNone(exc, bad)
            self.assertEqual(exc.code, contract.CODE_FORBIDDEN_PATH, bad)

    def test_not_outward_files_are_forbidden(self):
        self._write(".hidden.json", b"{}")
        self._write("metrics/after.json.raw.crc", b"checksum")
        self._write("logs/tmp.tmp", b"residue")
        cases = {".hidden.json": "sidecar",
                 "metrics/after.json.raw.crc": "sidecar",
                 "logs/tmp.tmp": "residue"}
        for relpath, kind in cases.items():
            exc = self._code(relpath)
            self.assertIsNotNone(exc, relpath)
            self.assertEqual(exc.code, contract.CODE_FORBIDDEN_PATH, relpath)
            self.assertEqual(exc.extra.get("reason_kind"), kind, relpath)

    def test_missing_file_and_directory(self):
        exc = self._code("nope.json")
        self.assertEqual(exc.code, contract.CODE_FILE_NOT_FOUND)
        os.makedirs(os.path.join(self.model_dir, "subdir"), exist_ok=True)
        exc = self._code("subdir")
        self.assertEqual(exc.code, contract.CODE_FILE_NOT_FOUND)
        self.assertEqual(exc.extra.get("reason_kind"), "directory")

    def test_symlink_escape_is_forbidden(self):
        outside = os.path.join(self.repo, "outside.txt")
        with open(outside, "w", encoding="utf-8") as handle:
            handle.write("secret")
        link = os.path.join(self.model_dir, "link.json")
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):        # pragma: no cover
            self.skipTest("本环境不支持创建符号链接")
        exc = self._code("link.json")
        self.assertIsNotNone(exc)
        self.assertEqual(exc.code, contract.CODE_FORBIDDEN_PATH)

    def test_media_type_by_extension(self):
        self.assertEqual(server.model_media_type("meta.json"), "application/json")
        self.assertEqual(server.model_media_type("labels.csv"), "text/csv")
        self.assertEqual(server.model_media_type("coords.jsonl"), "application/x-ndjson")
        self.assertEqual(server.model_media_type("report.md"), "text/markdown")
        self.assertEqual(server.model_media_type("model.joblib"),
                         "application/octet-stream")

    def test_route_is_advertised(self):
        routes = server.download_routes()
        self.assertEqual(routes["model"], server.DOWNLOAD_MODEL_ROUTE)
        payload = server.health_payload(self.ctx, {"name": "s", "version": "1"})
        self.assertEqual(payload["endpoints"]["download_model"], server.DOWNLOAD_MODEL_ROUTE)


# ---------------------------------------------------------------------------
# 五、查询与预测
# ---------------------------------------------------------------------------

class FakeCore(object):
    """机器学习核心的桩：只回固定形状，不真的算。"""

    def __init__(self, models=None, model=None, prediction=None, error=None):
        self._models = models if models is not None else []
        self._model = model
        self._prediction = prediction or {}
        self._error = error

    def model_exists(self, model_id, repo_root):
        return self._model is not None

    def list_models(self, repo_root):
        return {"models": self._models}

    def get_model(self, model_id, repo_root):
        if self._error is not None:
            raise self._error
        if self._model is None:
            return {}
        return {"model": self._model}

    def predict_rating(self, model_id, user_id, movie_id, repo_root):
        if self._error is not None:
            raise self._error
        return dict(self._prediction)


class QueryTest(MlTestBase):

    def test_list_models(self):
        core = FakeCore(models=[{"model_id": "ml-1", "task_type": "classification",
                                 "algorithm": "random_forest", "data_version": "ml1m-clean-v1",
                                 "created_at": "2026-10-18T00:00:00Z",
                                 "metrics_summary": {"f1": 0.7, "auc": 0.8}}])
        with mock.patch.object(ml, "_api", return_value=core):
            payload = dispatch.call_tool("list_ml_models", {}, self.ctx)
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["models"][0]["model_id"], "ml-1")

    def test_get_model_not_found(self):
        core = FakeCore(model=None)
        with mock.patch.object(ml, "_api", return_value=core):
            payload = dispatch.call_tool("get_ml_model", {"model_id": "ml-none"}, self.ctx)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], contract.CODE_MODEL_NOT_FOUND)
        self.assertEqual(payload["error"]["model_id"], "ml-none")

    def test_get_model_returns_metadata(self):
        model = {"model_id": "ml-1", "metrics": {"f1": 0.7},
                 "artifacts": [{"id": "a", "title": "模型本体", "kind": "joblib",
                                "role": "model", "path": "model.joblib", "size": 10}]}
        core = FakeCore(model=model)
        with mock.patch.object(ml, "_api", return_value=core):
            payload = dispatch.call_tool("get_ml_model", {"model_id": "ml-1"}, self.ctx)
        self.assertTrue(payload["ok"], payload)
        artifact = payload["model"]["artifacts"][0]
        self.assertEqual(artifact["role"], "model")
        self.assertEqual(artifact["kind"], "joblib")

    @unittest.skipUnless(CORE_AVAILABLE, "机器学习核心不可用（缺依赖），跳过")
    def test_get_model_core_error_keeps_code(self):
        from ml.api import TrainingError
        core = FakeCore(error=TrainingError("MODEL_NOT_FOUND", "没有这个模型"))
        with mock.patch.object(ml, "_api", return_value=core):
            payload = dispatch.call_tool("get_ml_model", {"model_id": "ml-x"}, self.ctx)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "MODEL_NOT_FOUND")


class PredictTest(MlTestBase):

    def test_model_missing(self):
        core = FakeCore(model=None)
        with mock.patch.object(ml, "_api", return_value=core):
            payload = dispatch.call_tool("predict_high_rating",
                                         {"model_id": "ml-none", "user_id": 1,
                                          "movie_id": 2}, self.ctx)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], contract.CODE_MODEL_NOT_FOUND)

    def test_prediction_payload_is_passed_through(self):
        core = FakeCore(model={"model_id": "ml-1"})
        result = {"prediction": {"label": 1, "probability": 0.82, "threshold": 0.5},
                  "model_version": "fp-1"}
        with mock.patch.object(ml, "_api", return_value=core), \
                mock.patch.object(ml, "_run_prediction", return_value=result):
            payload = dispatch.call_tool("predict_high_rating",
                                         {"model_id": "ml-1", "user_id": 7,
                                          "movie_id": 1193}, self.ctx)
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["user_id"], "7")          # 回显统一成字符串
        self.assertEqual(payload["movie_id"], "1193")
        self.assertEqual(payload["prediction"]["probability"], 0.82)
        self.assertEqual(payload["model_version"], "fp-1")

    def test_prediction_timeout_is_reported(self):
        core = FakeCore(model={"model_id": "ml-1"})
        with mock.patch.object(ml, "_api", return_value=core), \
                mock.patch.object(ml.subprocess, "run",
                                  side_effect=subprocess.TimeoutExpired("x", 20)):
            payload = dispatch.call_tool("predict_high_rating",
                                         {"model_id": "ml-1", "user_id": 1,
                                          "movie_id": 2}, self.ctx)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], contract.CODE_DRIVER_TIMEOUT)

    def test_prediction_core_failure_is_mapped(self):
        core = FakeCore(model={"model_id": "ml-1"})
        failure = {"ok": False, "error": {"code": "USAGE",
                                          "message": "该模型不是分类模型"}}
        completed = subprocess.CompletedProcess(args=[], returncode=3,
                                                stdout=json.dumps(failure).encode(),
                                                stderr=b"")
        with mock.patch.object(ml, "_api", return_value=core), \
                mock.patch.object(ml.subprocess, "run", return_value=completed):
            payload = dispatch.call_tool("predict_high_rating",
                                         {"model_id": "ml-1", "user_id": 1,
                                          "movie_id": 2}, self.ctx)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "USAGE")
        self.assertIn("不是分类模型", payload["error"]["message"])

    def test_unparseable_prediction_output(self):
        core = FakeCore(model={"model_id": "ml-1"})
        completed = subprocess.CompletedProcess(args=[], returncode=1,
                                                stdout=b"", stderr=b"boom")
        with mock.patch.object(ml, "_api", return_value=core), \
                mock.patch.object(ml.subprocess, "run", return_value=completed):
            payload = dispatch.call_tool("predict_high_rating",
                                         {"model_id": "ml-1", "user_id": 1,
                                          "movie_id": 2}, self.ctx)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], contract.CODE_DRIVER_BAD_OUTPUT)


# ---------------------------------------------------------------------------
# 六、工作进程的解析与进度语义
# ---------------------------------------------------------------------------

class WorkerTest(unittest.TestCase):

    def test_stage_index_keeps_progress_on_unknown_stage(self):
        from mcpserver import ml_worker
        self.assertEqual(ml_worker._stage_index("train", 1), ml.ML_STAGES.index("train"))
        # 核心将来加了新阶段：进度停在原地，不退回起点
        self.assertEqual(ml_worker._stage_index("brand_new_stage", 4), 4)

    def test_parser_defaults(self):
        from mcpserver import ml_worker
        args = ml_worker.build_parser().parse_args(
            ["--task-id", "ml-1", "--task-type", "clustering", "--algorithm", "kmeans"])
        self.assertEqual(args.mode, "train")
        self.assertEqual(args.params, "")

    @unittest.skipUnless(CORE_AVAILABLE, "机器学习核心不可用（缺依赖），跳过")
    def test_predict_mode_reports_structured_failure(self):
        """预测模式：核心报错时输出一行结构化 JSON，退出码 3。"""
        from mcpserver import ml_worker
        from ml.api import TrainingError

        with mock.patch("ml.api.predict_rating",
                        side_effect=TrainingError("MODEL_NOT_FOUND", "没有这个模型")):
            with mock.patch.object(sys, "stdout", new=io.StringIO()) as captured:
                code = ml_worker._run_predict(
                    mock.Mock(model_id="ml-x", user_id="1", movie_id="2"))
        self.assertEqual(code, 3)
        payload = json.loads(captured.getvalue().strip().splitlines()[-1])
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "MODEL_NOT_FOUND")



# ---------------------------------------------------------------------------
# 七、工作进程：状态推进、结果落盘、失败与中止
# ---------------------------------------------------------------------------

class _FakeTrainingError(Exception):
    """桩用的核心异常：按约定带 code / message / extra 三个属性。"""

    def __init__(self, code, message, **extra):
        Exception.__init__(self, message)
        self.code = code
        self.message = message
        self.extra = dict(extra)


def _fake_core(run_training):
    """造一个假的 `ml.api` 模块（只替换工作进程用到的那几个名字）。"""
    module = types.ModuleType("ml.api")
    module.TrainingError = _FakeTrainingError
    module.run_training = run_training
    module.predict_rating = lambda *a, **k: {}
    module.ML_ROOT_REL = ml.ML_ROOT_REL
    return module


class WorkerTrainingTest(MlTestBase):
    """工作进程的核心行为：进度推进、成功落结果、失败带码、中止优先。"""

    TASK_ID = "ml-20261018-000000-abc123"

    def _args(self, **over):
        base = {"task_id": self.TASK_ID, "task_type": "classification",
                "algorithm": "random_forest", "params": '{"n_estimators": 20}',
                "data_version": "ml1m-clean-v1", "tag": "t"}
        base.update(over)
        return argparse.Namespace(**base)

    def _status(self):
        path = os.path.join(self.var_dir, "tasks", self.TASK_ID, "status.json")
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    def _run(self, run_training):
        from mcpserver import ml_worker
        seen = []

        def wrapped(*args, **kwargs):
            progress = kwargs.get("progress")
            if progress:
                progress("load_data")
                progress("train")
                seen.append(self._status()["stage"])
            return run_training(*args, **kwargs)

        # 工作进程会把一行 JSON 打到标准输出（真实运行时进日志），测试里静音掉
        with mock.patch.dict(sys.modules, {"ml.api": _fake_core(wrapped)}), \
                mock.patch.object(sys, "stdout", new=io.StringIO()):
            code = ml_worker._run_training(self._args())
        return code, seen

    def test_success_writes_result_and_finishes(self):
        def run_training(task_type, algorithm, params, repo_root, progress=None,
                         data_version=None, task_id=None):
            return {"model_id": task_id, "task_type": task_type, "algorithm": algorithm,
                    "params": {"n_estimators": 20}, "metrics": {"f1": 0.71, "auc": 0.79},
                    "artifacts": [{"id": "a", "title": "模型本体", "kind": "joblib",
                                   "role": "model", "path": "model.joblib", "size": 12}],
                    "data_version": data_version}

        code, seen = self._run(run_training)
        self.assertEqual(code, 0)
        self.assertEqual(seen, ["train"])                 # 进度确实推进到训练阶段
        status = self._status()
        self.assertEqual(status["status"], "succeeded")
        self.assertEqual(status["stage"], "done")
        self.assertEqual(status["progress_percent"], 100)
        result_path = os.path.join(self.var_dir, "tasks", self.TASK_ID, "result.json")
        self.assertTrue(os.path.isfile(result_path))
        with open(result_path, encoding="utf-8") as handle:
            result = json.load(handle)
        self.assertEqual(result["metrics"]["f1"], 0.71)
        self.assertEqual(result["artifacts"][0]["role"], "model")
        self.assertEqual(result["finished_at"], status["finished_at"])

    def test_failure_records_code(self):
        def run_training(*_a, **_k):
            raise _FakeTrainingError("TASK_FAILED", "训练阶段失败：内存不足", stage="train")

        code, _ = self._run(run_training)
        self.assertEqual(code, 3)
        status = self._status()
        self.assertEqual(status["status"], "failed")
        self.assertEqual(status["errors"][0]["code"], "TASK_FAILED")
        self.assertIn("内存不足", status["errors"][0]["message"])

    def test_unexpected_error_is_reported_as_internal(self):
        def run_training(*_a, **_k):
            raise ValueError("核心内部炸了")

        code, _ = self._run(run_training)
        self.assertEqual(code, 3)
        status = self._status()
        self.assertEqual(status["status"], "failed")
        self.assertEqual(status["errors"][0]["code"], contract.CODE_INTERNAL_ERROR)
        self.assertIn("ValueError", status["errors"][0]["message"])

    def test_abort_request_wins_over_success(self):
        """已请求中止时，即使核心跑成功也要落成"已中止"（由执行侧的守卫统一负责）。"""
        task_dir = os.path.join(self.var_dir, "tasks", self.TASK_ID)
        os.makedirs(task_dir, exist_ok=True)
        with open(os.path.join(task_dir, contract.CONTROL_FILE_CANCEL), "w",
                  encoding="utf-8") as handle:
            handle.write("{}")

        def run_training(task_type, algorithm, params, repo_root, progress=None,
                         data_version=None, task_id=None):
            return {"model_id": task_id, "metrics": {}}

        code, _ = self._run(run_training)
        self.assertEqual(code, 0)
        status = self._status()
        self.assertEqual(status["status"], "aborted")
        self.assertIn("aborted_at", status)

    def test_missing_required_arguments(self):
        from mcpserver import ml_worker
        with mock.patch.object(sys, "stdout", new=io.StringIO()) as captured:
            code = ml_worker.main(["--task-type", "classification"])
        self.assertEqual(code, 2)
        payload = json.loads(captured.getvalue().strip().splitlines()[-1])
        self.assertFalse(payload["ok"])
        self.assertIn("缺少参数", payload["error"]["message"])


# ---------------------------------------------------------------------------
# 五、训练工作进程的存活监护：进程没了而状态没终态，死因必须进 status.json
# ---------------------------------------------------------------------------

class WorkerSupervisionTest(MlTestBase):
    """清单㉗㉘：监护 + 死因记录。只补记录，不改已经到终态的结论。"""

    TASK_ID = "ml-20261020-120000-sup001"

    class _FakeProc(object):
        """够用的进程替身：监护只问它"退出码是多少、什么时候退的"。"""

        def __init__(self, returncode):
            self.returncode = returncode

        def wait(self):
            return self.returncode

    def _write_status(self, status="running", stage="train"):
        task_dir = os.path.join(self.var_dir, "tasks", self.TASK_ID)
        os.makedirs(task_dir, exist_ok=True)
        with open(os.path.join(task_dir, "status.json"), "w", encoding="utf-8") as fh:
            json.dump({"task_id": self.TASK_ID, "status": status, "stage": stage}, fh)
        return task_dir

    def _read(self):
        with open(os.path.join(self.var_dir, "tasks", self.TASK_ID, "status.json"),
                  encoding="utf-8") as fh:
            return json.load(fh)

    def test_dead_worker_gets_failure_record(self):
        task_dir = self._write_status()
        with open(os.path.join(task_dir, "ml_worker.log"), "w", encoding="utf-8") as fh:
            fh.write("开始训练……\nKilled\n")
        ml._reap_dead_worker(self.TASK_ID, self._FakeProc(-9), self.ctx, task_dir)
        status = self._read()
        self.assertEqual(status["status"], "failed")
        record = status["errors"][0]
        self.assertEqual(record["code"], contract.CODE_TASK_FAILED)
        self.assertEqual(record["exit_code"], -9)
        self.assertIn("Killed", record["log_tail"], "死因要带日志尾部，排障就靠它")
        self.assertNotIn(self.TASK_ID, ml._LIVE_WORKERS, "回收后不再持有进程")

    def test_dependency_missing_is_its_own_code(self):
        """缺依赖是最常见的死因：单独归类，并直接告诉用户装依赖的命令。"""
        task_dir = self._write_status()
        with open(os.path.join(task_dir, "ml_worker.log"), "w", encoding="utf-8") as fh:
            fh.write("ModuleNotFoundError: No module named 'numpy'\n")
        ml._reap_dead_worker(self.TASK_ID, self._FakeProc(1), self.ctx, task_dir)
        record = self._read()["errors"][0]
        self.assertEqual(record["code"], contract.CODE_DEPENDENCY_MISSING)
        self.assertIn("requirements-ml.txt", record["message"])

    def test_terminal_status_is_never_overwritten(self):
        task_dir = self._write_status(status="succeeded", stage="done")
        ml._reap_dead_worker(self.TASK_ID, self._FakeProc(0), self.ctx, task_dir)
        self.assertEqual(self._read()["status"], "succeeded")

    def test_aborted_status_is_never_overwritten(self):
        task_dir = self._write_status(status="aborted", stage="train")
        ml._reap_dead_worker(self.TASK_ID, self._FakeProc(-9), self.ctx, task_dir)
        self.assertEqual(self._read()["status"], "aborted")

    def test_missing_log_is_tolerated(self):
        task_dir = self._write_status()
        ml._reap_dead_worker(self.TASK_ID, self._FakeProc(1), self.ctx, task_dir)
        record = self._read()["errors"][0]
        self.assertEqual(record["code"], contract.CODE_TASK_FAILED)
        self.assertNotIn("log_tail", record, "没有日志就不编一段进去")

    def test_supervisor_thread_reaps_a_real_process(self):
        """端到端：起一个立刻退出的真进程，监护线程把死因补上。"""
        task_dir = self._write_status()
        proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(7)"])
        thread = ml._supervise_worker(self.TASK_ID, proc, self.ctx, task_dir)
        thread.join(timeout=15)
        self.assertFalse(thread.is_alive(), "监护线程该在进程退出后收尾")
        self.assertEqual(self._read()["errors"][0]["exit_code"], 7)

    def test_spawn_worker_uses_the_service_interpreter(self):
        """工作进程的解释器就是服务进程这一个（sys.executable）—— 清单㉖ 的口径。"""
        task_dir = os.path.join(self.var_dir, "tasks", self.TASK_ID)
        os.makedirs(task_dir, exist_ok=True)
        with mock.patch.object(ml.subprocess, "Popen") as popen, \
                mock.patch.object(ml, "_supervise_worker") as supervise:
            proc = ml._spawn_worker(self.TASK_ID, "classification", "random_forest",
                                    {"n_estimators": 5}, "ml1m-clean-v1", "t",
                                    self.ctx, task_dir)
        argv = popen.call_args[0][0]
        self.assertIs(argv[0], sys.executable)
        self.assertEqual(argv[1:4], ["-m", "mcpserver.ml_worker", "--task-id"])
        self.assertEqual(argv[4], self.TASK_ID)
        self.assertTrue(popen.call_args[1]["start_new_session"], "必须独立会话")
        self.assertIn(self.TASK_ID, ml._LIVE_WORKERS, "要持有进程，防止被垃圾回收")
        supervise.assert_called_once()
        self.assertIs(proc, popen.return_value)
        ml._LIVE_WORKERS.pop(self.TASK_ID, None)


class WorkerImportFailureTest(MlTestBase):
    """工作进程自己也要接住"核心导入失败"（缺依赖）：不能只留一段 traceback。"""

    TASK_ID = "ml-20261020-120000-sup002"

    def test_import_failure_writes_dependency_missing(self):
        from mcpserver import ml_worker
        args = argparse.Namespace(task_id=self.TASK_ID, task_type="classification",
                                  algorithm="random_forest", params="{}",
                                  data_version="ml1m-clean-v1", tag="")
        task_dir = os.path.join(self.var_dir, "tasks", self.TASK_ID)
        os.makedirs(task_dir, exist_ok=True)
        # sys.modules 里放 None：`from ml.api import ...` 会以 ImportError 失败
        with mock.patch.dict(sys.modules, {"ml.api": None}), \
                mock.patch.object(sys, "stdout", new=io.StringIO()) as captured:
            code = ml_worker._run_training(args)
        self.assertEqual(code, 3)
        with open(os.path.join(task_dir, "status.json"), encoding="utf-8") as fh:
            status = json.load(fh)
        self.assertEqual(status["status"], "failed")
        self.assertEqual(status["errors"][0]["code"], contract.CODE_DEPENDENCY_MISSING)
        payload = json.loads(captured.getvalue().strip().splitlines()[-1])
        self.assertFalse(payload["ok"])


if __name__ == "__main__":
    unittest.main()
