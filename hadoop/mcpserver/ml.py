# -*- coding: utf-8 -*-
"""mcpserver/ml.py —— 迭代二机器学习能力的**服务侧**（受理、状态、预测、查询）。

分工（与实现方案 §4 一致）：

  * 本模块只做"接活与转达"：把分派层校验好的参数转给机器学习核心（`hadoop/ml`，由
    另一位同事实现），并把结果、进度、失败原因如实落到任务体系里；
  * **训练任务并入现有任务体系**：状态文件与清洗任务同构（`<产物目录>/tasks/<编号>/status.json`），
    因此「查任务状态 / 等任务结束 / 查占用状态 / 列任务」**无需新增入口**即可用；
  * **执行位共用**：训练期间发起清洗会被拒并给出占用者 —— 一次只做一件事，状态与取消
    才能统一（这是与对端约定过的取舍）；
  * **取消沿用既有机制**：工作进程以独立会话启动，中止能力可按进程组终止它
    （进程反查认得机器学习工作进程，见 `tasks._matches_ml_worker`）；
  * **模型编号即任务编号**（`ml-YYYYMMDD-HHMMSS-xxxx`），模型与产物落在
    `<仓库>/var/ml/<编号>/`。

对外字段与类型以 `docs/hadoop/迭代二_机器学习_数据契约.md` 为准；本模块不产生任何业务数字，
指标全部来自机器学习核心的返回。
"""

import datetime
import io
import json
import os
import subprocess
import sys

from . import contract, readers, tasks, tools, writers
from .contract import StructuredError

#: 三类任务（与机器学习核心的取值一致）。
TASK_CLASSIFICATION = "classification"
TASK_CLUSTERING = "clustering"
TASK_REDUCTION = "dimensionality_reduction"
ML_TASK_TYPES = (TASK_CLASSIFICATION, TASK_CLUSTERING, TASK_REDUCTION)

#: 工具名 → 任务类型（三个训练能力各自固定一种任务类型，算法由参数选）。
TOOL_TASK_TYPE = {
    "train_classifier": TASK_CLASSIFICATION,
    "cluster_users": TASK_CLUSTERING,
    "reduce_dimensions": TASK_REDUCTION,
}

#: 训练任务的阶段序列。与清洗任务共用同一套状态文件字段（stage / stage_index /
#: stage_total / progress_percent），只是阶段名不同；`queued` 是起始状态、不计入进度。
ML_STAGES = ("queued", "load_data", "build_features", "train", "evaluate", "persist",
             "register", "done")

#: 模型与产物的相对根目录。**与机器学习核心的 `ml.api.ML_ROOT_REL` 必须一致**，
#: 有一条跨模块断言测试盯着（核心不可用时按跳过处理）。
ML_ROOT_REL = os.path.join("var", "ml")

#: 预测是同步短调用：单次时长上限。超时就如实报"没有返回"，不无限等下去。
PREDICT_TIMEOUT_SECONDS = 20.0

#: 受理返回里那句"预计耗时是估算"的标注（与既有受理能力同一套说法）。
ESTIMATE_NOTE = ("预计耗时取自能力清单里声明的参考值，不是本次实测；"
                 "真实耗时以任务进入终态时记录的时间为准。")


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _now_utc():
    """与执行侧同格式的 UTC 时间戳，便于两边直接对照。"""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _api(ctx=None):
    """按需取机器学习核心（`ml.api`）。

    为什么延迟导入：核心会引入 scikit-learn 那一套依赖，而服务端在**只跑契约与只读
    测试**的环境里（系统解释器、没装机器学习依赖）也必须能被导入。取不到就如实报
    "核心不可用"，而不是让整个服务起不来。
    """
    root = tasks._repo_root(ctx)
    if root not in sys.path:
        sys.path.insert(0, root)
    hadoop_root = os.path.join(root, "hadoop")
    if hadoop_root not in sys.path:
        sys.path.insert(0, hadoop_root)
    try:
        from ml import api as ml_api
    except Exception as exc:                      # noqa: BLE001 —— 缺依赖也算不可用
        raise StructuredError(
            contract.CODE_INTERNAL_ERROR,
            "机器学习核心不可用（%s: %s）：请确认依赖已装在服务所用的运行环境里"
            % (type(exc).__name__, exc))
    return ml_api


def ml_root(ctx=None):
    """模型与产物的根目录（`<仓库>/var/ml`）。"""
    return os.path.join(tasks._repo_root(ctx), ML_ROOT_REL)


def model_directory(ctx, model_id):
    """某个模型的目录；编号形状不合法或逃出模型根一律拒绝。

    编号的形状校验**主闸门在分派层**（`task_id` / `from_task` / `model_id` 统一按
    "单层编号"判），这里再判一次是**防御性**：本函数也会被下载路由与资源读取直接调用，
    不经过分派层。不另立规则（长度、字符集都沿用契约层那一份）。
    """
    if not contract.valid_task_id(model_id):
        raise StructuredError(
            contract.CODE_FORBIDDEN_PATH,
            "模型编号不合法：%r（应为形如 ml-YYYYMMDD-HHMMSS-xxxx 的单层编号）" % (model_id,),
            model_id=model_id)
    base = ml_root(ctx)
    # 复用读取层的路径解析：拒绝上级跳转、绝对路径与指向根外的符号链接
    return readers.resolve_under(base, model_id, must_exist=False)


def model_exists(ctx, model_id):
    """模型是否已登记（以元数据 sidecar 为准，与核心的判定同一份来源）。"""
    try:
        return bool(_api(ctx).model_exists(model_id, tasks._repo_root(ctx)))
    except StructuredError:
        raise
    except Exception:                             # noqa: BLE001
        # 核心不可用时退化为"看文件在不在"，不因此把已训练的模型说成不存在
        return os.path.isfile(os.path.join(model_directory(ctx, model_id), "meta.json"))


def _new_model_id():
    """模型编号（与任务编号同值）：`ml-YYYYMMDD-HHMMSS-xxxx`。"""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return "ml-%s-%s" % (stamp, os.urandom(3).hex())


def _estimated_seconds(tool_name):
    """预计耗时：取自工具表里该能力的声明值（对端从"列工具"看到的就是它）。"""
    tool = tools.get_tool(tool_name) or {}
    return tool.get("estimated_seconds")


def _check_data_version(ctx, data_version):
    """发布区是否有这个数据版本；返回一句可回显的说明。

    判断顺序是刻意的：**"连不上集群"绝不能报成"缺数据"**。两者对使用者的下一步动作
    完全不同（一个去启动集群，一个去核对版本或先跑一次清洗发布）。所以：
      * 读得到发布区、且里面确实没有这个版本 → `DEPENDENCY_MISSING`（当场拦住，不让它
        白建一个必然失败的任务）；
      * 连不上集群 → **照常受理**：核心若能用本机缓存就正常训练，不能用会在任务里
        如实失败（那里的报错能分清"连不上"与"没有这个版本"）。
    """
    if not data_version:
        return None
    try:
        listing = readers.list_published_versions({}, ctx)
    except StructuredError as exc:
        if exc.code == contract.CODE_DRIVER_UNREACHABLE:
            return {"data_check": "unreachable",
                    "note": "暂时读不到发布区（可能集群未启动）；本次照常受理，"
                            "若本机没有该版本的缓存，任务会如实失败并说明原因"}
        raise
    available = [entry.get("data_version") for entry in listing.get("versions") or []]
    if data_version not in available:
        raise StructuredError(
            contract.CODE_DEPENDENCY_MISSING,
            "发布区里没有数据版本 %s：现有版本是 %s。请先完成一次清洗发布，或改用现有版本"
            % (data_version, "、".join(available) or "（一个都没有）"),
            data_version=data_version, available=available)
    return {"data_check": "ok", "available_versions": available}


def _busy_failure(holder, tool_name):
    """执行位被占用时的失败：给出占用者与解除办法（与既有写操作同一套说法）。"""
    return StructuredError(
        contract.CODE_TASK_ALREADY_RUNNING,
        "已有运行中的任务：%s。训练与清洗共用同一个执行位，一次只做一件事："
        "请等它跑到终态（用「查任务状态」或「等任务结束」跟进），或用「取消任务」中止它"
        % holder,
        task_id=holder, occupier=holder,
        remedy="等占用者到终态，或取消它；训练期间不支持并行")


# ---------------------------------------------------------------------------
# 三个训练能力的公共受理
# ---------------------------------------------------------------------------

def _submit_training(args, ctx, tool_name):
    """训练能力的公共受理：检查 → 建任务 → 启动工作进程 → 留凭据 → 立即返回。

    受理阶段**只回**编号、状态、取向与预计耗时；指标与产物清单要等任务成功之后
    由「取任务结果」给出（受理阶段没有任何真实指标可回，编一个出来就是假数字）。
    """
    task_type = TOOL_TASK_TYPE[tool_name]
    algorithm = args.get("algorithm")
    params = dict(args.get("params") or {})
    # 核心的约定是"服务层把工具参数 k / n_components 合并进 params"（见 ml/api 模块说明）。
    # 漏了这一步的后果很隐蔽：训练照常成功，但用的是端侧自选值 —— 调用方传 8 个簇、
    # 结果却是 2 个（实测发生）。所以这两项必须在这里并进去，且只并本工具声明过的参数。
    for key in ("k", "n_components"):
        if args.get(key) is not None:
            params[key] = args[key]
    tag = args.get("tag")
    data_version = args.get("data_version")

    data_check = _check_data_version(ctx, data_version)

    # 占用判定复用"查占用状态"那一套（内部是执行侧自己的判定），不另写一套
    holder = writers._busy_holder(ctx)
    if holder:
        raise _busy_failure(holder, tool_name)

    # 任务目录与状态：先对齐产物目录（driver 只认进程环境里的取值），再建目录
    tasks._ensure_var_dir(ctx)
    model_id = _new_model_id()
    task_dir = writers._task_dir(model_id, ctx)
    if not os.path.isdir(task_dir):
        os.makedirs(task_dir)

    started_at = _now_utc()
    rt = tasks._driver(ctx)
    rt.write_status(model_id, stage_list=ML_STAGES, status="queued", stage="queued",
                    started_at=started_at, message="queued",
                    task_type=task_type, algorithm=algorithm,
                    data_version=data_version or "", tag=tag or "",
                    params=params, errors=[])

    _spawn_worker(model_id, task_type, algorithm, params, data_version, tag, ctx, task_dir)

    # 受理成功之后才留凭据：没被受理的任务不该有「本服务提交」的标记
    writers.write_submit_marker(
        model_id, ctx, tool=tool_name,
        orientation={"task_type": task_type, "algorithm": algorithm,
                     "data_version": data_version})

    payload = {
        "model_id": model_id,
        "status": "queued",
        "status_label": contract.STATUS_LABELS.get("queued", ""),
        "status_cn": contract.STATUS_LABELS.get("queued", ""),
        "task_type": task_type,
        "algorithm": algorithm,
        # 受理时回"预计生效参数"（与训练时同一套默认值与校验规则算出来的），
        # 让人在开始跑之前就能看见"多少棵树、多深、叶子最少几条"，而不是等结果。
        # 真正的生效值仍以结果里的 `params` 为准（含端侧自选的 k/n_components）。
        "params": _preview_params(task_type, algorithm, params),
        "created_at": started_at,
        "estimated_seconds": _estimated_seconds(tool_name),
        "estimated_seconds_note": ESTIMATE_NOTE,
    }
    if data_version:
        payload["data_version"] = data_version
    if tag:
        payload["tag"] = tag
    if data_check:
        payload["data_check"] = data_check
    return payload


def _preview_params(task_type, algorithm, params):
    """受理时的参数预览；核心缺该方法时退回请求值（不让预览拖垮受理）。"""
    try:
        from ml import api as ml_api
        return ml_api.effective_params(task_type, algorithm, params,
                                       k=params.get("k"), n_components=params.get("n_components"))
    except Exception:                                   # noqa: BLE001 —— 预览失败不影响受理
        return dict(params)

def _spawn_worker(model_id, task_type, algorithm, params, data_version, tag, ctx, task_dir):
    """以独立会话启动训练工作进程（便于按进程组取消）。

    为什么必须是**独立会话**：中止能力按进程组发信号，工作进程若与服务工作在同一
    进程组，取消时会连服务一起打；独立会话之后，整组终止只影响这个训练。
    日志写到任务目录下的 `ml_worker.log`，失败时可直接取来排障。
    """
    argv = [sys.executable, "-m", "mcpserver.ml_worker",
            "--task-id", model_id,
            "--task-type", task_type,
            "--algorithm", algorithm,
            "--params", json.dumps(params, ensure_ascii=False),
            "--data-version", data_version or "",
            "--tag", tag or ""]
    log_path = os.path.join(task_dir, "ml_worker.log")
    try:
        logf = io.open(log_path, "ab")
    except (IOError, OSError) as exc:
        raise StructuredError(
            contract.CODE_INTERNAL_ERROR,
            "无法创建工作进程日志文件：%s" % exc, task_id=model_id)
    try:
        subprocess.Popen(argv, stdout=logf, stderr=logf,
                         cwd=tasks._repo_root(ctx), start_new_session=True)
    except OSError as exc:
        raise StructuredError(
            contract.CODE_INTERNAL_ERROR,
            "无法启动训练工作进程：%s" % (getattr(exc, "strerror", None) or exc),
            task_id=model_id)


def handle_train_classifier(args, ctx):
    """训练与评估分类模型（朴素贝叶斯 / 随机森林）。"""
    return _submit_training(args, ctx, "train_classifier")


def handle_cluster_users(args, ctx):
    """训练用户群体聚类（K-Means / 凝聚层次）。"""
    return _submit_training(args, ctx, "cluster_users")


def handle_reduce_dimensions(args, ctx):
    """训练降维模型（PCA / NMF），并给出降维前后的聚类一致性对比。"""
    return _submit_training(args, ctx, "reduce_dimensions")


# ---------------------------------------------------------------------------
# 预测（同步短调用）
# ---------------------------------------------------------------------------

def _training_error(exc):
    """把核心的结构化错误转成服务层的结构化错误（**码原样保留**）。

    为什么按属性取而不是 `isinstance(exc, ml.api.TrainingError)`：核心可能因为缺依赖
    而根本导入不了，那时在异常处理里再去导入它的异常类会二次出错 —— 排障最需要的
    那句话反而丢了。核心的异常约定是"带 code / message / extra 三个属性"，按它取即可。
    """
    code = getattr(exc, "code", None) or contract.CODE_INTERNAL_ERROR
    message = getattr(exc, "message", None) or str(exc)
    extra = getattr(exc, "extra", None) or {}
    return StructuredError(code, message, **extra)


def _run_prediction(model_id, user_id, movie_id, ctx):
    """在工作进程里做一次预测（带时长上限）。

    为什么放到子进程而不是直接在本进程调：预测是同步短调用，但模型加载与特征构造
    都可能因为异常输入变慢甚至卡住；子进程能用**真实的时长上限**兜住，也不会因为
    核心里的异常把服务拖下水。代价是一次进程启动（不到一秒），相对"短调用"仍可接受。
    """
    argv = [sys.executable, "-m", "mcpserver.ml_worker", "--mode", "predict",
            "--model-id", model_id, "--user-id", str(user_id), "--movie-id", str(movie_id)]
    try:
        proc = subprocess.run(argv, cwd=tasks._repo_root(ctx), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=PREDICT_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        raise StructuredError(
            contract.CODE_DRIVER_TIMEOUT,
            "预测在 %d 秒内没有返回，已终止本次预测（模型可能异常，或该编号的数据规模异常）"
            % int(PREDICT_TIMEOUT_SECONDS), model_id=model_id)
    except OSError as exc:
        raise StructuredError(
            contract.CODE_INTERNAL_ERROR,
            "无法启动预测进程：%s" % (getattr(exc, "strerror", None) or exc),
            model_id=model_id)

    text = (proc.stdout or b"").decode("utf-8", "replace").strip()
    payload = None
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
            except ValueError:
                payload = None
            if payload is not None:
                break
    if payload is None:
        detail = (proc.stderr or b"").decode("utf-8", "replace").strip()[-800:]
        raise StructuredError(
            contract.CODE_DRIVER_BAD_OUTPUT,
            "预测没有给出可解析的结果（退出码 %s）" % proc.returncode,
            model_id=model_id, stderr=detail or None)
    if not payload.get("ok"):
        err = payload.get("error") or {}
        raise StructuredError(err.get("code") or contract.CODE_TASK_FAILED,
                              err.get("message") or "预测失败",
                              **{k: v for k, v in err.items() if k not in ("code", "message")})
    return payload


def handle_predict_high_rating(args, ctx):
    """用已训练的分类模型预测"某用户对某电影是否不低于四分"。"""
    model_id = args["model_id"]
    if not model_exists(ctx, model_id):
        raise StructuredError(
            contract.CODE_MODEL_NOT_FOUND,
            "模型不存在：%s（可用「列模型」看已登记编号）" % model_id, model_id=model_id)
    payload = _run_prediction(model_id, args["user_id"], args["movie_id"], ctx)
    out = {"model_id": model_id,
           "user_id": str(args["user_id"]),
           "movie_id": str(args["movie_id"])}
    for key in ("prediction", "model_version"):
        if payload.get(key) is not None:
            out[key] = payload[key]
    return out


# ---------------------------------------------------------------------------
# 两个只读查询
# ---------------------------------------------------------------------------

def handle_list_ml_models(args, ctx):
    """列出已登记的模型（编号、任务类型、算法、数据版本、创建时间与指标摘要）。"""
    try:
        payload = _api(ctx).list_models(tasks._repo_root(ctx))
    except StructuredError:
        raise
    except Exception as exc:                      # noqa: BLE001
        raise StructuredError(contract.CODE_INTERNAL_ERROR,
                              "列出模型失败：%s: %s" % (type(exc).__name__, exc))
    models = payload.get("models")
    if models is None:
        raise StructuredError(contract.CODE_INTERNAL_ERROR, "模型索引里没有模型列表字段")
    return {"models": models, "count": len(models)}


def handle_get_ml_model(args, ctx):
    """取某个模型的元数据与指标（含产物清单，用于下载与展示）。"""
    model_id = args["model_id"]
    try:
        payload = _api(ctx).get_model(model_id, tasks._repo_root(ctx))
    except StructuredError:
        raise
    except Exception as exc:                      # noqa: BLE001
        if getattr(exc, "code", None):
            raise _training_error(exc)
        raise StructuredError(contract.CODE_INTERNAL_ERROR,
                              "读取模型元数据失败：%s: %s" % (type(exc).__name__, exc),
                              model_id=model_id)
    model = payload.get("model")
    if model is None:
        raise StructuredError(
            contract.CODE_MODEL_NOT_FOUND,
            "模型不存在：%s（可用「列模型」看已登记编号）" % model_id, model_id=model_id)
    return {"model": model}


# ---------------------------------------------------------------------------
# 处理器登记表（分派层按名字取）
# ---------------------------------------------------------------------------

HANDLERS = {
    "train_classifier": handle_train_classifier,
    "cluster_users": handle_cluster_users,
    "reduce_dimensions": handle_reduce_dimensions,
    "predict_high_rating": handle_predict_high_rating,
    "list_ml_models": handle_list_ml_models,
    "get_ml_model": handle_get_ml_model,
}
