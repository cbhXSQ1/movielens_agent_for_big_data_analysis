# -*- coding: utf-8 -*-
"""第 5 节 Agent：命令行入口。

用法示例（在仓库根目录执行）：
    python3 -m agent.cli validate
    python3 -m agent.cli schemes --explain
    python3 -m agent.cli start --exec local --foreground
    python3 -m agent.cli status --task-id <id> --explain
    python3 -m agent.cli result --task-id <id> --explain
    python3 -m agent.cli samples --task-id <id> --type quarantine --table ratings --n 5
    python3 -m agent.cli report --task-id <id> --format md
    python3 -m agent.cli tasks
    python3 -m agent.cli quick-clean --sample 2000

默认输出 driver 的原始 JSON 信封（便于联调）；
加 --explain 则额外输出一段中文解释（给人和给前端展示用）。
"""

import argparse
import json
import sys

from . import explain, tools


def _emit(envelope, do_explain, explainer):
    """打印 JSON 信封；需要时再打印中文解释。"""
    print(json.dumps(envelope, ensure_ascii=False, indent=2))
    if do_explain:
        print("\n---- 中文解释 ----")
        print(explainer(envelope))
    return 0 if envelope.get("ok") else 1


def build_parser():
    p = argparse.ArgumentParser(prog="agent", description="MovieLens 数据治理 Agent 工具集")
    sub = p.add_subparsers(dest="tool", required=True)

    sp = sub.add_parser("validate", help="校验清洗/评分配置")
    sp.add_argument("--rules")
    sp.add_argument("--scoring")
    sp.add_argument("--explain", action="store_true")

    sp = sub.add_parser("schemes", help="列出已登记方案")
    sp.add_argument("--explain", action="store_true")

    sp = sub.add_parser("start", help="发起清洗+评分任务")
    sp.add_argument("--rules")
    sp.add_argument("--scoring")
    sp.add_argument("--data-version")
    sp.add_argument("--tag")
    sp.add_argument("--exec", dest="exec_mode", choices=["local", "cluster"])
    sp.add_argument("--foreground", action="store_true")
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--explain", action="store_true")

    sp = sub.add_parser("status", help="查询任务状态")
    sp.add_argument("--task-id", required=True)
    sp.add_argument("--explain", action="store_true")

    sp = sub.add_parser("result", help="获取任务结果（仅成功任务）")
    sp.add_argument("--task-id", required=True)
    sp.add_argument("--explain", action="store_true")

    sp = sub.add_parser("samples", help="取清洗后/隔离区样本")
    sp.add_argument("--task-id", required=True)
    sp.add_argument("--type", default="cleaned", choices=["cleaned", "quarantine"])
    sp.add_argument("--table", default="movies", choices=["users", "movies", "ratings"])
    sp.add_argument("--n", type=int, default=5)
    sp.add_argument("--explain", action="store_true")

    sp = sub.add_parser("report", help="获取评估报告")
    sp.add_argument("--task-id", required=True)
    sp.add_argument("--format", default="md", choices=["md", "json"])
    sp.add_argument("--explain", action="store_true")

    sp = sub.add_parser("tasks", help="列出最近任务")
    sp.add_argument("--explain", action="store_true")

    sp = sub.add_parser("quick-clean", help="附加工具：演示性快速清洗（不经 Hadoop）")
    sp.add_argument("--raw")
    sp.add_argument("--out")
    sp.add_argument("--sample", type=int)
    sp.add_argument("--explain", action="store_true")

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    name = args.tool

    if name == "validate":
        env = tools.validate_config(args.rules, args.scoring)
        return _emit(env, args.explain, lambda e: json.dumps(e, ensure_ascii=False))

    if name == "schemes":
        env = tools.list_schemes()
        return _emit(env, args.explain, explain.explain_schemes)

    if name == "start":
        env = tools.start_cleaning_task(
            rules=args.rules, scoring=args.scoring,
            data_version=args.data_version, tag=args.tag,
            foreground=args.foreground, force=args.force,
            exec_mode=args.exec_mode)
        return _emit(env, args.explain, explain.explain_status)

    if name == "status":
        env = tools.get_task_status(args.task_id)
        return _emit(env, args.explain, explain.explain_status)

    if name == "result":
        env = tools.get_task_result(args.task_id)
        return _emit(env, args.explain, explain.explain_result)

    if name == "samples":
        env = tools.get_samples(args.task_id, args.type, args.table, args.n)
        return _emit(env, args.explain, explain.explain_samples)

    if name == "report":
        env = tools.get_report(args.task_id, args.format)
        return _emit(env, False, None)

    if name == "tasks":
        env = tools.list_tasks()
        return _emit(env, False, None)

    if name == "quick-clean":
        env = tools.quick_clean_demo(raw_dir=args.raw, sample=args.sample, out=args.out)
        return _emit(env, args.explain, lambda e: json.dumps(e, ensure_ascii=False))

    print("未知工具：%s" % name, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
