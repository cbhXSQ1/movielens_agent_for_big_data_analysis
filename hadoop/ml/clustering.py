# -*- coding: utf-8 -*-
"""ml/clustering.py —— 用户群体聚类：K-Means 与 凝聚层次（Ward）。

要点：

  * **同一份画像、同一个簇数**：两种算法都用调用方给的那份标准化画像与同一个 k，
    否则"两种算法比较"就失去意义；
  * **不传 k 时由端侧选**：按轮廓系数最大来选（在 K-Means 上扫描候选簇数，
    把候选与分数一并回给调用方，便于解释"为什么选这个 k"），结果可复现；
  * **群体画像要能读懂**：每个群体给规模、偏离最大的 2～3 个类型、行为均值，
    以及由数据驱动生成的中文标签 —— 标签规则写死在这里，同样的输入必得同样的标签。
"""

import numpy as np

from . import config
from .errors import TrainingError

#: 类型英文 → 中文（画人群像与"偏好"标签要用中文，且这是课程场景的展示语言）。
GENRE_CN = {
    "Action": "动作", "Adventure": "冒险", "Animation": "动画", "Children's": "儿童",
    "Comedy": "喜剧", "Crime": "犯罪", "Documentary": "纪录", "Drama": "剧情",
    "Fantasy": "奇幻", "Film-Noir": "黑色电影", "Horror": "恐怖", "Musical": "音乐",
    "Mystery": "悬疑", "Romance": "爱情", "Sci-Fi": "科幻", "Thriller": "惊悚",
    "War": "战争", "Western": "西部",
}


def effective_params(algorithm, params):
    """按白名单校验聚类超参（合同 §1.3：K-Means 有 random_state 与 max_iter，层次只有 random_state）。"""
    from .classification import effective_params as _common

    specs = {spec["key"]: spec for spec in config.HYPERPARAMS[("clustering", algorithm)]}
    params = dict(params or {})
    unknown = [k for k in params if k not in specs]
    if unknown:
        raise TrainingError(
            "USAGE",
            "不认识的超参：%s。%s 可用的超参：%s"
            % ("、".join(sorted(unknown)), algorithm, "、".join(sorted(specs))),
            param="params", values=sorted(specs))
    # 共用那套校验（上下界、类型），这里只负责挑出该算法的键
    return _common_subset(specs, params)


def _common_subset(specs, params):
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
        out[key] = value
    return out


def select_k(X, random_state=42, candidates=None):
    """按轮廓系数选簇数；返回 (k, 候选明细)。候选明细进指标，用来解释"为什么是这个 k"。"""
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score

    candidates = candidates or config.K_CANDIDATES
    upper = min(max(candidates), max(2, X.shape[0] - 1))
    rows = []
    best_k, best_score = None, -2.0
    for k in candidates:
        if k > upper:
            continue
        model = KMeans(n_clusters=k, n_init=10, random_state=random_state)
        labels = model.fit_predict(X)
        if len(set(labels)) < 2:
            continue
        score = float(silhouette_score(X, labels))
        rows.append({"k": int(k), "silhouette": round(score, 4)})
        if score > best_score:
            best_k, best_score = int(k), score
    if best_k is None:
        raise TrainingError("TASK_FAILED", "无法在候选簇数上完成选择（样本太少或全同）",
                            reason="k_selection_failed")
    return best_k, {"method": "轮廓系数最大（在 K-Means 上扫描候选簇数，两种聚类共用该 k）",
                    "candidates": rows}


def train(algorithm, X, k, params, progress=None):
    """在标准化画像上拟合聚类模型，返回 (模型, 标签数组, 生效超参)。"""
    from sklearn.cluster import AgglomerativeClustering, KMeans

    eff = effective_params(algorithm, params)
    if progress:
        progress(config.STAGE_TRAIN, message="训练 %s（k=%s）" % (algorithm, k))
    if algorithm == "kmeans":
        model = KMeans(n_clusters=k, n_init=10, random_state=eff["random_state"],
                       max_iter=eff["max_iter"])
        labels = model.fit_predict(X)
    elif algorithm == "hierarchical":
        model = AgglomerativeClustering(n_clusters=k, linkage="ward")
        labels = model.fit_predict(X)
    else:
        raise TrainingError("USAGE", "聚类不支持的算法：%r" % algorithm, param="algorithm",
                            values=list(config.HYPERPARAMS.keys() and
                                        ("kmeans", "hierarchical")))
    return model, labels, eff


def metrics(X, labels, k, raw_behaviors, global_genre_share, k_selection=None):
    """聚类指标与群体画像（合同 §3.3）。"""
    from sklearn.metrics import davies_bouldin_score, silhouette_score

    silhouette = float(silhouette_score(X, labels))
    davies = float(davies_bouldin_score(X, labels))

    clusters = []
    activity_all = raw_behaviors["activity"]
    avg_all = raw_behaviors["avg_rating"]
    std_all = raw_behaviors["rating_std"]
    rec_all = raw_behaviors["recency"]
    activity_median = float(np.median(activity_all)) if activity_all.size else 0.0
    avg_mean = float(np.mean(avg_all)) if avg_all.size else 0.0

    for cluster_id in range(k):
        mask = labels == cluster_id
        size = int(mask.sum())
        if size == 0:
            clusters.append({"cluster_id": int(cluster_id), "size": 0,
                             "label": "空群体", "top_genres": [],
                             "behavior": {"activity": 0.0, "avg_rating": 0.0,
                                          "rating_std": 0.0, "recency": 0.0}})
            continue
        share = X[mask, :len(config.GENRES)].mean(axis=0)
        deviation = share - global_genre_share
        top_idx = np.argsort(-deviation)[:3]
        top_genres = [config.GENRES[i] for i in top_idx if deviation[i] > 0]
        behavior = {
            "activity": round(float(activity_all[mask].mean()), 2),
            "avg_rating": round(float(avg_all[mask].mean()), 4),
            "rating_std": round(float(std_all[mask].mean()), 4),
            "recency": round(float(rec_all[mask].mean()), 2),
        }
        label = make_label(top_genres, behavior, activity_median, avg_mean)
        clusters.append({
            "cluster_id": int(cluster_id),
            "size": size,
            "label": label,
            "top_genres": top_genres,
            "behavior": behavior,
        })

    out = {
        "k": int(k),
        "silhouette": round(silhouette, 4),
        "davies_bouldin": round(davies, 4),
        "clusters": clusters,
        "cluster_size_distribution": sorted([c["size"] for c in clusters], reverse=True),
    }
    if k_selection:
        out["k_selection"] = k_selection
    return out


def make_label(top_genres, behavior, activity_median, avg_mean):
    """生成中文群体标签：偏好 + 活跃度 + 评分倾向（规则固定、可复现）。

    三段的取值都由数据决定（与全体比较），不是拍脑袋的形容词：
    类型取偏离最大的两三个；活跃度与中位数比；评分倾向与全体均分比。
    """
    parts = []
    if top_genres:
        names = "/".join(GENRE_CN.get(g, g) for g in top_genres[:2])
        parts.append("%s偏好" % names)
    activity = behavior["activity"]
    if activity_median > 0:
        ratio = activity / activity_median
        if ratio >= 1.3:
            parts.append("高活跃")
        elif ratio <= 0.7:
            parts.append("低活跃")
    delta = behavior["avg_rating"] - avg_mean
    if delta >= 0.15:
        parts.append("评分偏宽")
    elif delta <= -0.15:
        parts.append("评分偏严")
    if not parts:
        parts.append("行为均衡")
    return " · ".join(parts)
