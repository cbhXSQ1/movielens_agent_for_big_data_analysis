# -*- coding: utf-8 -*-
"""ml/features.py —— 特征构造（分类的样本特征、聚类与降维的用户画像）。

三条纪律：

1. **防时间泄漏**：分类样本的"历史统计特征"（用户历史均分/活跃度、电影历史均分/热度）
   由**按时间排序的前缀聚合**得到 —— 每条样本只看它自己时间戳之前的记录；
   单测里有一条"塞进未来数据，历史样本的特征不变"的断言守着这件事。
2. **同一份画像两处共用**：聚类与降维都用同一份用户画像（18 个类型占比 + 4 个行为统计），
   只用训练期（≤T1）的评分构建，保证"同画像、同 k"的可比性。
3. **编码器可复现**：分箱边界、标准化参数都以纯数据结构保存并随模型落盘，
   预测或复现时按同一套参数重建特征 —— 否则同一个用户在不同时刻会得到不同特征。

本模块只依赖 numpy（不依赖 sklearn）。
"""

import numpy as np

from . import config
from .errors import TrainingError

#: 性别取值（ML-1M：M/F）。
GENDERS = ("M", "F")
#: 年龄分段取值（ML-1M 原始取值，按数值排序）。
AGES = (1, 18, 25, 35, 45, 50, 56)
#: 职业取值（ML-1M：0～20）。
OCCUPATIONS = tuple(range(21))

#: 语义特征清单（写进模型元数据，便于对端与评审核对"到底用了哪些特征"）。
SEMANTIC_FEATURES = (
    "gender", "age", "occupation", "genres",
    "user_history_avg", "user_history_count",
    "movie_history_avg", "movie_history_count",
)

_USER_BLOCK = len(GENDERS) + len(AGES) + len(OCCUPATIONS)
_MOVIE_BLOCK = len(config.GENRES)


# ---------------------------------------------------------------------------
# 前缀聚合（防泄漏的核心）
# ---------------------------------------------------------------------------

def prefix_aggregates(user, movie, rating, ts):
    """按时间排序做前缀聚合，返回每条记录**此前**的用户/电影统计。

    返回四个等长数组：`user_avg`、`user_count`、`movie_avg`、`movie_count`；
    没有历史记录的位置 `user_avg`/`movie_avg` 为 `nan`、计数为 0（由调用方用训练期均值填充）。

    实现是一次按时间顺序的遍历：取当前聚合值 → 写进结果 → 再把本条计入。
    因此每条样本只看到它之前的记录 —— 哪怕后面追加未来数据，历史样本的特征也不会变。
    """
    n = len(ts)
    order = np.argsort(ts, kind="stable")
    user_avg = np.full(n, np.nan, dtype=np.float64)
    user_count = np.zeros(n, dtype=np.int64)
    movie_avg = np.full(n, np.nan, dtype=np.float64)
    movie_count = np.zeros(n, dtype=np.int64)

    user_sum, user_n = {}, {}
    movie_sum, movie_n = {}, {}
    for idx in order:
        uid = int(user[idx])
        mid = int(movie[idx])
        r = float(rating[idx])

        n_u = user_n.get(uid, 0)
        if n_u:
            user_avg[idx] = user_sum[uid] / n_u
        user_count[idx] = n_u

        n_m = movie_n.get(mid, 0)
        if n_m:
            movie_avg[idx] = movie_sum[mid] / n_m
        movie_count[idx] = n_m

        user_sum[uid] = user_sum.get(uid, 0.0) + r
        user_n[uid] = n_u + 1
        movie_sum[mid] = movie_sum.get(mid, 0.0) + r
        movie_n[mid] = n_m + 1

    return user_avg, user_count, movie_avg, movie_count


def split_masks(ts, t1_epoch, t2_epoch):
    """按时间边界切出训练 / 验证 / 测试三份掩码（训练 ≤T1、验证 (T1,T2]、测试 >T2）。"""
    train = ts <= t1_epoch
    valid = (ts > t1_epoch) & (ts <= t2_epoch)
    test = ts > t2_epoch
    return train, valid, test


# ---------------------------------------------------------------------------
# 静态属性编码（用户属性与电影类型）
# ---------------------------------------------------------------------------

def user_vector(attrs):
    """用户属性 → 29 维独热（性别 2 + 年龄 7 + 职业 20/21 个取值）。

    未知取值不给类别、也不伪造：对应位全 0（"忽略"，与编码器的 handle_unknown 同义）。
    """
    vec = np.zeros(_USER_BLOCK, dtype=np.float32)
    if not attrs:
        return vec
    gender = attrs.get("gender")
    age = attrs.get("age")
    occ = attrs.get("occupation")
    if gender in GENDERS:
        vec[GENDERS.index(gender)] = 1.0
    if age in AGES:
        vec[len(GENDERS) + AGES.index(age)] = 1.0
    if occ in OCCUPATIONS:
        vec[len(GENDERS) + len(AGES) + OCCUPATIONS.index(occ)] = 1.0
    return vec


def movie_vector(attrs):
    """电影类型多热 → 18 维（一部电影可属于多个类型，对应位置都为 1）。"""
    vec = np.zeros(_MOVIE_BLOCK, dtype=np.float32)
    for genre in (attrs or {}).get("genres") or []:
        if genre in config.GENRES:
            vec[config.GENRES.index(genre)] = 1.0
    return vec


def static_names():
    names = ["gender_%s" % g for g in GENDERS]
    names += ["age_%s" % a for a in AGES]
    names += ["occ_%s" % o for o in OCCUPATIONS]
    names += ["genre_%s" % g for g in config.GENRES]
    return names


def _lookup_tables(users_table, movies_table):
    """把用户/电影属性展开成可按编号下标取用的矩阵（比逐行查字典快一个量级）。"""
    max_user = max(users_table) if users_table else 0
    max_movie = max(movies_table) if movies_table else 0
    user_lut = np.zeros((max_user + 1, _USER_BLOCK), dtype=np.float32)
    movie_lut = np.zeros((max_movie + 1, _MOVIE_BLOCK), dtype=np.float32)
    for uid, attrs in users_table.items():
        user_lut[uid] = user_vector(attrs)
    for mid, attrs in movies_table.items():
        movie_lut[mid] = movie_vector(attrs)
    return user_lut, movie_lut


# ---------------------------------------------------------------------------
# 分箱（朴素贝叶斯要把连续值转成类别）
# ---------------------------------------------------------------------------

def fit_bin_edges(values, bins=None):
    """用训练集的分位数定分箱边界。

    为什么用分位数而不是等宽：评分类数据分布很偏（大量 4 分），等宽分箱会让某一箱几乎没有样本，
    条件概率估计不稳；分位数分箱保证每箱都有足够样本。
    """
    bins = bins or config.NUMERIC_BINS
    arr = np.asarray(values, dtype=np.float64)
    arr = arr[~np.isnan(arr)]
    if arr.size == 0:
        return np.asarray([], dtype=np.float64)
    quantiles = np.linspace(0.0, 1.0, bins + 1)[1:-1]
    return np.unique(np.quantile(arr, quantiles))


def apply_bins(values, edges):
    """把数值按边界转成类别编号（0 起；无边界时全归 0）。"""
    arr = np.asarray(values, dtype=np.float64)
    if edges is None or len(edges) == 0:
        return np.zeros(arr.shape, dtype=np.int16)
    arr = np.where(np.isnan(arr), -np.inf, arr)
    return np.digitize(arr, edges).astype(np.int16)


# ---------------------------------------------------------------------------
# 分类样本矩阵
# ---------------------------------------------------------------------------

def build_classifier_matrix(ratings, users_table, movies_table, t1_epoch, fill=None):
    """构造分类用的样本矩阵（全部评分行）。

    特征 = 47 维静态独热/多热 + 4 维历史统计 = 51 维；
    缺失的历史统计用**训练期**均值填充（避免把验证/测试期的信息带进训练输入）。
    返回矩阵、标签、特征名、编码器（分箱边界 + 填充值）与中间量。
    """
    user = ratings["user"]
    movie = ratings["movie"]
    rating = ratings["rating"]
    ts = ratings["ts"]

    user_avg, user_count, movie_avg, movie_count = prefix_aggregates(user, movie, rating, ts)
    train_mask = ts <= t1_epoch
    if fill is None:
        train_rating = rating[train_mask]
        base = float(train_rating.mean()) if train_rating.size else float(np.mean(rating))
        fill = {"rating": base}

    user_avg_f = np.where(np.isnan(user_avg), fill["rating"], user_avg).astype(np.float32)
    movie_avg_f = np.where(np.isnan(movie_avg), fill["rating"], movie_avg).astype(np.float32)
    numeric = np.column_stack([user_avg_f,
                               user_count.astype(np.float32),
                               movie_avg_f,
                               movie_count.astype(np.float32)]).astype(np.float32)

    user_lut, movie_lut = _lookup_tables(users_table, movies_table)
    static = np.hstack([user_lut[user], movie_lut[movie]]).astype(np.float32)

    X = np.hstack([static, numeric]).astype(np.float32)
    y = (rating >= config.POSITIVE_RATING).astype(np.int8)

    names = static_names() + list(NUMERIC_NAMES)
    encoders = {
        "fill": fill,
        "numeric_edges": [fit_bin_edges(numeric[:, i], config.NUMERIC_BINS)
                          for i in range(numeric.shape[1])],
        "numeric_start": int(static.shape[1]),
        "semantic_features": list(SEMANTIC_FEATURES),
    }
    return {"X": X, "y": y, "feature_names": names, "encoders": encoders,
            "numeric": numeric, "ts": ts, "user": user, "movie": movie, "rating": rating,
            "user_lut": user_lut, "movie_lut": movie_lut}


#: 数值特征名（顺序固定：用户历史均分、用户活跃度、电影历史均分、电影热度）。
NUMERIC_NAMES = ("user_history_avg", "user_history_count",
                 "movie_history_avg", "movie_history_count")


def to_categorical(X, encoders):
    """把连续矩阵转成朴素贝叶斯用的整数类别矩阵（数值按训练期分箱边界离散化）。"""
    start = encoders["numeric_start"]
    parts = [X[:, :start].astype(np.int16)]
    for offset, edges in enumerate(encoders["numeric_edges"]):
        parts.append(apply_bins(X[:, start + offset], edges).reshape(-1, 1))
    return np.hstack(parts).astype(np.int16)


# ---------------------------------------------------------------------------
# 用户画像（聚类与降维共用）
# ---------------------------------------------------------------------------

def build_user_profile(ratings, movies_table, t1_epoch):
    """用训练期（≤T1）的评分构造用户画像。

    返回：
      * `user_ids`：参与构建的用户编号；
      * `matrix`：(用户数, 22)：前 18 列类型偏好占比（行内归一化）、后 4 列行为统计（原值）；
      * `raw_behaviors`：未标准化的行为统计（群体画像与标签用）；
      * `global_genre_share`：全体用户的类型占比均值（判断"某群体偏好什么"的基准）；
      * `train_window`：参与构建的评分条数与用户数。
    """
    mask = ratings["ts"] <= t1_epoch
    user = ratings["user"][mask]
    movie = ratings["movie"][mask]
    rating = ratings["rating"][mask].astype(np.float64)
    ts = ratings["ts"][mask]
    if user.size == 0:
        raise TrainingError(
            "DEPENDENCY_MISSING",
            "训练期（≤T1）没有任何评分记录，无法构造用户画像",
            missing="训练期评分", reason="empty_train_window")

    genre_index = {g: i for i, g in enumerate(config.GENRES)}
    users = np.unique(user)
    pos = {uid: i for i, uid in enumerate(users)}
    genre_sum = np.zeros((len(users), len(config.GENRES)), dtype=np.float64)
    rating_sum = np.zeros(len(users), dtype=np.float64)
    rating_sq = np.zeros(len(users), dtype=np.float64)
    last_ts = np.full(len(users), -1, dtype=np.int64)
    counts = np.zeros(len(users), dtype=np.int64)

    for uid, mid, r, t in zip(user, movie, rating, ts):
        i = pos[int(uid)]
        counts[i] += 1
        rating_sum[i] += r
        rating_sq[i] += r * r
        if t > last_ts[i]:
            last_ts[i] = int(t)
        attrs = movies_table.get(int(mid))
        if not attrs:
            continue
        genres = [g for g in attrs.get("genres") or [] if g in genre_index]
        if not genres:
            continue
        share = 1.0 / len(genres)          # 一部电影属于多个类型时，把这条评分平摊
        for g in genres:
            genre_sum[i, genre_index[g]] += share

    totals = counts.astype(np.float64)
    shares = np.where(totals[:, None] > 0, genre_sum / totals[:, None], 0.0)
    avg_rating = np.where(counts > 0, rating_sum / np.maximum(counts, 1), 0.0)
    var = np.where(counts > 0, rating_sq / np.maximum(counts, 1) - avg_rating ** 2, 0.0)
    std_rating = np.sqrt(np.maximum(var, 0.0))
    recency_days = np.where(last_ts >= 0, (t1_epoch - last_ts) / 86400.0, 0.0)

    matrix = np.hstack([
        shares.astype(np.float32),
        np.column_stack([counts.astype(np.float32), avg_rating.astype(np.float32),
                         std_rating.astype(np.float32), recency_days.astype(np.float32)]),
    ]).astype(np.float32)

    return {
        "user_ids": users,
        "matrix": matrix,
        "raw_behaviors": {
            "activity": counts.astype(np.int64),
            "avg_rating": avg_rating.astype(np.float32),
            "rating_std": std_rating.astype(np.float32),
            "recency": recency_days.astype(np.float32),
        },
        "global_genre_share": shares.mean(axis=0).astype(np.float32),
        "train_window": {"ratings": int(user.size), "users": int(len(users)),
                         "until": int(t1_epoch)},
    }


def standardize(matrix, n_genres=None):
    """连续维标准化：前 18 列（占比）保持原样，后 4 列（行为统计）做 z 分数。

    返回 (标准化后的矩阵, 参数)；参数随模型落盘，预测/复现时按同一套参数变换。
    """
    n_genres = len(config.GENRES) if n_genres is None else n_genres
    out = np.asarray(matrix, dtype=np.float64).copy()
    tail = out[:, n_genres:]
    mean = tail.mean(axis=0)
    std = tail.std(axis=0)
    std = np.where(std < 1e-9, 1.0, std)
    out[:, n_genres:] = (tail - mean) / std
    return out.astype(np.float32), {"mean": mean.tolist(), "std": std.tolist()}


def apply_standardize(matrix, params, n_genres=None):
    """按已保存的参数做同样的标准化（预测或复现时用）。"""
    n_genres = len(config.GENRES) if n_genres is None else n_genres
    out = np.asarray(matrix, dtype=np.float32).copy()
    mean = np.asarray(params["mean"], dtype=np.float32)
    std = np.asarray(params["std"], dtype=np.float32)
    out[:, n_genres:] = (out[:, n_genres:] - mean) / std
    return out
