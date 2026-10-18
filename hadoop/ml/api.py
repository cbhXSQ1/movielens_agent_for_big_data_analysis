# -*- coding: utf-8 -*-
"""ml/api.py —— 机器学习核心的对外入口（服务层只认这个模块里的四个函数与一个异常）。

## 契约（服务层按此调用）

    run_training(task_type, algorithm, params, repo_root, progress=None,
                 data_version=None, task_id=None) -> dict
        任务类型：classification / clustering / dimensionality_reduction
        算法：      naive_bayes / random_forest / kmeans / hierarchical / pca / nmf
        params：    算法超参（对象）。**另有两个"任务参数"也放在这里传**：
                      * `k`            聚类簇数（2～50，不传则按轮廓系数选）
                      * `n_components` 降维目标维度（2～20，不传则按累计解释方差选）
                    为什么放在这里而不单开参数：`run_training` 的签名已由服务层固定，加参数会破坏
                    两端约定；服务层把工具参数 `k` / `n_components` 合并进 `params` 即可。
        progress：  可选回调 progress(stage, **fields)，阶段名见 `config.STAGES`
        data_version：可选，缺省用方案声明/发布区当前版本
        task_id：   可选，模型编号；缺省由本模块生成（形如 `ml-YYYYMMDD-HHMMSS-xxxx`）
        返回：      成功结果字典（字段按数据契约 §3.1）

    predict_rating(model_id, user_id, movie_id, repo_root) -> dict   （§3.6）
    list_models(repo_root) -> dict                                   （§3.7，`{"models": [...]}`）
    get_model(model_id, repo_root) -> dict                            （§3.7，`{"model": {...}}`）

## 特征来源（端侧配置，不是工具参数）

`ML_FEATURE_SOURCE` = `local`（默认，本机算特征）或 `cluster`（先由集群作业聚合再训练）。
默认本机：当前数据量下分布式无收益、也不依赖集群；`cluster` 一路只留扩展点（见
`cluster_aggregation.py`），未接入时会**明确报错**而不是静默退化成本机计算。
"""

import datetime
import os
import secrets

from . import config
from . import cluster_aggregation
from . import classification
from . import clustering
from . import datasets
from . import features
from . import persistence
from . import prediction
from . import reduction
from .errors import TrainingError

TASK_CLASSIFICATION = "classification"
TASK_CLUSTERING = "clustering"
TASK_REDUCTION = "dimensionality_reduction"
TASK_TYPES = (TASK_CLASSIFICATION, TASK_CLUSTERING, TASK_REDUCTION)

#: 任务类型 → 允许的算法。
ALGORITHMS = {
    TASK_CLASSIFICATION: ("naive_bayes", "random_forest"),
    TASK_CLUSTERING: ("kmeans", "hierarchical"),
    TASK_REDUCTION: ("pca", "nmf"),
}

#: 模型与产物的相对根目录（相对仓库根）。
ML_ROOT_REL = config.ML_ROOT_REL

#: 状态中文名（与服务层一致；核心层也带上，便于脱离服务层直接使用）。
STATUS_LABELS = {"queued": "排队", "running": "运行", "succeeded": "成功",
                 "failed": "失败", "aborted": "已中止"}


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def ml_root(repo_root):
    return os.path.join(repo_root, config.ML_ROOT_REL)


def model_dir(model_id, repo_root):
    return os.path.join(ml_root(repo_root), model_id)


def model_exists(model_id, repo_root):
    return os.path.isfile(os.path.join(model_dir(model_id, repo_root), config.META_NAME))


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_model_id():
    """模型编号：`ml-YYYYMMDD-HHMMSS-xxxx`（数据契约 §0：单层、可安全拼进路径）。"""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return "ml-%s-%s" % (stamp, secrets.token_hex(2))


def _stage(progress, name, **fields):
    if progress:
        progress(name, **fields)


def _validate_task(task_type, algorithm):
    if task_type not in TASK_TYPES:
        raise TrainingError("USAGE", "不支持的任务类型：%r" % (task_type,),
                            param="task_type", values=list(TASK_TYPES))
    allowed = ALGORITHMS[task_type]
    if algorithm not in allowed:
        raise TrainingError(
            "USAGE", "%s 不支持的算法：%r（可用：%s）" % (task_type, algorithm, "、".join(allowed)),
            param="algorithm", values=list(allowed))


def _pop_task_keys(params):
    """取出 `k` / `n_components`（服务层把工具参数合并进 params，见模块说明）。"""
    params = dict(params or {})
    return params.pop("k", None), params.pop("n_components", None), params


def _check_k(k, n_samples):
    if k is None:
        return None
    if isinstance(k, bool) or not isinstance(k, int):
        raise TrainingError("USAGE", "簇数 k 需要整数，收到 %r" % (k,), param="k")
    if k < config.K_MIN or k > config.K_MAX:
        raise TrainingError(
            "USAGE", "簇数 k 必须在 %s～%s 之间，收到 %s" % (config.K_MIN, config.K_MAX, k),
            param="k")
    if k >= n_samples:
        raise TrainingError(
            "USAGE", "簇数 k（%s）必须小于参与聚类的用户数（%s）" % (k, n_samples), param="k")
    return k


def _load_data(repo_root, data_version, progress):
    """取数 + 解析 + 时间边界，返回训练所需的全部原料。"""
    tb = datasets.load_time_boundaries(repo_root, data_version)
    version = data_version or tb["data_version"] or config.DEFAULT_DATA_VERSION
    t1 = datasets.to_epoch(tb["T1"])
    t2 = datasets.to_epoch(tb["T2"])
    if t1 is None or t2 is None:
        raise TrainingError("DEPENDENCY_MISSING", "时间边界无法解析（T1=%r，T2=%r）"
                            % (tb["T1"], tb["T2"]), missing="时间边界")

    bundle = datasets.load_published(repo_root, version, progress=progress)
    data_dir = bundle["dir"]
    users_table, bad_users, partial_users = datasets.parse_users(
        os.path.join(data_dir, datasets.TABLE_FILES["users"]))
    movies_table, bad_movies = datasets.parse_movies(
        os.path.join(data_dir, datasets.TABLE_FILES["movies"]))
    ratings = datasets.parse_ratings(os.path.join(data_dir, datasets.TABLE_FILES["ratings"]))
    return {
        "version": version,
        "time_boundaries": {"T1": tb["T1"], "T2": tb["T2"]},
        "time_boundaries_source": tb["source"],
        "t1_epoch": t1,
        "t2_epoch": t2,
        "users": users_table,
        "movies": movies_table,
        "ratings": ratings,
        "data_quality": {
            "users": {"rows": len(users_table), "bad_lines": bad_users,
                      "missing_attributes": partial_users},
            "movies": {"rows": len(movies_table), "bad_lines": bad_movies},
            "ratings": {"rows": int(ratings["user"].size),
                        "bad_lines": int(ratings["bad_lines"])},
            "cache_reused": bool(bundle["reused"]),
        },
    }


def assert_bundle_within_limit(bundle_path, model_id=None):
    """模型包体积护栏：超限就删掉文件并失败，绝不留一个巨型包。

    为什么单独成函数：它要在两处复用（写完之后立刻判、以及测试里直接判），
    更重要的是这条规则值得被单独测住 —— 它挡的是"一次训练把磁盘和预测一起拖垮"
    这种后果很重的失控（实测发生过：接近五Ｇ的模型包）。
    """
    try:
        size_mb = os.path.getsize(bundle_path) / (1024.0 * 1024.0)
    except OSError:
        return
    if size_mb > config.MAX_BUNDLE_MB:
        try:
            os.remove(bundle_path)
        except OSError:
            pass
        raise TrainingError(
            "TASK_FAILED",
            "模型包体积 %.1f 兆，超过上限 %s 兆：请调小 max_depth / min_samples_leaf "
            "或减少 n_estimators 后重试（体积主要来自树的数量与深度）"
            % (size_mb, config.MAX_BUNDLE_MB),
            reason="bundle_too_large", size_mb=round(size_mb, 1),
            limit_mb=config.MAX_BUNDLE_MB, model_id=model_id)


def _finish(model_id, repo_root, payload, artifact_entries, progress, created_at, started_iso):
    """落盘 + 生成产物清单 + 登记索引 + 组装返回（三类任务共用）。"""
    model_path = model_dir(model_id, repo_root)
    persistence.ensure_dir(model_path)
    _stage(progress, config.STAGE_PERSIST, message="写入模型与产物")
    bundle_path = os.path.join(model_path, config.MODEL_BUNDLE_NAME)
    persistence.write_joblib(bundle_path, payload["bundle"])
    # 模型包体积护栏（踩过坑写清）：模型包是"预测时整份加载"的东西，一旦失控会同时
    # 拖垮预测与磁盘 —— 实测有一次随机森林因为没限深度，把包写到接近五Ｇ，预测加载
    # 直接超时、磁盘也被吃掉。超限就**当场失败并给出怎么调小**，而不是留个巨型文件。
    assert_bundle_within_limit(bundle_path, model_id)
    for relpath, content in payload["files"].items():
        target = os.path.join(model_path, relpath)
        persistence.ensure_dir(os.path.dirname(target) or model_path)
        if relpath.endswith(".json"):
            persistence.write_json(target, content)
        elif relpath.endswith(".csv"):
            persistence.write_csv(target, content["header"], content["rows"])
        else:
            raise TrainingError("TASK_FAILED", "不支持的产物类型：%s" % relpath,
                                reason="artifact_kind")

    # 产物清单里**不放元文件自己**：它的字节数取决于清单内容，放进去会自相矛盾
    # （要么循环依赖、要么写一个偏差几字节的假数字）。元文件这一条在读取时现算补上。
    listed = [(relpath, role) for relpath, role in artifact_entries
              if relpath != config.META_NAME]
    artifacts = persistence.collect_artifacts(model_id, model_path, listed)
    meta = dict(payload["meta"])
    meta["artifacts"] = artifacts
    meta_path = os.path.join(model_path, config.META_NAME)
    persistence.write_json(meta_path, meta)
    # 元文件写完后再按真实字节数补一条，随返回值与"取模型"一起给出
    meta_artifact = persistence.artifact_entry(model_id, model_path, config.META_NAME, "meta")

    _stage(progress, config.STAGE_REGISTER, message="登记模型")
    persistence.update_index(repo_root, {
        "model_id": model_id,
        "task_type": meta["task_type"],
        "algorithm": meta["algorithm"],
        "data_version": meta["data_version"],
        "created_at": meta["created_at"],
        "metrics_summary": payload["metrics_summary"],
        "version_fingerprint": meta.get("version_fingerprint", ""),
    })

    return {
        "model_id": model_id,
        "status": "succeeded",
        "status_label": STATUS_LABELS["succeeded"],
        "status_cn": STATUS_LABELS["succeeded"],
        "task_type": meta["task_type"],
        "algorithm": meta["algorithm"],
        "data_version": meta["data_version"],
        "time_boundaries": meta["time_boundaries"],
        "params": meta["params"],
        "metrics": meta["metrics"],
        "artifacts": artifacts + [meta_artifact],
        "created_at": created_at,
        "started_at": started_iso,
        "finished_at": _now_iso(),
        "version_fingerprint": meta.get("version_fingerprint", ""),
        "data_quality": meta.get("data_quality", {}),
    }


# ---------------------------------------------------------------------------
# 分类
# ---------------------------------------------------------------------------

def _run_classification(task_type, algorithm, params, repo_root, progress, data, model_id,
                        created_at, started_iso):
    _stage(progress, config.STAGE_BUILD_FEATURES, message="构造分类特征（按时间前缀聚合）")
    built = features.build_classifier_matrix(data["ratings"], data["users"], data["movies"],
                                             data["t1_epoch"])
    built["t1_epoch"] = data["t1_epoch"]
    built["t2_epoch"] = data["t2_epoch"]

    model, encoding, eff, _importances = classification.train(algorithm, built, params, progress)
    metrics = classification.evaluate(model, built, eff, progress)

    fingerprint = persistence.version_fingerprint(data["version"], algorithm, eff)
    bundle = {
        "model": model,
        "task_type": task_type,
        "algorithm": algorithm,
        "encoders": built["encoders"],
        "params": eff,
        "encoding": encoding,
        "snapshot": prediction.build_snapshot(data["ratings"], data["users"], data["movies"]),
        "version_fingerprint": fingerprint,
    }
    meta = {
        "model_id": model_id,
        "task_type": task_type,
        "algorithm": algorithm,
        "data_version": data["version"],
        "created_at": created_at,
        "params": eff,
        "metrics": metrics,
        "time_boundaries": data["time_boundaries"],
        "time_boundaries_source": data["time_boundaries_source"],
        "feature_spec": {
            "features": built["feature_names"],
            "semantic_features": list(features.SEMANTIC_FEATURES),
            "encoders": ["独热（性别/年龄/职业）", "多热（电影类型）",
                         "数值分箱（朴素贝叶斯用）" if algorithm == "naive_bayes"
                         else "连续值（树模型用）"],
            "encoding": encoding,
            "leakage": metrics.get("leakage"),
        },
        "data_quality": data["data_quality"],
        "evaluated_on": metrics.get("evaluated_on"),
        "version_fingerprint": fingerprint,
    }
    payload = {
        "bundle": bundle,
        "files": {},
        "meta": meta,
        "metrics_summary": {"f1": metrics.get("f1"), "auc": metrics.get("auc")},
    }
    return _finish(model_id, repo_root, payload,
                   [(config.MODEL_BUNDLE_NAME, "model"), (config.META_NAME, "meta")],
                   progress, created_at, started_iso)


# ---------------------------------------------------------------------------
# 聚类
# ---------------------------------------------------------------------------

def _run_clustering(task_type, algorithm, params, repo_root, progress, data, model_id,
                    created_at, started_iso, k=None):
    _stage(progress, config.STAGE_BUILD_FEATURES, message="构造用户偏好画像（仅用 ≤T1 的评分）")
    prof = features.build_user_profile(data["ratings"], data["movies"], data["t1_epoch"])
    X_std, std_params = features.standardize(prof["matrix"])

    k_selection = None
    k = _check_k(k, X_std.shape[0])
    if k is None:
        k, k_selection = clustering.select_k(X_std)

    model, labels, eff = clustering.train(algorithm, X_std, k, params, progress)
    metrics = clustering.metrics(X_std, labels, k, prof["raw_behaviors"],
                                 prof["global_genre_share"], k_selection=k_selection)
    _stage(progress, config.STAGE_PROFILE, message="生成群体画像与标签",
           clusters=len(metrics["clusters"]))

    fingerprint = persistence.version_fingerprint(data["version"], algorithm, dict(eff, k=k))
    bundle = {
        "model": model,
        "task_type": task_type,
        "algorithm": algorithm,
        "k": int(k),
        "std_params": std_params,
        "user_ids": prof["user_ids"],
        "params": dict(eff, k=int(k)),
        "version_fingerprint": fingerprint,
    }
    profile = {
        "model_id": model_id,
        "k": int(k),
        "silhouette": metrics["silhouette"],
        "davies_bouldin": metrics["davies_bouldin"],
        "clusters": metrics["clusters"],
    }
    labels_rows = [[int(uid), int(lab)]
                   for uid, lab in zip(prof["user_ids"].tolist(), labels.tolist())]
    meta = {
        "model_id": model_id,
        "task_type": task_type,
        "algorithm": algorithm,
        "data_version": data["version"],
        "created_at": created_at,
        "params": dict(eff, k=int(k)),
        "metrics": metrics,
        "time_boundaries": data["time_boundaries"],
        "time_boundaries_source": data["time_boundaries_source"],
        "feature_spec": {
            "features": [("genre_%s" % g) for g in config.GENRES]
                        + ["activity", "avg_rating", "rating_std", "recency"],
            "rows": int(X_std.shape[0]),
            "standardization": "前 18 列占比保持原样、后 4 列行为统计做 z 分数",
            "std_params": std_params,
            "leakage": profile_leakage_statement(),
        },
        "data_quality": data["data_quality"],
        "train_window": prof["train_window"],
        "version_fingerprint": fingerprint,
    }
    payload = {
        "bundle": bundle,
        "files": {
            config.PROFILE_NAME: profile,
            config.LABELS_NAME: {"header": ["user_id", "cluster_id"], "rows": labels_rows},
        },
        "meta": meta,
        "metrics_summary": {"k": int(k), "silhouette": metrics["silhouette"]},
    }
    return _finish(model_id, repo_root, payload,
                   [(config.MODEL_BUNDLE_NAME, "model"), (config.PROFILE_NAME, "profile"),
                    (config.LABELS_NAME, "labels"), (config.META_NAME, "meta")],
                   progress, created_at, started_iso)


# ---------------------------------------------------------------------------
# 降维
# ---------------------------------------------------------------------------

def _run_reduction(task_type, algorithm, params, repo_root, progress, data, model_id,
                   created_at, started_iso, n_components=None):
    _stage(progress, config.STAGE_BUILD_FEATURES, message="构造用户偏好画像（仅用 ≤T1 的评分）")
    prof = features.build_user_profile(data["ratings"], data["movies"], data["t1_epoch"])
    X_std, std_params = features.standardize(prof["matrix"])

    components, selection = reduction.choose_components(X_std, n_components)
    model, reduced, detail = reduction.train(algorithm, X_std, components, params, progress)

    # 对比实验要一个 k：沿用"按轮廓系数选簇数"的同一套做法，与聚类任务同一口径
    k, k_selection = clustering.select_k(X_std)
    comparison, _before, _after = reduction.compare(X_std, reduced, k, params=params,
                                                    progress=progress)

    metrics = {
        "n_components": int(components),
        "n_components_selection": selection,
        "explained_variance_ratio": detail["explained_variance_ratio"],
        "reconstruction_error": detail["reconstruction_error"],
        "reconstruction_error_note": detail.get("reconstruction_error_note"),
        "resource": detail["resource"],
        "resource_note": detail.get("resource_note"),
        "comparison": comparison,
        "comparison_k_selection": k_selection,
    }
    fingerprint = persistence.version_fingerprint(
        data["version"], algorithm, dict(params, n_components=int(components)))
    bundle = {
        "model": model,
        "task_type": task_type,
        "algorithm": algorithm,
        "n_components": int(components),
        "std_params": std_params,
        "user_ids": prof["user_ids"],
        "params": dict(params, n_components=int(components)),
        "version_fingerprint": fingerprint,
    }
    coords_rows = [[int(uid)] + [round(float(v), 6) for v in row]
                   for uid, row in zip(prof["user_ids"].tolist(), reduced.tolist())]
    coords_header = ["user_id"] + ["component_%d" % (i + 1) for i in range(int(components))]
    meta = {
        "model_id": model_id,
        "task_type": task_type,
        "algorithm": algorithm,
        "data_version": data["version"],
        "created_at": created_at,
        "params": dict(params, n_components=int(components)),
        "metrics": metrics,
        "time_boundaries": data["time_boundaries"],
        "time_boundaries_source": data["time_boundaries_source"],
        "feature_spec": {
            "features": [("genre_%s" % g) for g in config.GENRES]
                        + ["activity", "avg_rating", "rating_std", "recency"],
            "rows": int(X_std.shape[0]),
            "standardization": "前 18 列占比保持原样、后 4 列行为统计做 z 分数",
            "std_params": std_params,
            "non_negative_shift": detail.get("shift"),
            "leakage": profile_leakage_statement(),
        },
        "data_quality": data["data_quality"],
        "train_window": prof["train_window"],
        "version_fingerprint": fingerprint,
    }
    payload = {
        "bundle": bundle,
        "files": {config.COORDS_NAME: {"header": coords_header, "rows": coords_rows}},
        "meta": meta,
        "metrics_summary": {"n_components": int(components),
                            "reconstruction_error": detail["reconstruction_error"]},
    }
    return _finish(model_id, repo_root, payload,
                   [(config.MODEL_BUNDLE_NAME, "model"), (config.COORDS_NAME, "coords"),
                    (config.META_NAME, "meta")],
                   progress, created_at, started_iso)


def profile_leakage_statement():
    """聚类/降维的防泄漏说明（只用 ≤T1 的数据构造画像，天然不涉及时点泄漏）。"""
    return {
        "policy": "用户画像只用训练期（≤T1）的评分构造；验证期与测试期的评分不参与画像与聚类",
        "verified_on": "画像构造前按时间边界过滤（ts≤T1），训练窗规模见 train_window",
        "split": {"profile_window": "≤T1"},
    }


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------

def run_training(task_type, algorithm, params, repo_root, progress=None, data_version=None,
                 task_id=None):
    """训练一次并落盘登记；返回结果字典（字段按数据契约 §3.1）。"""
    _validate_task(task_type, algorithm)
    if config.feature_source() != config.FEATURE_SOURCE_LOCAL:
        # 集群聚合：只留扩展点；未接入时明确报错，绝不静默退化成本机计算
        cluster_aggregation.run(repo_root, data_version, progress=progress, task_id=task_id)

    k, n_components, algo_params = _pop_task_keys(params)
    if task_type == TASK_CLASSIFICATION and (k is not None or n_components is not None):
        raise TrainingError(
            "USAGE", "分类任务不接受 k 或 n_components（那是聚类/降维的参数）", param="params")

    model_id = task_id or _new_model_id()
    created_at = _now_iso()
    started_iso = created_at

    data = _load_data(repo_root, data_version, progress)
    persistence.drop_tmp_files(model_dir(model_id, repo_root))

    if task_type == TASK_CLASSIFICATION:
        return _run_classification(task_type, algorithm, algo_params, repo_root, progress, data,
                                   model_id, created_at, started_iso)
    if task_type == TASK_CLUSTERING:
        return _run_clustering(task_type, algorithm, algo_params, repo_root, progress, data,
                               model_id, created_at, started_iso, k=k)
    return _run_reduction(task_type, algorithm, algo_params, repo_root, progress, data,
                          model_id, created_at, started_iso, n_components=n_components)


def effective_params(task_type, algorithm, params, k=None, n_components=None):
    """受理时给对端一个"预计生效参数"（与训练时的生效值同一套规则）。

    为什么在受理阶段就回：用户传的是空超参，若只回 `{}`，界面上看不出"实际会用
    多少棵树、多深、叶子最少几条"，等训练完才知道。这里用与训练完全相同的默认值
    与校验规则算一遍，保证"受理时的预计"与"结果里的生效值"一致。
    """
    params = dict(params or {})
    if k is not None:
        params["k"] = k
    if n_components is not None:
        params["n_components"] = n_components
    if task_type == TASK_CLASSIFICATION:
        from . import classification
        return dict(classification.effective_params(algorithm, params))
    if task_type == TASK_CLUSTERING:
        from . import clustering
        return dict(clustering.effective_params(algorithm, params))
    from . import reduction
    return dict(reduction.effective_params(algorithm, params))


def predict_rating(model_id, user_id, movie_id, repo_root):
    """单条预测（§3.6）。"""
    return prediction.predict(model_id, user_id, movie_id, repo_root)


def list_models(repo_root):
    """列已登记模型（§3.7）。"""
    entries = persistence.read_index(repo_root)
    models = []
    for item in entries:
        models.append({
            "model_id": item.get("model_id", ""),
            "task_type": item.get("task_type", ""),
            "algorithm": item.get("algorithm", ""),
            "data_version": item.get("data_version", ""),
            "created_at": item.get("created_at", ""),
            "metrics_summary": item.get("metrics_summary") or {},
            "version_fingerprint": item.get("version_fingerprint", ""),
        })
    return {"models": models}


def get_model(model_id, repo_root):
    """取模型元数据与指标（§3.7）。"""
    path = os.path.join(model_dir(model_id, repo_root), config.META_NAME)
    if not os.path.isfile(path):
        raise TrainingError(
            "MODEL_NOT_FOUND",
            "模型 %s 不存在（可能编号不对，或模型还没训练成功）" % model_id,
            model_id=model_id)
    model = persistence.read_json(path)
    # 元文件自身也作为一条产物给出（字节数按当前文件现算，见 _finish 里的说明）
    listing = [item for item in model.get("artifacts") or []
               if item.get("path") != config.META_NAME]
    model["artifacts"] = listing + [persistence.artifact_entry(
        model_id, model_dir(model_id, repo_root), config.META_NAME, "meta")]
    return {"model": model}
