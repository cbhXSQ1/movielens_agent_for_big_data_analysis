# -*- coding: utf-8 -*-
"""ml/datasets.py —— 从发布区取三表到本机缓存，并解析成训练用的数组。

分工：
  * `load_published` 负责"取"与"缓存"（按数据版本 + 内容清单判断能否复用）；
  * `parse_*` 负责"读成结构"（评分表 → 四个数组；维表 → 字典）；
  * `load_time_boundaries` 负责时间边界（优先方案声明，读不到用缺省并标注来源）。

为什么不把解析放在取数里：取数会失败在集群一侧（连不上、没发布），解析会失败在文件内容一侧
（字段数不对、编码坏了）——两件事的报错完全不同，分开才好定位。
"""

import json
import os

import numpy as np

from . import config
from .errors import TrainingError
from . import hdfs

#: 三表相对发布区的文件名（数据契约 §2）。
TABLE_FILES = {"users": "users.dat", "movies": "movies.dat", "ratings": "ratings.dat"}
TABLE_ORDER = ("users", "movies", "ratings")


# ---------------------------------------------------------------------------
# 取数与缓存
# ---------------------------------------------------------------------------

def publish_root(hdfs_base="data"):
    """发布区根目录（与迭代一的约定一致：`/data/published/<数据版本>`）。"""
    return "/%s/published" % hdfs_base.strip("/")


def cache_dir(repo_root, data_version):
    return os.path.join(repo_root, config.CACHE_ROOT_REL, data_version)


def _manifest_path(repo_root, data_version):
    return os.path.join(cache_dir(repo_root, data_version), "source_hashes.json")


def _read_manifest(path):
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_manifest(path, payload):
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, sort_keys=True, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def load_published(repo_root, data_version=None, progress=None, force_refresh=False):
    """确保三表已在本机缓存；返回 `{"dir": ..., "files": {...}, "reused": bool, "hashes": {...}}`。

    缓存判定看两样：**内容清单**（发布区里的哈希文件）与本地清单是否一致，以及三个数据文件都在。
    清单一致就复用，不再从集群拉；不一致就重取（例如数据版本被重新发布过）。
    """
    version = data_version or config.DEFAULT_DATA_VERSION
    root = os.path.join(publish_root(), version)
    if progress:
        progress(config.STAGE_LOAD_DATA, message="读取发布区 %s" % version)

    if not hdfs.exists(repo_root, root):
        raise TrainingError(
            "DEPENDENCY_MISSING",
            "发布区里没有数据版本 %s（%s）：训练需要先有一次成功的清洗与发布" % (version, root),
            missing="发布区 %s" % version, data_version=version)

    manifest_hdfs = "%s/.published_hashes.json" % root
    try:
        remote_manifest = json.loads(hdfs.read_text(repo_root, manifest_hdfs,
                                                    what="发布区内容清单"))
    except TrainingError:
        raise
    except ValueError:
        raise TrainingError(
            "DEPENDENCY_MISSING",
            "发布区内容清单不是合法 JSON（%s），无法判断数据是否变化" % manifest_hdfs,
            missing="发布区内容清单", data_version=version)
    if not isinstance(remote_manifest, dict) or not remote_manifest:
        raise TrainingError(
            "DEPENDENCY_MISSING",
            "发布区内容清单为空（%s），无法确认数据完整性" % manifest_hdfs,
            missing="发布区内容清单", data_version=version)

    local_dir = cache_dir(repo_root, version)
    os.makedirs(local_dir, exist_ok=True)
    local_manifest = _read_manifest(_manifest_path(repo_root, version))
    files_ok = all(os.path.isfile(os.path.join(local_dir, name))
                   for name in TABLE_FILES.values())

    reused = bool(not force_refresh and files_ok and local_manifest == remote_manifest)
    if reused:
        if progress:
            progress(config.STAGE_LOAD_DATA, message="复用本机缓存",
                     detail="内容清单一致，不再从集群拉取")
        return {"dir": local_dir, "files": dict(TABLE_FILES), "reused": True,
                "hashes": remote_manifest, "data_version": version}

    for table in TABLE_ORDER:
        name = TABLE_FILES[table]
        hdfs_path = "%s/%s" % (root, name)
        local_path = os.path.join(local_dir, name)
        if progress:
            progress(config.STAGE_LOAD_DATA, message="拉取 %s" % name)
        hdfs.stream_to_file(repo_root, hdfs_path, local_path,
                            what="发布区的 %s" % name)
    _write_manifest(_manifest_path(repo_root, version), remote_manifest)
    return {"dir": local_dir, "files": dict(TABLE_FILES), "reused": False,
            "hashes": remote_manifest, "data_version": version}


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

def _read_lines(path):
    """按 ISO-8859-1 读出所有非空行（发布区三表就是这个编码）。"""
    with open(path, "r", encoding="iso-8859-1", newline="") as fh:
        text = fh.read()
    out = []
    for line in text.split("\n"):
        if line.endswith("\r"):
            line = line[:-1]
        if line:
            out.append(line)
    return out


def parse_users(path):
    """用户表 → `{用户号: {"gender","age","occupation"}}`，以及被跳过的坏行数。

    **宽容处理属性缺失**：原始数据本身就带脏行（性别/年龄为空的用户确实存在，且清洗后仍保留），
    这类用户"在数据里、只是属性缺失" —— 不能当作"用户不存在"，否则预测时会误报。
    缺失的属性记成 None，特征层按"未知类别"处理（全 0，不伪造取值）。
    只有用户号本身不合法、或字段数不对的行才真正跳过（计入坏行）。
    """
    table, bad, partial = {}, 0, 0
    for line in _read_lines(path):
        parts = line.split("::")
        if len(parts) != 5:
            bad += 1
            continue
        try:
            uid = int(parts[0])
        except (TypeError, ValueError):
            bad += 1
            continue
        gender = parts[1] or None
        age = None
        if parts[2].strip():
            try:
                age = int(parts[2])
            except (TypeError, ValueError):
                age = None
        occ = None
        if parts[3].strip():
            try:
                occ = int(parts[3])
            except (TypeError, ValueError):
                occ = None
        if gender is None or age is None or occ is None:
            partial += 1
        table[uid] = {"gender": gender, "age": age, "occupation": occ}
    return table, bad, partial


def parse_movies(path):
    """电影表 → `{电影号: {"title","genres":[...]}}`，以及被跳过的坏行数。"""
    table, bad = {}, 0
    for line in _read_lines(path):
        parts = line.split("::")
        if len(parts) != 3:
            bad += 1
            continue
        try:
            mid = int(parts[0])
        except (TypeError, ValueError):
            bad += 1
            continue
        genres = [g for g in parts[2].split("|") if g]
        table[mid] = {"title": parts[1], "genres": genres}
    return table, bad


def parse_ratings(path):
    """评分表 → 四个定长数组（用户号、电影号、评分、时间戳），以及被跳过的坏行数。

    用定长数组而不是字典列表：一百万行的情况下省内存也快得多，后续按时间排序与聚合都靠它。
    """
    users, movies, ratings, stamps = [], [], [], []
    bad = 0
    for line in _read_lines(path):
        parts = line.split("::")
        if len(parts) != 4:
            bad += 1
            continue
        try:
            users.append(int(parts[0]))
            movies.append(int(parts[1]))
            ratings.append(float(parts[2]))
            stamps.append(int(parts[3]))
        except (TypeError, ValueError):
            bad += 1
            continue
    if not users:
        raise TrainingError(
            "DEPENDENCY_MISSING", "评分表里没有可解析的记录，无法训练",
            missing="评分表记录", reason="empty")
    return {
        "user": np.asarray(users, dtype=np.int64),
        "movie": np.asarray(movies, dtype=np.int64),
        "rating": np.asarray(ratings, dtype=np.float32),
        "ts": np.asarray(stamps, dtype=np.int64),
        "bad_lines": bad,
    }


# ---------------------------------------------------------------------------
# 时间边界
# ---------------------------------------------------------------------------

def load_time_boundaries(repo_root, data_version=None):
    """读时间边界（T1/T2）与数据版本声明；读不到就用缺省值，并标注来源。

    来源写进模型元数据，是为了让"这份模型用的是哪套边界、从哪儿来的"可追溯 ——
    边界一旦被人改过，历史模型与当前模型就不可直接比较，必须看得出这一点。
    """
    version = data_version or config.DEFAULT_DATA_VERSION
    path = os.path.join(repo_root, config.RULES_RELPATH)
    defaults = {"T1": config.DEFAULT_T1, "T2": config.DEFAULT_T2}
    try:
        with open(path, encoding="utf-8") as fh:
            rules = json.load(fh)
    except (OSError, ValueError):
        return {"T1": defaults["T1"], "T2": defaults["T2"],
                "data_version": version,
                "source": "缺省值（方案文件读不到：%s）" % config.RULES_RELPATH}
    declared = (rules.get("data_version") or {})
    tb = (declared.get("time_boundaries") or {})
    t1 = tb.get("T1") or defaults["T1"]
    t2 = tb.get("T2") or defaults["T2"]
    declared_version = declared.get("id") or version
    return {"T1": t1, "T2": t2,
            "data_version": declared_version,
            "source": config.RULES_RELPATH.replace(os.sep, "/")}


def to_epoch(iso_text):
    """ISO8601（`YYYY-MM-DDTHH:MM:SSZ`）→ Unix 秒；解析不了就给 None（调用方沿用缺省）。"""
    if not iso_text:
        return None
    text = str(iso_text).strip().replace("Z", "+00:00")
    try:
        import datetime
        return int(datetime.datetime.fromisoformat(text).timestamp())
    except (TypeError, ValueError):
        return None
