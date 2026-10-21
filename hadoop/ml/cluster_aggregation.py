# -*- coding: utf-8 -*-
"""ml/cluster_aggregation.py —— 「作业聚合」扩展点（默认不启用）。

## 这是什么

特征来源有两种：`local`（默认，本机按时间前缀聚合）与 `cluster`（先由**一节集群作业**在集群上
算出"每个用户、每部电影在某个时点之前的历史统计 + 必要的抽样聚合"，产出一张紧凑画像表，
本机训练读这张表）。开关是**纯端侧配置**（`ML_FEATURE_SOURCE`，默认 `local`），不作为工具参数
对外，对端零改动。

## 为什么留扩展点而不是现在就做

当前数据量（评分表二十四兆、一百万行）下，分布式调度的开销远大于计算本身；
而真正随数据量增长的是**聚合**而不是模型 —— 所以需要时把聚合挪到集群上，
单机训练看到的仍是小表，凝聚层次与矩阵分解这类没有分布式实现的算法也能保留。

## 接口（给接作业的人）

    run(repo_root, data_version, progress=None, task_id=None) -> dict
        {
          "profile_matrix": float32 数组 (n_users, 22),   # 18 类型占比 + 4 行为统计（未标准化）
          "user_ids": int 数组,
          "raw_behaviors": {...},                         # 与 features.build_user_profile 同构
          "global_genre_share": float32 数组 (18,),
          "train_window": {"ratings": int, "users": int, "until": int},
          "source": "cluster",                            # 便于元数据标注
        }

    分类那一侧同理：若将来把"点对点历史特征"也交给作业算，可再加 `run_rating_features(...)`，
    返回与 `features.build_classifier_matrix` 同构的矩阵。

## 现在调用会怎样

明确拒绝，并把"需要接什么"写进错误说明 —— **绝不静默退化成本机计算**，
否则配置写错了却看不出来，正是最难查的那类问题。
"""

from .errors import TrainingError


def enabled(feature_source):
    """该特征来源是否在本机可用（`cluster` 目前未接作业提交）。"""
    return feature_source == "local"


def run(repo_root, data_version, progress=None, task_id=None):
    """作业聚合的入口（尚未实现）。"""
    raise TrainingError(
        "TASK_FAILED",
        "特征来源配置为集群聚合，但本机尚未接入作业提交（扩展点：hadoop/ml/cluster_aggregation.py）。"
        "接法：提交一节作业算「每用户/每电影在某时点前的历史统计 + 抽样聚合」，"
        "按本模块文档里的返回结构交回，训练侧无需改动。当前请把配置改回本机（ML_FEATURE_SOURCE=local）。",
        reason="cluster_aggregation_not_wired",
        hint="扩展点见 hadoop/ml/cluster_aggregation.py")
