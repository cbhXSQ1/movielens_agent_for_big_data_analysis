# -*- coding: utf-8 -*-
"""迭代二 ML 工具表单测：6 项远端能力的形状（逐字对齐数据契约 §1）。

契约权威：`docs/hadoop/迭代二_机器学习_数据契约.md` §1（输入的参数契约）。
本表只声明参数形状，不产出任何业务数字。
"""
import os
import sys
import unittest

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent import mcp_tools  # noqa: E402

ML_TOOL_NAMES = (
    "train_classifier", "cluster_users", "reduce_dimensions",
    "predict_high_rating", "list_ml_models", "get_ml_model",
)
TRAINING_TOOLS = ("train_classifier", "cluster_users", "reduce_dimensions")


class TestMLToolNames(unittest.TestCase):
    def test_all_six_present(self):
        for name in ML_TOOL_NAMES:
            with self.subTest(tool=name):
                self.assertIn(name, mcp_tools.REMOTE_TOOL_SPECS)

    def test_all_six_are_mcp_source(self):
        for name in ML_TOOL_NAMES:
            with self.subTest(tool=name):
                self.assertEqual("mcp",
                                 mcp_tools.REMOTE_TOOL_SPECS[name]["source"])

    def test_names_match_registry(self):
        for name in ML_TOOL_NAMES:
            with self.subTest(tool=name):
                self.assertEqual(name, mcp_tools.REMOTE_TOOL_SPECS[name]["name"])


class TestTrainingTools(unittest.TestCase):
    def test_training_tools_are_long_running(self):
        for name in TRAINING_TOOLS:
            with self.subTest(tool=name):
                self.assertIs(
                    True, mcp_tools.REMOTE_TOOL_SPECS[name]["long_running"])

    def test_algorithm_enums(self):
        expected = {
            "train_classifier": ["naive_bayes", "random_forest"],
            "cluster_users": ["kmeans", "hierarchical"],
            "reduce_dimensions": ["pca", "nmf"],
        }
        for name, values in expected.items():
            with self.subTest(tool=name):
                spec = mcp_tools.REMOTE_TOOL_SPECS[name]
                self.assertEqual(values, spec["parameters"]["properties"]["algorithm"]["enum"])
                self.assertIn("algorithm", spec["parameters"]["required"])

    def test_cluster_k_bounds(self):
        props = mcp_tools.REMOTE_TOOL_SPECS["cluster_users"]["parameters"]["properties"]
        self.assertEqual("integer", props["k"]["type"])
        self.assertEqual(2, props["k"]["minimum"])
        self.assertEqual(50, props["k"]["maximum"])

    def test_reduce_n_components_bounds(self):
        props = mcp_tools.REMOTE_TOOL_SPECS[
            "reduce_dimensions"]["parameters"]["properties"]
        self.assertEqual("integer", props["n_components"]["type"])
        self.assertEqual(2, props["n_components"]["minimum"])
        self.assertEqual(20, props["n_components"]["maximum"])

    def test_params_is_object(self):
        for name in TRAINING_TOOLS:
            with self.subTest(tool=name):
                props = mcp_tools.REMOTE_TOOL_SPECS[name]["parameters"]["properties"]
                self.assertEqual("object", props["params"]["type"])

    def test_shared_optional_params(self):
        for name in TRAINING_TOOLS:
            with self.subTest(tool=name):
                spec = mcp_tools.REMOTE_TOOL_SPECS[name]
                props = spec["parameters"]["properties"]
                for key in ("data_version", "tag", "params"):
                    self.assertIn(key, props)
                self.assertNotIn("data_version", spec["parameters"]["required"])
                self.assertNotIn("tag", spec["parameters"]["required"])
                self.assertNotIn("params", spec["parameters"]["required"])


class TestPredictAndModelTools(unittest.TestCase):
    def test_predict_high_rating_required(self):
        spec = mcp_tools.REMOTE_TOOL_SPECS["predict_high_rating"]
        self.assertIs(False, spec["long_running"])
        self.assertEqual({"model_id", "user_id", "movie_id"},
                         set(spec["parameters"]["required"]))
        self.assertEqual("string", spec["parameters"]["properties"]["model_id"]["type"])

    def test_list_ml_models_has_no_params(self):
        spec = mcp_tools.REMOTE_TOOL_SPECS["list_ml_models"]
        self.assertIs(False, spec["long_running"])
        self.assertEqual({}, spec["parameters"]["properties"])
        self.assertEqual([], spec["parameters"]["required"])

    def test_get_ml_model_requires_model_id(self):
        spec = mcp_tools.REMOTE_TOOL_SPECS["get_ml_model"]
        self.assertIs(False, spec["long_running"])
        self.assertEqual(["model_id"], spec["parameters"]["required"])
        self.assertEqual("string", spec["parameters"]["properties"]["model_id"]["type"])


class TestEstimatedSeconds(unittest.TestCase):
    def test_estimates(self):
        expected = {
            "train_classifier": 600,
            "cluster_users": 300,
            "reduce_dimensions": 300,
        }
        for name, seconds in expected.items():
            with self.subTest(tool=name):
                self.assertEqual(
                    seconds,
                    mcp_tools.REMOTE_TOOL_SPECS[name]["estimated_seconds"])


if __name__ == "__main__":
    unittest.main()
