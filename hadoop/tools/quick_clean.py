#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hadoop/tools/quick_clean.py —— 演示性数据清洗：直接调本地 runner，不经过 Hadoop。

用途：现场演示、前端取数、Agent 联调时"秒级拿到干净数据"。

与集群任务产出**同源同数**：内部直接调用 `engine.pipeline.run_local`（同一个引擎、
同一条黄金测试路径），所以这里拿到的 counts / 分数与集群全量任务一致；
但**不依赖任何 Hadoop 守护进程**，纯标准库，几秒到 90 秒出结果。

用法::

    python3 hadoop/tools/quick_clean.py [--raw DIR] [--out DIR] [--sample N]
                                        [--task-id T] [--quiet]

  --raw     原始数据目录（含 ratings.dat/users.dat/movies.dat）；
            缺省取 $ML_RAW_DIR
  --out     输出目录；缺省 <repo>/.demo/quick（已 gitignore）
  --sample  只清洗评分表前 N 行、维表全量 —— 沿用 upload_raw.sh 的口径：
            三表各自抽样会破坏引用完整性（抽样评分的 UserID/MovieID 几乎必然
            不在抽样维表里，会被 X1/X2 全数隔离成孤儿）
  --task-id 写入 summary 的任务标识（纯装饰，便于前端合并展示）
  --quiet   只在 stdout 输出 summary JSON，不打印进度

stdout 永远是一个 JSON 信封（与 driver 的约定一致）：
  {"ok": true, "summary": {...}} 或 {"ok": false, "error": {...}}

零 Hadoop 依赖，绝不触碰 config/、agent/、frontend/。
"""
import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO_ROOT, "hadoop"))

from engine.config_loader import load_schemes  # noqa: E402
from engine.pipeline import TABLE_FILES, run_local  # noqa: E402

RULES = os.path.join(REPO_ROOT, "config", "cleaning_rules.v1.json")
SCORING = os.path.join(REPO_ROOT, "config", "scoring_scheme.v1.json")


def parse_args(argv):
    opts = {"raw": "", "out": "", "sample": 0, "task-id": "quick", "quiet": False}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            key = a[2:]
            if "=" in key:
                key, val = key.split("=", 1)
                opts[key] = val
            elif i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                opts[key] = argv[i + 1]
                i += 1
            else:
                opts[key] = "1"
        i += 1
    return opts


def resolve_raw(opts):
    raw = opts.get("raw") or os.environ.get("ML_RAW_DIR") or ""
    if not raw:
        raise ValueError("找不到原始数据：请用 --raw 指定，或设置 ML_RAW_DIR")
    for t in TABLE_FILES.values():
        if not os.path.isfile(os.path.join(raw, t)):
            raise ValueError("原始数据目录缺少 %s：%s" % (t, raw))
    return os.path.abspath(raw)


def build_input(raw, sample):
    """返回 run_local 的输入目录。

    sample <= 0 时直接用原始目录（不复制）；
    否则把三表抄进临时目录，评分表只取前 N 行、维表全量
    （原因见模块 docstring 与 upload_raw.sh 的注释）。
    """
    if sample <= 0:
        return raw
    d = tempfile.mkdtemp(prefix="quick-clean-")
    for table in ("users", "movies"):
        shutil.copy2(os.path.join(raw, TABLE_FILES[table]),
                     os.path.join(d, TABLE_FILES[table]))
    rp = os.path.join(raw, TABLE_FILES["ratings"])
    with io.open(rp, "rb") as fh:
        first = b"".join([fh.readline() for _ in range(sample)])
    with io.open(os.path.join(d, TABLE_FILES["ratings"]), "wb") as fh:
        fh.write(first)
    return d


def quick_clean(raw, out, sample=0, task_id="quick", processed_at=None,
                quiet=False):
    """清洗 + 评分 + 落盘，返回 summary（与 driver result 的 counts/scores 同源）。

    直接调用 engine.pipeline.run_local —— 同一引擎，本地黄金测试与集群对账
    都验证过它产出的数字，因此本工具的 summary 与集群任务结果一致。
    """
    if not quiet:
        sys.stderr.write("quick_clean: raw=%s out=%s sample=%s\n"
                         % (raw, out, sample))
    schemes = load_schemes(RULES, SCORING)
    tmp = None
    try:
        tmp = build_input(raw, sample)
        stats = run_local(tmp, schemes, out, task_id, processed_at=processed_at)
    finally:
        if tmp is not None and tmp != raw:
            shutil.rmtree(tmp, ignore_errors=True)
    return {
        "task_id": task_id,
        "data_version": schemes.rules["data_version"]["id"],
        "counts": stats["counts"],
        "scores": stats["scores"],
        "rule_hits": stats["rule_hits"],
        "detail": stats["detail"],
        "out_dir": os.path.abspath(out),
        "sample": sample,
    }


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    opts = parse_args(argv)
    try:
        raw = resolve_raw(opts)
        out = opts.get("out") or os.path.join(REPO_ROOT, ".demo", "quick")
        sample = int(opts.get("sample", 0) or 0)
        if sample < 0:
            raise ValueError("--sample 不能为负")
        summary = quick_clean(raw, out, sample, opts.get("task-id", "quick"),
                              quiet=bool(opts.get("quiet")))
    except Exception as exc:                                    # noqa: BLE001
        sys.stdout.write(json.dumps(
            {"ok": False, "error": {"code": type(exc).__name__, "message": str(exc)}},
            ensure_ascii=False))
        sys.stdout.write("\n")
        return 2
    sys.stdout.write(json.dumps({"ok": True, "interface_version": "1.0",
                                 "summary": summary}, ensure_ascii=False))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())