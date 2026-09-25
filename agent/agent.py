# -*- coding: utf-8 -*-
"""第 5 节 Agent：真正的入口——输入一句人话，输出一段人话 + 结构化数据。

    from agent import agent
    r = agent.respond("用默认规则清洗 MovieLens 1M 并评估五维质量")
    print(r["reply"])      # 给用户的中文回答
    r["data"]              # 原始 JSON 信封，给前端渲染

设计红线（与 tools / explain 一致）：
    * 不编造：失败或未完成时，原样转述状态与原因
    * 隔离 ≠ 修复：解释结果时同屏给出数据量变化与隔离影响
    * 权威结果以集群任务（start + result）为准，quick_clean 只用于演示
"""

from . import explain, intent, tools

#: 需要 task_id 的意图
NEEDS_TASK = ("task_status", "task_result", "get_samples", "get_report")


def _pick_task_id(params, context):
    """优先用句子里带的 ID，其次用上下文，最后回退到最近一个任务（并如实说明）。"""
    tid = params.get("task_id") or (context or {}).get("task_id")
    source = "explicit" if params.get("task_id") else ("context" if tid else "")
    if tid:
        return tid, source
    env = tools.list_tasks()
    tasks = (env.get("tasks") or []) if env.get("ok") else []
    if tasks:
        return tasks[0].get("task_id"), "latest"
    return None, ""


def respond(text, context=None, auto_start=True, exec_mode=None):
    """处理一句自然语言，返回 {"ok","intent","intent_cn","reply","data","task_id"}。"""
    parsed = intent.parse(text)
    name = parsed["intent"]
    params = parsed["params"]
    ctx = dict(context or {})

    # ---------------------------------------------------------- 未知意图
    if name == "unknown":
        return {"ok": True, "intent": name, "intent_cn": "未识别",
                "reply": intent.help_text(), "data": None, "task_id": None}

    # ---------------------------------------------------------- 帮助
    if name == "help":
        return {"ok": True, "intent": name, "intent_cn": intent.INTENT_CN[name],
                "reply": intent.help_text(), "data": None, "task_id": None}

    # ---------------------------------------------------------- 方案列表
    if name == "list_schemes":
        env = tools.list_schemes()
        return {"ok": env.get("ok", False), "intent": name,
                "intent_cn": intent.INTENT_CN[name],
                "reply": explain.explain_schemes(env), "data": env, "task_id": None}

    # ---------------------------------------------------------- 历史任务
    if name == "list_tasks":
        env = tools.list_tasks()
        tasks = env.get("tasks") or []
        if not tasks:
            return {"ok": True, "intent": name, "intent_cn": intent.INTENT_CN[name],
                    "reply": "目前没有任何任务记录。", "data": env, "task_id": None}
        lines = ["最近 %d 个任务：" % len(tasks)]
        for t in tasks:
            lines.append("  · %s ｜ %s ｜ %s ｜ %s"
                         % (t.get("task_id", "?"), t.get("status", "?"),
                            t.get("started_at", "?"), t.get("data_version", "?")))
        return {"ok": True, "intent": name, "intent_cn": intent.INTENT_CN[name],
                "reply": "\n".join(lines), "data": env, "task_id": None}

    # ---------------------------------------------------------- 快速演示
    if name == "quick_demo":
        env = tools.quick_clean_demo(sample=params.get("n") or 2000)
        if not env.get("ok"):
            return {"ok": False, "intent": name, "intent_cn": intent.INTENT_CN[name],
                    "reply": explain.explain_error(env), "data": env, "task_id": None}
        summary = (env.get("summary") or {})
        counts = summary.get("counts") or {}
        scores = summary.get("scores") or {}
        lines = [
            "快速演示完成（不走 Hadoop，只用于预览，不能当正式结果）。",
            "抽样行数：%s" % summary.get("sample", 0),
        ]
        if counts.get("output"):
            lines.append("输出：%s" % counts["output"])
        if scores.get("before") and scores.get("after"):
            lines.append("综合分：%s → %s"
                         % (scores["before"].get("composite"),
                            scores["after"].get("composite")))
        lines.append("正式结果请用「清洗并评估」，那才是 Hadoop 集群跑出来的。")
        return {"ok": True, "intent": name, "intent_cn": intent.INTENT_CN[name],
                "reply": "\n".join(lines), "data": env, "task_id": None}

    # ---------------------------------------------------------- 需要 task_id 的意图
    if name in NEEDS_TASK:
        tid, source = _pick_task_id(params, ctx)
        if not tid:
            return {"ok": False, "intent": name, "intent_cn": intent.INTENT_CN[name],
                    "reply": "还没有任何任务，先说「清洗并评估」发起一个吧。",
                    "data": None, "task_id": None}

        note = ""
        if source == "latest":
            note = "（你没指定任务，我用的是最近一个：%s）\n" % tid

        if name == "task_status":
            env = tools.get_task_status(tid)
            return {"ok": env.get("ok", False), "intent": name,
                    "intent_cn": intent.INTENT_CN[name],
                    "reply": note + explain.explain_status(env),
                    "data": env, "task_id": tid}

        if name == "task_result":
            env = tools.get_task_result(tid)
            return {"ok": env.get("ok", False), "intent": name,
                    "intent_cn": intent.INTENT_CN[name],
                    "reply": note + explain.explain_result(env),
                    "data": env, "task_id": tid}

        if name == "get_samples":
            env = tools.get_samples(tid,
                                    params.get("sample_type", "quarantine"),
                                    params.get("table", "ratings"),
                                    params.get("n", 5))
            return {"ok": env.get("ok", False), "intent": name,
                    "intent_cn": intent.INTENT_CN[name],
                    "reply": note + explain.explain_samples(env),
                    "data": env, "task_id": tid}

        if name == "get_report":
            env = tools.get_report(tid, "md")
            if not env.get("ok"):
                return {"ok": False, "intent": name,
                        "intent_cn": intent.INTENT_CN[name],
                        "reply": note + explain.explain_error(env),
                        "data": env, "task_id": tid}
            return {"ok": True, "intent": name, "intent_cn": intent.INTENT_CN[name],
                    "reply": note + "已生成评估报告（全文见 data.report）。",
                    "data": env, "task_id": tid}

    # ---------------------------------------------------------- 发起清洗 + 评估
    if name == "clean_evaluate":
        if not auto_start:
            return {"ok": True, "intent": name, "intent_cn": intent.INTENT_CN[name],
                    "reply": "已理解需求：用默认方案发起清洗 + 五维评估。"
                             "（当前为只解析模式，未真正提交任务）",
                    "data": parsed, "task_id": None}
        env = tools.start_cleaning_task(exec_mode=exec_mode)
        if not env.get("ok"):
            return {"ok": False, "intent": name, "intent_cn": intent.INTENT_CN[name],
                    "reply": explain.explain_error(env), "data": env, "task_id": None}
        tid = env.get("task_id")
        return {"ok": True, "intent": name, "intent_cn": intent.INTENT_CN[name],
                "reply": "任务已提交，ID：%s\n"
                         "它会依次完成：用户表清洗 → 电影表清洗 → 评分表清洗 → 打标 → "
                         "清洗前评分 → 清洗后评分 → 汇总 → 发布。\n"
                         "全量集群跑约 10 分钟，期间你可以随时问我「跑到哪一步了」。"
                         % tid,
                "data": env, "task_id": tid}

    return {"ok": True, "intent": name, "intent_cn": intent.INTENT_CN.get(name, name),
            "reply": intent.help_text(), "data": parsed, "task_id": None}
