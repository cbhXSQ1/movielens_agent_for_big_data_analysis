# -*- coding: utf-8 -*-
"""ml/prediction.py —— 用已存的分类模型预测"某用户对某电影是否 ≥4 分"。

三条要求：

  * **只读模型、不训练**：加载模型包（含编码器与训练时的历史统计快照），构造一行特征即可，
    不做任何全量扫描 —— 单次预测应当是"秒级"的；
  * **同一模型对同一输入给同一答案**：特征所用的历史统计来自**训练时的数据快照**（全量历史），
    不随数据更新而变；这样结果可复现，也避免"同一模型两次调用给出不同答案"这种难以排查的现象；
  * **输入要如实校验**：用户或电影不在数据里 → `USAGE`（说明该编号不在数据里）；
    模型类型不是分类 → `USAGE`（说明该模型是干什么的）。
"""

import os

import numpy as np

from . import config
from .errors import TrainingError
from . import features
from . import persistence


def load_bundle(model_id, repo_root):
    """读模型包与元数据；不存在就按合同回 `MODEL_NOT_FOUND`。"""
    model_dir = os.path.join(repo_root, config.ML_ROOT_REL, model_id)
    meta_path = os.path.join(model_dir, config.META_NAME)
    bundle_path = os.path.join(model_dir, config.MODEL_BUNDLE_NAME)
    if not (os.path.isfile(meta_path) and os.path.isfile(bundle_path)):
        raise TrainingError(
            "MODEL_NOT_FOUND",
            "模型 %s 不存在（可能编号不对，或模型还没训练成功）" % model_id,
            model_id=model_id)
    return model_dir, persistence.read_json(meta_path), persistence.read_bundle(bundle_path)


def _current_aggregates(ratings):
    """训练时快照用的历史统计：全量数据的用户/电影评分和与次数（前缀聚合的终态）。

    **用排好序的数组存，不用字典**：一百万条评分若用四个 Python 字典存，光快照就
    几百兆，而这些字典会被整份塞进模型包、预测时再整份读回内存（实测把模型包撑到
    近五个Ｇ的元凶之一）。改成"编号数组 + 求和数组 + 次数数组"之后同样信息只要
    几十兆，预测时用二分查找取值，代价可以忽略。
    """
    user = ratings["user"].astype(np.int64)
    movie = ratings["movie"].astype(np.int64)
    rating = ratings["rating"].astype(np.float64)

    def _pack(keys, values):
        order = np.argsort(keys, kind="mergesort")
        ids, vals = keys[order], values[order]
        uniq, start = np.unique(ids, return_index=True)
        sums = np.add.reduceat(vals, start)
        counts = np.diff(np.append(start, ids.size))
        return {"ids": uniq, "sum": sums.astype(np.float64), "n": counts.astype(np.int64)}

    return {"user": _pack(user, rating), "movie": _pack(movie, rating)}


def build_snapshot(ratings, users_table, movies_table):
    """训练时保存一份可供预测复用的快照（历史统计 + 维表 + 用户/电影编号集合）。"""
    return {
        "aggregates": _current_aggregates(ratings),
        "users": {int(k): v for k, v in users_table.items()},
        "movies": {int(k): v for k, v in movies_table.items()},
    }


def predict(model_id, user_id, movie_id, repo_root):
    """单条预测：返回合同 §3.6 的字段。"""
    model_dir, meta, bundle = load_bundle(model_id, repo_root)
    if meta.get("task_type") != "classification":
        raise TrainingError(
            "USAGE",
            "模型 %s 不是分类模型（任务类型：%s），无法用于评分预测"
            % (model_id, meta.get("task_type")),
            model_id=model_id, task_type=meta.get("task_type"))

    user_key = _as_id(user_id, "user_id")
    movie_key = _as_id(movie_id, "movie_id")
    snapshot = bundle.get("snapshot") or {}
    users_table = {int(k): v for k, v in (snapshot.get("users") or {}).items()}
    movies_table = {int(k): v for k, v in (snapshot.get("movies") or {}).items()}
    if user_key not in users_table:
        raise TrainingError(
            "USAGE",
            "用户编号 %s 不在训练所用的数据里，无法预测" % user_id,
            param="user_id", value=str(user_id))
    if movie_key not in movies_table:
        raise TrainingError(
            "USAGE",
            "电影编号 %s 不在训练所用的数据里，无法预测" % movie_id,
            param="movie_id", value=str(movie_id))

    encoders = bundle["encoders"]
    fill = encoders.get("fill") or {}
    aggregates = snapshot.get("aggregates") or {}

    def _lookup(block, key):
        """在"编号数组"里二分找这个编号，返回 (求和, 次数)。"""
        ids = (block or {}).get("ids")
        if ids is None or ids.size == 0:
            return 0.0, 0
        pos = int(np.searchsorted(ids, key))
        if pos >= ids.size or int(ids[pos]) != key:
            return 0.0, 0
        return float(block["sum"][pos]), int(block["n"][pos])

    user_sum, n_u = _lookup(aggregates.get("user"), user_key)
    movie_sum, n_m = _lookup(aggregates.get("movie"), movie_key)
    user_avg = (user_sum / n_u) if n_u else float(fill.get("rating", 0.0))
    movie_avg = (movie_sum / n_m) if n_m else float(fill.get("rating", 0.0))

    static = np.hstack([features.user_vector(users_table[user_key]),
                        features.movie_vector(movies_table[movie_key])])
    numeric = np.asarray([[user_avg, n_u, movie_avg, n_m]], dtype=np.float32)
    row = np.hstack([static.reshape(1, -1), numeric]).astype(np.float32)

    model = bundle["model"]
    if hasattr(model, "feature_log_prob_"):
        matrix = features.to_categorical(row, encoders)
        probability = float(model.predict_proba(matrix)[0, 1])
    else:
        probability = float(model.predict_proba(row)[0, 1])

    threshold = float(meta.get("params", {}).get("threshold", 0.5))
    label = 1 if probability >= threshold else 0
    return {
        "model_id": model_id,
        "user_id": str(user_id),
        "movie_id": str(movie_id),
        "prediction": {
            "label": label,
            "probability": round(probability, 4),
            "threshold": round(threshold, 4),
        },
        "model_version": meta.get("version_fingerprint", ""),
    }


def _as_id(value, param):
    """编号统一成整数（数据里是整数编号）；非整数如实拒绝。"""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        raise TrainingError(
            "USAGE", "%s 必须是整数编号，收到 %r" % (param, value), param=param)
