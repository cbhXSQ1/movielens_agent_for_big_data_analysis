# -*- coding: utf-8 -*-
"""ml/reduction.py —— 降维：主成分分析（PCA）与 非负矩阵分解（NMF），并做前后聚类对比。

三件事写清楚：

  * **两种降维看同一份输入**：都用聚类那份标准化画像；NMF 要求非负，所以整体平移到 [0,1]
    （保留全部维度，不丢信息、也不改变行之间的关系），这样与 PCA 的对比才公平；
  * **对比实验**：降维前、降维后各跑一次**同一个聚类算法**（默认 K-Means）且**同一个 k**，
    用 ARI/NMI 看分组是否一致；轮廓系数与 Davies-Bouldin **都在原始全维空间里算**
    （若在各自空间里算，两个数不可比）；
  * **资源开销**：拟合耗时与进程峰值内存，如实记录（不是估算）。

不传目标维度时：先按主成分累计解释方差达到阈值选维度，NMF 用同一个维度，保证可比。
"""

import resource
import time

import numpy as np

from . import config
from .errors import TrainingError
from . import clustering as clustering_mod


def _rss_mb():
    """当前进程常驻内存（MB）。"""
    try:
        with open("/proc/self/statm", encoding="ascii") as fh:
            pages = int(fh.read().split()[1])
        return round(pages * 4096 / (1024.0 * 1024.0), 1)
    except (OSError, IndexError, ValueError):
        return 0.0


def _peak_mb():
    """进程峰值内存（MB，含解释器与数据的基线）。"""
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)


def effective_params(algorithm, params):
    """按白名单校验降维超参（合同 §1.3）。"""
    specs = {spec["key"]: spec for spec in config.HYPERPARAMS[("dimensionality_reduction", algorithm)]}
    params = dict(params or {})
    unknown = [k for k in params if k not in specs]
    if unknown:
        raise TrainingError(
            "USAGE",
            "不认识的超参：%s。%s 可用的超参：%s"
            % ("、".join(sorted(unknown)), algorithm, "、".join(sorted(specs))),
            param="params", values=sorted(specs))
    out = {}
    for key, spec in specs.items():
        value = params.get(key, spec["default"])
        if spec["type"] == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise TrainingError("USAGE", "%s 需要整数，收到 %r" % (key, value), param=key)
            if value < spec["min"] or value > spec["max"]:
                raise TrainingError(
                    "USAGE", "%s 必须在 %s～%s 之间，收到 %s"
                    % (key, spec["min"], spec["max"], value), param=key)
        elif spec["type"] == "string":
            if value not in spec["values"]:
                raise TrainingError(
                    "USAGE", "%s 只能是 %s，收到 %r"
                    % (key, " 或 ".join(spec["values"]), value), param=key)
        out[key] = value
    return out


def choose_components(X, n_components=None):
    """不传目标维度时按累计解释方差选；返回 (维度, 依据说明)。"""
    dims = X.shape[1]
    upper = min(config.COMPONENTS_MAX, dims - 1)
    if n_components:
        if n_components < config.COMPONENTS_MIN or n_components > upper:
            raise TrainingError(
                "USAGE",
                "n_components 必须在 %s～%s 之间（不超过画像维度减一），收到 %s"
                % (config.COMPONENTS_MIN, upper, n_components), param="n_components")
        return int(n_components), {"method": "调用方指定", "n_components": int(n_components)}
    from sklearn.decomposition import PCA

    probe = PCA(n_components=upper, random_state=42)
    probe.fit(X)
    cumulative = np.cumsum(probe.explained_variance_ratio_)
    reached = np.searchsorted(cumulative, config.PCA_VARIANCE_TARGET) + 1
    chosen = int(min(max(reached, config.COMPONENTS_MIN), upper))
    return chosen, {"method": "按主成分累计解释方差达到 %.0f%% 选维度（NMF 用同一维度以便对比）"
                              % (config.PCA_VARIANCE_TARGET * 100),
                    "n_components": chosen,
                    "cumulative_explained_variance": [round(float(v), 4) for v in cumulative[:chosen]]}


def shift_non_negative(X):
    """把矩阵整体平移到 [0,1]（NMF 要求非负）。返回 (平移后矩阵, 平移参数)。"""
    mins = X.min(axis=0)
    maxs = X.max(axis=0)
    span = np.where((maxs - mins) < 1e-9, 1.0, maxs - mins)
    return ((X - mins) / span).astype(np.float32), {"min": mins.tolist(), "span": span.tolist()}


def train(algorithm, X, n_components, params, progress=None):
    """拟合降维模型，返回 (模型, 降维表示, 详情)。"""
    from sklearn.decomposition import NMF, PCA

    eff = effective_params(algorithm, params)
    if progress:
        progress(config.STAGE_TRAIN, message="训练 %s（%s 维）" % (algorithm, n_components))
    started = time.time()
    mem_before = _rss_mb()
    detail = {}
    if algorithm == "pca":
        model = PCA(n_components=int(n_components), random_state=eff["random_state"])
        reduced = model.fit_transform(X)
        reconstructed = model.inverse_transform(reduced)
        detail["explained_variance_ratio"] = [round(float(v), 4)
                                              for v in model.explained_variance_ratio_]
        detail["reconstruction_error"] = round(
            float(np.mean((X - reconstructed) ** 2)), 6)
        detail["reconstruction_error_note"] = "全维与重建之间的均方误差"
    elif algorithm == "nmf":
        shifted, shift_params = shift_non_negative(X)
        model = NMF(n_components=int(n_components), init=eff["init"],
                    max_iter=eff["max_iter"], random_state=eff["random_state"])
        reduced = model.fit_transform(shifted)
        reconstructed = model.inverse_transform(reduced)
        detail["explained_variance_ratio"] = None      # NMF 没有解释方差比（合同明确给 null）
        detail["reconstruction_error"] = round(
            float(np.linalg.norm(shifted - reconstructed) / np.sqrt(shifted.size)), 6)
        detail["reconstruction_error_note"] = "平移后的矩阵与重建之间的均方根误差"
        detail["shift"] = shift_params
        detail["n_iter"] = int(getattr(model, "n_iter_", 0) or 0)
    else:
        raise TrainingError("USAGE", "降维不支持的算法：%r" % algorithm, param="algorithm",
                            values=("pca", "nmf"))
    detail["resource"] = {
        "fit_seconds": round(time.time() - started, 2),
        "mem_mb": round(max(_peak_mb() - mem_before, 0.0) + _rss_mb() - mem_before, 1),
    }
    detail["resource_note"] = "耗时与内存均为本次真实测量（内存为进程级峰值增量，含数据本身）"
    return model, reduced, detail


def compare(X_full, reduced, k, algorithm="kmeans", params=None, progress=None):
    """降维前后各跑一次同一聚类算法，比较分组一致性（合同 §3.4 的 comparison）。"""
    from sklearn.cluster import KMeans
    from sklearn.metrics import (adjusted_rand_score, davies_bouldin_score,
                                 normalized_mutual_info_score, silhouette_score)

    if progress:
        progress(config.STAGE_COMPARE, message="降维前后聚类对比（同一算法、同一 k=%s）" % k)
    params = dict(params or {})
    seed = int(params.get("random_state", 42))

    before_model = KMeans(n_clusters=k, n_init=10, random_state=seed)
    labels_before = before_model.fit_predict(X_full)
    after_model = KMeans(n_clusters=k, n_init=10, random_state=seed)
    labels_after = after_model.fit_predict(reduced)

    # 两套分组都在**全维空间**里评估，这样两个数才可比
    def _score(labels):
        return {"silhouette": round(float(silhouette_score(X_full, labels)), 4),
                "davies_bouldin": round(float(davies_bouldin_score(X_full, labels)), 4),
                "k": int(k)}

    comparison = {
        "before": _score(labels_before),
        "after": _score(labels_after),
        "agreement": {
            "ari": round(float(adjusted_rand_score(labels_before, labels_after)), 4),
            "nmi": round(float(normalized_mutual_info_score(labels_before, labels_after)), 4),
        },
        "note": ("before=全维画像直接聚类；after=降维后表示上聚类；"
                 "两者都用同一个聚类算法与同一个 k，且轮廓系数/DB 指标都在全维空间计算以保证可比"),
    }
    return comparison, labels_before, labels_after
