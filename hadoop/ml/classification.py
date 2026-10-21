# -*- coding: utf-8 -*-
"""ml/classification.py —— 二分类：朴素贝叶斯 与 随机森林。

两类算法的编码方式不同，但**用的是同一批语义特征**（性别/年龄/职业/类型 + 四个历史统计）：

  * 随机森林：直接吃连续值与独热（树本来就能处理混合尺度）；
  * 朴素贝叶斯：需要离散特征，所以数值按**训练期分位数**分箱、其余按独热类别处理。

这不是"两套输入"，而是同一批特征按各自算法的要求编码 —— 元数据里会如实记录编码方式，
评估口径（同时间划分、同阈值、同指标）保持一致，结论才可比。

指标全部来自 sklearn 的真实计算，不做任何修饰；数值按数据契约保留四位小数。
"""

import numpy as np

from . import config
from .errors import TrainingError
from . import features


# ---------------------------------------------------------------------------
# 超参校验（白名单 + 上下界）
# ---------------------------------------------------------------------------

def _spec_for(algorithm):
    return config.HYPERPARAMS[("classification", algorithm)]


def effective_params(algorithm, params):
    """按白名单校验超参并回显生效值；未知键拒绝并列出可用键。"""
    specs = {spec["key"]: spec for spec in _spec_for(algorithm)}
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
        if value is None and spec.get("nullable"):
            out[key] = None
            continue
        if spec["type"] == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise TrainingError("USAGE", "%s 需要整数，收到 %r" % (key, value), param=key)
            if value < spec["min"] or value > spec["max"]:
                raise TrainingError(
                    "USAGE", "%s 必须在 %s～%s 之间，收到 %s"
                    % (key, spec["min"], spec["max"], value), param=key)
        elif spec["type"] == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TrainingError("USAGE", "%s 需要数值，收到 %r" % (key, value), param=key)
            value = float(value)
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


# ---------------------------------------------------------------------------
# 训练
# ---------------------------------------------------------------------------

def train(algorithm, built, params, progress=None):
    """在训练集上拟合，返回 `(模型, 编码信息, 生效超参)`。

    `built` 来自 `features.build_classifier_matrix`；训练只用训练期（≤T1）的样本。
    """
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.naive_bayes import CategoricalNB

    t1_epoch = built["t1_epoch"]
    train_mask = built["ts"] <= t1_epoch
    y_train = built["y"][train_mask]
    if y_train.size == 0:
        raise TrainingError("DEPENDENCY_MISSING", "训练集为空（≤T1 没有评分），无法训练",
                            missing="训练期评分", reason="empty_train_window")
    if len(np.unique(y_train)) < 2:
        raise TrainingError(
            "TASK_FAILED",
            "训练集里只有一个类别，无法训练二分类模型（请扩充训练期数据或调整正类判定）",
            reason="single_class")

    eff = effective_params(algorithm, params)
    if progress:
        progress(config.STAGE_TRAIN, message="训练 %s" % algorithm)

    if algorithm == "naive_bayes":
        # 只对训练行做离散化：一百万行整体转成整数矩阵没必要，索引完再转更省内存
        X_cat = features.to_categorical(built["X"][train_mask], built["encoders"])
        # 必须给出每个特征的类别总数：否则验证/测试期出现训练期没见过的分箱编号时，
        # 预测会因为"类别数不够"直接报错（实测踩到过）。这也是让特征编码可复现的一部分。
        min_categories = _categories_per_feature(built["encoders"])
        model = CategoricalNB(alpha=eff["alpha"], min_categories=min_categories)
        model.fit(X_cat, y_train)
        importances = _nb_importance(model, X_cat, built["feature_names"])
        enc = {"encoding": "categorical（数值按训练期分位数分箱）", "bins": config.NUMERIC_BINS}
        return model, enc, eff, importances

    if algorithm == "random_forest":
        X = built["X"]
        model = RandomForestClassifier(
            n_estimators=eff["n_estimators"],
            max_depth=eff["max_depth"],
            min_samples_leaf=eff["min_samples_leaf"],
            n_jobs=1,                    # 本机 joblib 并行会被拒、退化成串行；显式写 1 并回显
            random_state=42,
            class_weight=None,
        )
        model.fit(X[train_mask], y_train)
        importances = [
            {"feature": name, "importance": round(float(value), 6)}
            for name, value in zip(built["feature_names"], model.feature_importances_)
        ]
        enc = {"encoding": "连续值 + 独热（树模型直接使用）", "n_jobs": 1}
        return model, enc, eff, importances

    raise TrainingError("USAGE", "分类不支持的算法：%r" % algorithm, param="algorithm",
                        values=list(config.HYPERPARAMS[("classification", "naive_bayes")]))


def _categories_per_feature(encoders):
    """每个特征的类别总数：独热列 2 个（0/1），分箱列为"边界数 + 1"。"""
    start = encoders["numeric_start"]
    categories = [2] * start
    categories += [int(len(edges) + 1) for edges in encoders["numeric_edges"]]
    return np.asarray(categories, dtype=np.int64)


def _nb_importance(model, X_cat, names):
    """朴素贝叶斯的"特征贡献"：各类别上对数概率差的最大绝对值。

    朴素贝叶斯没有树那样的分裂重要性，硬套一个会误导人。这里给的是可解释的量：
    对每个特征，看它在两个类别下的条件概率差得多不多 —— 差得越大，这个特征对区分两类越有用。
    名称沿用 `feature_importance`，并在 `feature_importance_note` 里说清含义。
    """
    out = []
    logs = getattr(model, "feature_log_prob_", None)
    if logs is None or not hasattr(model, "category_count_"):
        return out
    try:
        # CategoricalNB：每个特征一张 (类别 × 类别取值) 的对数概率表
        for j, name in enumerate(names):
            if j >= len(logs):
                break
            table = logs[j]                     # (n_classes, n_categories)
            if table.shape[0] < 2:
                break
            diff = np.abs(table[1] - table[0])
            if diff.size == 0:
                continue
            out.append({"feature": name, "importance": round(float(np.max(diff)), 6)})
    except (AttributeError, IndexError, TypeError):
        return []
    return out


# ---------------------------------------------------------------------------
# 评估
# ---------------------------------------------------------------------------

def evaluate(model, built, params, progress=None):
    """在测试集（>T2）上评估；顺带给出阈值扫描，便于看误报/漏报的取舍。

    为什么用测试集而不是验证集：验证集用来决定"用哪个阈值/超参"，测试集才是最后的交代。
    两个集合的样本量都写进 `split_sizes`，让人一眼看得出这份结论建立在多少条记录上。
    """
    from sklearn.metrics import (average_precision_score, confusion_matrix, f1_score,
                                 precision_score, recall_score, roc_auc_score)

    threshold = float(params.get("threshold", 0.5))
    t1_epoch = built["t1_epoch"]
    train_mask, valid_mask, test_mask = features.split_masks(
        built["ts"], t1_epoch, built["t2_epoch"])

    eval_mask = test_mask if test_mask.any() else valid_mask
    evaluated_on = "test" if test_mask.any() else "valid"
    if not eval_mask.any():
        raise TrainingError(
            "TASK_FAILED",
            "验证集与测试集都是空的（所有记录都在 T1 之前），无法给出评估结论",
            reason="empty_holdout")

    if progress:
        progress(config.STAGE_EVALUATE, message="评估（%s 集）" % evaluated_on)

    y_true = built["y"][eval_mask]
    matrix = built["X"][eval_mask]
    if hasattr(model, "feature_log_prob_"):        # 朴素贝叶斯：需要离散矩阵
        matrix = features.to_categorical(matrix, built["encoders"])
    prob = model.predict_proba(matrix)[:, 1]
    y_pred = (prob >= threshold).astype(np.int8)

    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    sweep = _threshold_sweep(y_true, prob)
    metrics = {
        "f1": _r4(f1_score(y_true, y_pred, zero_division=0)),
        "auc": _r4(roc_auc_score(y_true, prob)),
        "precision": _r4(precision_score(y_true, y_pred, zero_division=0)),
        "recall": _r4(recall_score(y_true, y_pred, zero_division=0)),
        "pr_auc": _r4(average_precision_score(y_true, prob)),
        "threshold": round(threshold, 4),
        "confusion_matrix": {"tp": int(tp), "fp": int(fp), "tn": int(tn), "fn": int(fn)},
        "positive_rate": _r4(float(y_true.mean())),
        "split_sizes": {"train": int(train_mask.sum()), "valid": int(valid_mask.sum()),
                        "test": int(test_mask.sum())},
        "evaluated_on": evaluated_on,
        "threshold_sweep": sweep,
        "feature_importance_note": (
            "随机森林给出分裂重要性；朴素贝叶斯给出各特征在两类之间条件概率的最大对数差（越大越有区分度）"
            if getattr(model, "feature_importances_", None) is not None else
            "朴素贝叶斯给出各特征在两类之间条件概率的最大对数差（越大越有区分度）；树模型给分裂重要性"),
    }
    if getattr(model, "feature_importances_", None) is not None:
        metrics["feature_importance"] = [
            {"feature": name, "importance": round(float(value), 6)}
            for name, value in zip(built["feature_names"], model.feature_importances_)
        ]
    else:
        metrics["feature_importance"] = _nb_importance(model, None, built["feature_names"])
    metrics["leakage"] = leakage_statement(built)
    return metrics


def leakage_statement(built):
    """防泄漏的做法与核验方式（写进指标与元数据，供评审核对）。"""
    return {
        "policy": ("按时间排序的前缀聚合：每条样本的历史统计特征只由它自己时间戳之前的记录聚合；"
                   "训练只用 ≤T1 的样本，验证 (T1,T2]、测试 >T2"),
        "verified_on": ("单元测试注入未来数据后历史样本特征不变；"
                        "训练/验证/测试的时间边界见 split_sizes 与 time_boundaries"),
        "split": {"train": "≤T1", "valid": "(T1, T2]", "test": ">T2"},
    }


def _threshold_sweep(y_true, prob):
    """阈值扫描：看"卡严一点"与"放松一点"对查准/查全的影响（对正类不平衡的场景很实用）。"""
    from sklearn.metrics import f1_score, precision_score, recall_score

    out = []
    for t in (0.3, 0.4, 0.5, 0.6, 0.7):
        pred = (prob >= t).astype(np.int8)
        out.append({
            "threshold": t,
            "precision": _r4(precision_score(y_true, pred, zero_division=0)),
            "recall": _r4(recall_score(y_true, pred, zero_division=0)),
            "f1": _r4(f1_score(y_true, pred, zero_division=0)),
        })
    return out


def predict_proba(model, X):
    """统一的概率接口：分别照顾朴素贝叶斯（需要离散化）与树模型（连续值）。"""
    if hasattr(model, "feature_log_prob_"):
        # 调用方传进来的应是离散矩阵（由 evaluate/train 侧统一处理），这里做防御性判断
        if X.dtype not in (np.int8, np.int16, np.int32, np.int64, np.uint8):
            raise TrainingError("TASK_FAILED", "朴素贝叶斯需要离散特征矩阵，收到浮点矩阵",
                                reason="encoding_mismatch")
        return model.predict_proba(X)[:, 1]
    return model.predict_proba(X)[:, 1]


def _r4(value):
    """数值统一保留四位小数（数据契约 §0）。"""
    return round(float(value), 4)


# 兼容性说明：`evaluate` 与 `train` 需要知道时间边界，故 `built` 里带上 T1/T2（由 api 填充）。
