# -*- coding: utf-8 -*-
"""mcpserver/ml_worker.py —— 机器学习的**工作进程**（训练与预测都在这里跑）。

为什么要单独一个进程，而不是在服务进程里直接算：

  * **可取消**：服务以独立会话拉起它，中止能力按进程组终止，训练不会把服务一起带走；
  * **可如实报进度**：训练过程中把阶段写进与清洗任务同构的状态文件，于是
    「查任务状态 / 等任务结束 / 列任务 / 查占用状态」无需任何改动就能看见它；
  * **互不干扰**：机器学习依赖（scikit-learn 那一套）只在工作进程里用到，服务进程
    即使在没有这些依赖的环境里也能起来（缺依赖时如实报"核心不可用"）。

用法（两种模式）：

    python -m mcpserver.ml_worker --task-id ml-... --task-type classification \\
        --algorithm random_forest --params '{"n_estimators": 120}' --data-version ml1m-clean-v1

    python -m mcpserver.ml_worker --mode predict --model-id ml-... --user-id 1 --movie-id 1193

预测模式把一行 JSON 打到标准输出：成功是 `{"ok": true, ...}`，失败是
`{"ok": false, "error": {"code": ..., "message": ...}}`；退出码 0 成功、3 结构化失败。
"""

import argparse
import json
import os
import sys
import traceback

# 工作进程可能被单独拉起（例如排障时手敲），这里自己把仓库根与 hadoop 目录补进导入路径，
# 不依赖调用方设置了 PYTHONPATH。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_HADOOP_ROOT = os.path.join(_REPO_ROOT, "hadoop")
for _path in (_HADOOP_ROOT, _REPO_ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from mcpserver.ml import ML_STAGES                                  # noqa: E402
from mcpserver.contract import CODE_INTERNAL_ERROR                  # noqa: E402


def _driver():
    """执行侧的状态读写（任务状态文件的字段结构由它统一，两边不会各写一套）。"""
    from driver import run_task
    return run_task


def _stage_index(name, current):
    """阶段名 → 序号；认不出的阶段**保持当前进度**，不退回 0。

    为什么不退：核心将来加了新阶段（例如"抽样"），旧的服务端不该把进度显示成"回到
    起点"——那会让人以为任务重启了。认不出就只更新说明文字，进度停在原地。
    """
    if name in ML_STAGES:
        return ML_STAGES.index(name)
    return current


def _write(rt, task_id, stage, index, **fields):
    """写一次状态：阶段名与序号由这里统一算，其余字段原样交给执行侧的状态写入。"""
    return rt.write_status(task_id, stage_list=ML_STAGES, stage=stage,
                           message=fields.pop("message", "running %s" % stage), **fields)


def _run_training(args):
    """训练模式：跑核心、写状态、落结果。"""
    rt = _driver()
    task_id = args.task_id
    params = json.loads(args.params) if args.params else {}

    _write(rt, task_id, "load_data", ML_STAGES.index("load_data"),
           status="running", task_type=args.task_type, algorithm=args.algorithm,
           data_version=args.data_version or "", params=params)

    state = {"index": ML_STAGES.index("load_data")}

    def progress(stage, **fields):
        """核心回调：写阶段与进度。认不出的阶段只更新说明（见 `_stage_index`）。"""
        state["index"] = _stage_index(stage, state["index"])
        extra = {}
        if fields.get("message"):
            extra["message"] = str(fields["message"])
        _write(rt, task_id, stage if stage in ML_STAGES else ML_STAGES[state["index"]],
               state["index"], status="running", **extra)

    from ml.api import TrainingError, run_training

    try:
        result = run_training(args.task_type, args.algorithm, params,
                              _REPO_ROOT, progress=progress,
                              data_version=args.data_version or None,
                              task_id=task_id)
    except TrainingError as exc:
        _fail(rt, task_id, state["index"], exc.code, exc.message, exc.extra)
        return 3
    except Exception as exc:                       # noqa: BLE001 —— 兜底不得吞掉信息
        _fail(rt, task_id, state["index"], CODE_INTERNAL_ERROR,
              "训练出现未预期的错误：%s: %s" % (type(exc).__name__, exc),
              {"traceback": traceback.format_exc()[-1500:]})
        return 3

    # 成功：先落结果，再写终态 —— 顺序反过来的话，可能出现"状态说成功、结果还没写完"
    # 的窗口，取结果会拿到半份数据。
    payload = dict(result or {})
    payload.setdefault("model_id", task_id)
    payload.setdefault("task_type", args.task_type)
    payload.setdefault("algorithm", args.algorithm)
    payload.setdefault("status", "succeeded")
    payload["finished_at"] = rt.now_utc()
    result_path = os.path.join(rt.task_dir(task_id), "result.json")
    with open(result_path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2))
        handle.write(u"\n")

    # 把**实际使用的数据版本**写回状态：受理时用户可能没传版本（由端侧取当前发布版本），
    # 若不回写，任务列表里这一列就是空的，看起来像"不知道用了哪份数据"。
    rt.write_status(task_id, stage_list=ML_STAGES, status="succeeded",
                    stage="done", finished_at=payload["finished_at"],
                    message="done", task_type=args.task_type,
                    algorithm=args.algorithm, params=payload.get("params") or params,
                    data_version=payload.get("data_version") or (args.data_version or ""),
                    errors=[])
    # 这一行只进任务目录下的日志（不是对外返回），所以给绝对路径更好排障；
    # 对外返回里的路径一律由服务层按"任务编号 + 相对路径"给出。
    _emit({"ok": True, "model_id": task_id, "status": "succeeded",
           "result_path": result_path})
    return 0


def _fail(rt, task_id, index, code, message, extra=None):
    """失败收尾：把结构化码与说明写进失败记录（沿用"失败记录带码"的既有形状）。"""
    stage = ML_STAGES[index] if 0 <= index < len(ML_STAGES) else "queued"
    record = {"stage": stage, "message": message, "code": code}
    for key, value in (extra or {}).items():
        if value is not None and key not in record:
            record[key] = value
    rt.write_status(task_id, stage_list=ML_STAGES, status="failed", stage=stage,
                    errors=[record], message=message)
    _emit({"ok": False, "model_id": task_id, "status": "failed",
           "error": {"code": code, "message": message}})


def _run_predict(args):
    """预测模式：调核心算一次，把结果打成一行 JSON。"""
    from ml.api import TrainingError, predict_rating

    try:
        result = predict_rating(args.model_id, args.user_id, args.movie_id, _REPO_ROOT)
    except TrainingError as exc:
        _emit({"ok": False, "error": dict({"code": exc.code, "message": exc.message},
                                          **exc.extra)})
        return 3
    except Exception as exc:                       # noqa: BLE001
        _emit({"ok": False, "error": {
            "code": CODE_INTERNAL_ERROR,
            "message": "预测出现未预期的错误：%s: %s" % (type(exc).__name__, exc)}})
        return 3
    payload = dict(result or {})
    payload["ok"] = True
    _emit(payload)
    return 0


def _emit(payload):
    """把一行 JSON 打到标准输出（服务端按最后一行 JSON 解析）。"""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def build_parser():
    parser = argparse.ArgumentParser(prog="ml_worker", description="机器学习工作进程")
    parser.add_argument("--mode", default="train", choices=("train", "predict"))
    parser.add_argument("--task-id")
    parser.add_argument("--task-type")
    parser.add_argument("--algorithm")
    parser.add_argument("--params", default="")
    parser.add_argument("--data-version", default="")
    parser.add_argument("--tag", default="")
    parser.add_argument("--model-id")
    parser.add_argument("--user-id")
    parser.add_argument("--movie-id")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.mode == "predict":
        return _run_predict(args)
    missing = [name for name in ("task_id", "task_type", "algorithm") if not getattr(args, name)]
    if missing:
        _emit({"ok": False, "error": {"code": CODE_INTERNAL_ERROR,
                                      "message": "训练模式缺少参数：%s" % "、".join(missing)}})
        return 2
    return _run_training(args)


if __name__ == "__main__":
    sys.exit(main())
