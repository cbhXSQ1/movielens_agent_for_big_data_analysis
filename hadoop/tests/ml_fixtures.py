# -*- coding: utf-8 -*-
"""tests/ml_fixtures.py —— 机器学习测试用的小规模人造数据与"离线仓库"。

为什么要造假数据而不是用真实发布区：单元测试不该依赖集群是否在跑、也不该受数据版本变化影响。
人造数据是**可手算的**：少量用户/电影、明确的时间阶段、评分里带一点真实信号，
这样指标量级可以预期，断言才有意义。

真实数据的冒烟是另一回事（跑一次、看量级），不放在单元测试里。
"""

import contextlib
import json
import os
import shutil
import tempfile
from unittest import mock

from ml import config
from ml import datasets

#: 人造数据的时间边界：与真实方案一致的取值（2001-12-31 / 2002-06-30）。
T1_ISO = "2001-12-31T23:59:59Z"
T2_ISO = "2002-06-30T23:59:59Z"

_GENRES = list(config.GENRES)


def _rng(seed):
    import numpy as np

    return np.random.RandomState(seed)


def make_tables(directory, n_users=300, n_movies=80, n_ratings=6000, seed=7):
    """写三张表（发布区格式：`::` 分隔、ISO-8859-1），返回目录。

    评分里带信号：用户偏宽/偏严 + 电影平均质量 + 噪声，这样"是否 ≥4 分"是可学的；
    同时刻意让 15% 的用户属性缺失（性别与年龄为空），把真实数据里的脏行也覆盖到。
    """
    import numpy as np

    rng = _rng(seed)
    user_bias = rng.normal(0, 0.8, n_users + 1)
    movie_bias = rng.normal(0, 0.8, n_movies + 1)

    user_ids = rng.randint(1, n_users + 1, n_ratings)
    movie_ids = rng.randint(1, n_movies + 1, n_ratings)
    score = 3.6 + user_bias[user_ids] + movie_bias[movie_ids] + rng.normal(0, 1.0, n_ratings)
    ratings = np.clip(np.round(score), 1, 5).astype(int)
    # 时间：60% 在 T1 之前、20% 在 (T1,T2]、20% 在 T2 之后
    t1 = datasets.to_epoch(T1_ISO)
    t2 = datasets.to_epoch(T2_ISO)
    spans = np.concatenate([
        rng.randint(t1 - 200 * 86400, t1, int(n_ratings * 0.6)),
        rng.randint(t1 + 1, t2, int(n_ratings * 0.2)),
        rng.randint(t2 + 1, t2 + 100 * 86400, n_ratings - int(n_ratings * 0.6) - int(n_ratings * 0.2)),
    ])
    spans = np.sort(spans)

    users_path = os.path.join(directory, "users.dat")
    movies_path = os.path.join(directory, "movies.dat")
    ratings_path = os.path.join(directory, "ratings.dat")

    with open(users_path, "w", encoding="iso-8859-1", newline="\n") as fh:
        for uid in range(1, n_users + 1):
            if uid % 7 == 0:            # 属性缺失的脏行（真实数据里确实存在）
                fh.write("%d::::::%d::%05d\n" % (uid, uid % 21, 10000 + uid))
                continue
            gender = "M" if uid % 2 else "F"
            age = (1, 18, 25, 35, 45, 50, 56)[uid % 7]
            fh.write("%d::%s::%d::%d::%05d\n" % (uid, gender, age, uid % 21, 10000 + uid))

    with open(movies_path, "w", encoding="iso-8859-1", newline="\n") as fh:
        for mid in range(1, n_movies + 1):
            genres = "|".join([_GENRES[mid % len(_GENRES)],
                               _GENRES[(mid * 3) % len(_GENRES)]])
            fh.write("%d::Movie %d (2000)::%s\n" % (mid, mid, genres))

    with open(ratings_path, "w", encoding="iso-8859-1", newline="\n") as fh:
        for uid, mid, rating, stamp in zip(user_ids, movie_ids, ratings, spans):
            fh.write("%d::%d::%d::%d\n" % (uid, mid, rating, stamp))

    return directory


def _fake_published(directory, version="fixture-v1"):
    """替身：把"从发布区取数"换成"读本地人造表"。"""
    def loader(repo_root, data_version=None, progress=None, force_refresh=False):
        if progress:
            progress(config.STAGE_LOAD_DATA, message="测试用本地人造数据")
        return {"dir": directory, "files": dict(datasets.TABLE_FILES), "reused": True,
                "hashes": {"fixture": "1"}, "data_version": data_version or version}
    return loader


def _fake_boundaries(version="fixture-v1"):
    def loader(repo_root, data_version=None):
        return {"T1": T1_ISO, "T2": T2_ISO, "data_version": version, "source": "测试固定边界"}
    return loader


@contextlib.contextmanager
def offline_repo(n_users=300, n_movies=80, n_ratings=6000, seed=7):
    """一个"离线仓库"：临时目录 + 人造三表 + 模型根目录指向临时目录。

    退出时恢复 `config.ML_ROOT_REL` 并删除临时目录 —— 测试不会污染真实的 `var/ml`。
    """
    tmp = tempfile.mkdtemp(prefix="ml-test-")
    data_dir = os.path.join(tmp, "published")
    os.makedirs(data_dir)
    make_tables(data_dir, n_users=n_users, n_movies=n_movies, n_ratings=n_ratings, seed=seed)

    old_root = config.ML_ROOT_REL
    config.ML_ROOT_REL = os.path.join(tmp, "ml")
    try:
        with mock.patch.object(datasets, "load_published", _fake_published(data_dir)), \
                mock.patch.object(datasets, "load_time_boundaries", _fake_boundaries()):
            yield {"repo_root": tmp, "data_dir": data_dir,
                   "ml_root": config.ML_ROOT_REL,
                   "t1": datasets.to_epoch(T1_ISO), "t2": datasets.to_epoch(T2_ISO)}
    finally:
        config.ML_ROOT_REL = old_root
        shutil.rmtree(tmp, ignore_errors=True)


def load_fixture(repo, data_version=None):
    """把离线仓库里的三表解析出来（供特征层测试直接用）。"""
    users, _bad_u, _partial = datasets.parse_users(
        os.path.join(repo["data_dir"], datasets.TABLE_FILES["users"]))
    movies, _bad_m = datasets.parse_movies(
        os.path.join(repo["data_dir"], datasets.TABLE_FILES["movies"]))
    ratings = datasets.parse_ratings(
        os.path.join(repo["data_dir"], datasets.TABLE_FILES["ratings"]))
    return users, movies, ratings
