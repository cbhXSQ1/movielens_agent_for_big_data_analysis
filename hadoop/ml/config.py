# -*- coding: utf-8 -*-
"""ml/config.py —— 常量、超参白名单与运行期配置（本模块不依赖任何三方包）。

三件事在这里集中定死，避免散落各处：

  * **超参白名单**（键、类型、默认值、上下界）—— 数据契约 §1.3 的落地，未知键一律拒绝；
  * **时间边界**（T1/T2）—— 优先读方案声明，读不到才用缺省值，并把来源写进模型元数据；
  * **特征来源**（本机 / 集群聚合）—— 默认本机；集群聚合只留扩展点（见 cluster_aggregation）。

超参上限偏保守：本机七Ｇ内存、集群常驻占四Ｇ，训练可用余量约三Ｇ，树太多会直接吃光。
"""

import os

# ---------------------------------------------------------------------------
# 数据与目录
# ---------------------------------------------------------------------------

#: 缺省数据版本（发布区当前版本）；调用方可用 `data_version` 覆盖。
DEFAULT_DATA_VERSION = "ml1m-clean-v1"

#: 缺省时间边界（数据契约 §2）；仅当方案声明读不到时使用，并在元数据里如实标注来源。
DEFAULT_T1 = "2001-12-31T23:59:59Z"
DEFAULT_T2 = "2002-06-30T23:59:59Z"

#: 方案文件（时间边界与数据版本声明的权威来源）。
RULES_RELPATH = os.path.join("config", "cleaning_rules.v1.json")

#: 本机缓存目录（相对仓库根）；按数据版本分目录，带来源哈希，命中即复用。
CACHE_ROOT_REL = os.path.join("var", "ml-cache")

#: 模型与产物根目录（相对仓库根）。
ML_ROOT_REL = os.path.join("var", "ml")

#: 模型目录内的固定文件名。
MODEL_BUNDLE_NAME = "model.joblib"
META_NAME = "meta.json"
PROFILE_NAME = "profile.json"
LABELS_NAME = "labels.csv"
COORDS_NAME = "coords.csv"
INDEX_NAME = "index.json"

# ---------------------------------------------------------------------------
# 特征来源（集群聚合扩展点）
# ---------------------------------------------------------------------------

#: 取值：`local`（默认，本机算特征） / `cluster`（先由集群作业聚合，本机训练）。
#: 默认本机：当前数据量下分布式无收益，也不依赖集群；集群聚合属端侧可选开关，默认关闭。
FEATURE_SOURCE_LOCAL = "local"
FEATURE_SOURCE_CLUSTER = "cluster"
FEATURE_SOURCES = (FEATURE_SOURCE_LOCAL, FEATURE_SOURCE_CLUSTER)
DEFAULT_FEATURE_SOURCE = FEATURE_SOURCE_LOCAL

#: 环境变量名（服务层可用它把端侧配置传下来；不对外暴露成工具参数）。
ENV_FEATURE_SOURCE = "ML_FEATURE_SOURCE"


def feature_source(value=None):
    """解析特征来源：显式入参 > 环境变量 > 缺省（本机）。非法取值如实拒绝。"""
    text = value if value not in (None, "") else os.environ.get(ENV_FEATURE_SOURCE, "")
    text = (text or "").strip().lower()
    if not text:
        return DEFAULT_FEATURE_SOURCE
    if text not in FEATURE_SOURCES:
        from .errors import TrainingError

        raise TrainingError(
            "USAGE",
            "特征来源只支持 %s，收到 %r" % (" 或 ".join(FEATURE_SOURCES), value),
            param="feature_source",
            values=list(FEATURE_SOURCES))
    return text


# ---------------------------------------------------------------------------
# 阶段（供服务层写进度）
# ---------------------------------------------------------------------------

STAGE_LOAD_DATA = "load_data"
STAGE_BUILD_FEATURES = "build_features"
STAGE_TRAIN = "train"
STAGE_EVALUATE = "evaluate"
STAGE_PROFILE = "profile"      # 聚类专用：从簇到用户群体画像
STAGE_COMPARE = "compare"      # 降维专用：降维前后聚类对比
STAGE_PERSIST = "persist"
STAGE_REGISTER = "register"

#: 各任务类型的阶段序列（服务层据此算进度百分比）。
STAGES = {
    "classification": (STAGE_LOAD_DATA, STAGE_BUILD_FEATURES, STAGE_TRAIN,
                       STAGE_EVALUATE, STAGE_PERSIST, STAGE_REGISTER),
    "clustering": (STAGE_LOAD_DATA, STAGE_BUILD_FEATURES, STAGE_TRAIN,
                   STAGE_EVALUATE, STAGE_PROFILE, STAGE_PERSIST, STAGE_REGISTER),
    "dimensionality_reduction": (STAGE_LOAD_DATA, STAGE_BUILD_FEATURES, STAGE_TRAIN,
                                 STAGE_EVALUATE, STAGE_COMPARE, STAGE_PERSIST, STAGE_REGISTER),
}

# ---------------------------------------------------------------------------
# 超参白名单（数据契约 §1.3）
# ---------------------------------------------------------------------------

#: 一条超参规格：类型、默认值、下界/上界（数值）、可选值（枚举）、说明。
#: `type` 取值：int / number / string。
N_ESTIMATORS = {
    "key": "n_estimators", "type": "int", "default": 120, "min": 10, "max": 500,
    "description": "随机森林的树数（朴素贝叶斯不使用此项）",
}
MAX_DEPTH = {
    # 默认给 20 而不是"不限制"：不加限制时，九十七万行、叶子最小样本 1 的随机森林
    # 单棵树就有上百万节点，一百二十棵树能把模型包写到接近五Ｇ（实测发生：预测要
    # 加载五Ｇ，直接超时、磁盘也被吃掉）。限制深度是控制模型体积最直接的一项。
    # 校准结论：深度 20 与 30 的验证集 AUC 相差不到 0.001，而深度越大体积增长明显。
    "key": "max_depth", "type": "int", "default": 20, "min": 2, "max": 40,
    "nullable": True, "description": "随机森林的最大深度；缺省 20，显式给 null 表示不限制",
}
MIN_SAMPLES_LEAF = {
    # 校准结论（2026-10-18，全量训练集、验证集比较）：叶子 20 的 AUC 0.7751、包 129M；
    # 叶子 50 的 AUC 0.7743、包 70M —— 只差 0.0008 却省一半体积与时间，取 50。
    "key": "min_samples_leaf", "type": "int", "default": 50, "min": 1, "max": 200,
    "description": "随机森林叶节点的最小样本数；缺省 50（校准所得）",
}
THRESHOLD = {
    "key": "threshold", "type": "number", "default": 0.5, "min": 0.05, "max": 0.95,
    "description": "判定为正类的概率阈值（只影响报告的分类结果与混淆矩阵）",
}
ALPHA = {
    "key": "alpha", "type": "number", "default": 1.0, "min": 0.001, "max": 10.0,
    "description": "朴素贝叶斯的拉普拉斯平滑系数",
}
RANDOM_STATE = {
    "key": "random_state", "type": "int", "default": 42, "min": 0, "max": 2 ** 31 - 1,
    "description": "可复现用的随机种子",
}
MAX_ITER = {
    "key": "max_iter", "type": "int", "default": 300, "min": 50, "max": 2000,
    "description": "迭代上限（聚类）",
}
NMF_INIT = {
    "key": "init", "type": "string", "default": "nndsvda",
    "values": ("nndsvda", "random"), "description": "非负矩阵分解的初始化方式",
}
NMF_MAX_ITER = {
    "key": "max_iter", "type": "int", "default": 400, "min": 50, "max": 2000,
    "description": "非负矩阵分解的迭代上限",
}

#: 任务类型 + 算法 → 可用超参规格（合同 §1.3）。
#: 注意：分类两种算法共用前四项（阈值/树数/深度/叶最小样本），朴素贝叶斯另有 alpha；
#: 随机森林忽略 alpha，朴素贝叶斯忽略树相关三项 —— 这属于"共用键"，不是未知键。
CLASSIFIER_COMMON = (N_ESTIMATORS, MAX_DEPTH, MIN_SAMPLES_LEAF, THRESHOLD)
HYPERPARAMS = {
    ("classification", "naive_bayes"): CLASSIFIER_COMMON + (ALPHA,),
    ("classification", "random_forest"): CLASSIFIER_COMMON,
    ("clustering", "kmeans"): (RANDOM_STATE, MAX_ITER),
    ("clustering", "hierarchical"): (RANDOM_STATE,),
    ("dimensionality_reduction", "pca"): (RANDOM_STATE,),
    ("dimensionality_reduction", "nmf"): (RANDOM_STATE, NMF_INIT, NMF_MAX_ITER),
}

# ---------------------------------------------------------------------------
# 其它可调项
# ---------------------------------------------------------------------------

#: 聚类簇数的候选范围与上限（数据契约：2～50）。
K_MIN, K_MAX = 2, 50
#: 不传 k 时的候选簇数（画像只有六千个用户，扫到十就够判断拐点了）。
K_CANDIDATES = tuple(range(2, 11))
#: 降维目标维度的候选范围（数据契约：2～20）。
COMPONENTS_MIN, COMPONENTS_MAX = 2, 20
#: 不传目标维度时：取解释方差累计达到该比例的最小维度，并夹在上下界内。
PCA_VARIANCE_TARGET = 0.90
#: 数值特征的离散化分箱数（朴素贝叶斯用）。
NUMERIC_BINS = 10
#: 阳性阈值：评分不低于该值算正类。
POSITIVE_RATING = 4
#: 训练单次时长上限（秒）；超时按失败如实报告。
TRAINING_TIMEOUT_SECONDS = 30 * 60
#: 预测单次时长上限（秒）。
PREDICT_TIMEOUT_SECONDS = 30
#: 模型包体积上限（兆）。超限**直接失败**而不是留下一个巨型文件：
#: 模型包是"预测时要整份加载"的东西，体积失控会同时拖垮预测与磁盘（实测踩到过）。
MAX_BUNDLE_MB = 200

#: ML-1M 的 18 个电影类型（电影表的 `Genres` 字段用 `|` 分隔）。
#: 固定成一串，是为了让"偏好画像"的 18 维在任何数据版本下含义一致、可比较。
GENRES = (
    "Action", "Adventure", "Animation", "Children's", "Comedy", "Crime",
    "Documentary", "Drama", "Fantasy", "Film-Noir", "Horror", "Musical",
    "Mystery", "Romance", "Sci-Fi", "Thriller", "War", "Western",
)

#: 画像向量维度：18 个类型占比 + 4 个行为统计（活跃度、均分、评分波动、最近活跃）。
PROFILE_DIM = len(GENRES) + 4
