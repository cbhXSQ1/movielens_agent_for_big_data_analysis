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

返回信封比 driver 多四个字段（加在外层，不改内层 data，前端旧代码不受影响）：
    opts          本轮真实生效的运行口径（前端可回显 / 下次回传）
    engine        "rules" | "llm" —— 这一句是被谁解析的（如实交代，不冒充）
    llm           {"configured","used","note"} —— 有没有用到大模型
    task_started  这一句是否真的发起了任务（http_api 据此落口径快照）
"""

from . import explain, intent, session, tools

try:                        # 阶段 2 的可选增强层；没这个文件就自动走纯规则
    from . import llm_parse
except Exception:           # pragma: no cover —— 阶段 1 恒走这里
    llm_parse = None

#: 需要 task_id 的意图
NEEDS_TASK = ("task_status", "task_result", "get_samples", "get_report")


def _pack(name, reply, data=None, task_id=None, ok=True, started=False,
          engine="rules", opts=None, llm=None):
    """统一的返回信封构造。

    17 处返回全部走这里，避免手写时漏字段（opts / engine / llm / task_started）。
    """
    return {
        "ok": bool(ok),
        "intent": name,
        "intent_cn": intent.INTENT_CN.get(name, name),
        "reply": reply,
        "data": data,
        "task_id": task_id,
        "task_started": bool(started),
        "opts": dict(opts or {}),
        "engine": engine,
        "llm": dict(llm or {}),
    }


def _llm_worth_trying(cfg, parsed):
    """什么时候值得去问大模型。

    默认策略 `fallback`：规则已经很有把握时就别浪费这一次调用，
    既保证「规则是主路径」，也让绝大多数交互是零延迟、零成本的。
    `always` 用来在答辩现场演示大模型能力；其它值 = 全关。
    """
    if cfg is None or not cfg.usable:
        return False
    if "intent" not in cfg.use_for:
        return False
    if cfg.mode == "always":
        return True
    if cfg.mode != "fallback":
        return False
    # fallback 的语义写死成：**只在规则彻底没识别出来（unknown）时才去兜底问它。**
    #
    # 为什么不按"置信度阈值"判断：规则一旦命中关键词，置信度就 ≥0.8
    # （intent.py：0.5 + 0.15×命中词数，实测常用说法落在 0.80~0.85），
    # 所以任何"低于某个阈值才去问"的写法都是**永不生效的死条件**——
    # 试过 0.5（永不触发）和 0.8（0.80 不小于 0.8，还是不触发）。
    # 与其留一个没人看得懂、调了也没用的旋钮，不如把语义写清楚：
    #   fallback = 规则听不懂才兜底；想在现场演示大模型能力，用 mode=always。
    return parsed.get("intent") == "unknown"


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


def respond(text, context=None, auto_start=True, exec_mode=None,
            task_opts=None, llm_cfg=None):
    """处理一句自然语言，返回 Agent 信封。

    task_opts: dict，允许 rules / scoring / data_version / tag / exec_mode / scope。
               调用方应当先用 session.merge(快照, 本次显式) 得到它再传进来。
    exec_mode: 向后兼容的快捷写法；task_opts 里已有 exec_mode 时以 task_opts 为准。
    llm_cfg:   阶段 2 的大模型配置；None / 不可用时一律走规则解析。
    """
    parsed = intent.parse(text)
    name = parsed["intent"]
    params = parsed["params"]
    ctx = dict(context or {})
    engine = "rules"
    llm_info = {"configured": False, "used": False, "note": ""}

    # 本轮生效的运行口径：显式 > 快照（已由 http_api 合并）> driver 默认
    opts = session.normalize(task_opts or {})
    if exec_mode and "exec_mode" not in opts:
        opts["exec_mode"] = exec_mode

    # ---------------- 大模型可选增强：只在规则没把握时才问它（默认策略） ----------------
    if llm_cfg is not None and llm_parse is not None:
        llm_info["configured"] = bool(llm_cfg.usable)
        if _llm_worth_trying(llm_cfg, parsed):
            got = llm_parse.parse_llm(text, llm_cfg)      # 失败返回 None
            if got:
                parsed, engine = got, "llm"
                params = parsed["params"]
                llm_info["used"] = True
                llm_info["note"] = "意图由大模型解析（约束：只准输出白名单内的意图与参数）"
            else:
                llm_info["note"] = ("大模型不可用或返回不合法，已回落到规则解析："
                                    + (llm_parse.last_reason() or "未知原因"))

    def say(reply, site):
        """白名单内的自述句才允许润色；任何护栏不过都返回原句。

        含运行结果数字的文本（explain.* 产出）**一个字都不润色** ——
        这是「大模型碰不到数字」的结构性保证。
        """
        if llm_parse is None or llm_cfg is None:
            return reply
        got, why = llm_parse.polish(reply, site, llm_cfg)
        if got and not why:
            llm_info["used"] = True
            note = "措辞由大模型润色（所有数字仍来自 Hadoop 运行结果）"
            llm_info["note"] = (llm_info["note"] + "；" if llm_info["note"] else "") + note
            return got
        return reply

    # ---------------------------------------------------------- 未知意图
    if name == "unknown":
        return _pack(name, say(intent.help_text(), "intent_unknown"),
                     None, None, opts=opts, engine=engine, llm=llm_info)

    # ---------------------------------------------------------- 帮助
    if name == "help":
        return _pack(name, say(intent.help_text(), "help"),
                     None, None, opts=opts, engine=engine, llm=llm_info)

    # ---------------------------------------------------------- 方案列表
    if name == "list_schemes":
        env = tools.list_schemes()
        return _pack(name, explain.explain_schemes(env), env, None,
                     ok=env.get("ok", False), opts=opts, engine=engine, llm=llm_info)

    # ---------------------------------------------------------- 历史任务
    if name == "list_tasks":
        env = tools.list_tasks()
        tasks = env.get("tasks") or []
        if not tasks:
            return _pack(name, "目前没有任何任务记录。", env, None,
                         opts=opts, engine=engine, llm=llm_info)
        lines = ["最近 %d 个任务：" % len(tasks)]
        for t in tasks:
            lines.append("  · %s ｜ %s ｜ %s ｜ %s"
                         % (t.get("task_id", "?"), t.get("status", "?"),
                            t.get("started_at", "?"), t.get("data_version", "?")))
        return _pack(name, "\n".join(lines), env, None,
                     opts=opts, engine=engine, llm=llm_info)

    # ---------------------------------------------------------- 快速演示
    if name == "quick_demo":
        env = tools.quick_clean_demo(sample=params.get("n") or 2000)
        if not env.get("ok"):
            return _pack(name, explain.explain_error(env), env, None, ok=False,
                         opts=opts, engine=engine, llm=llm_info)
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
        return _pack(name, "\n".join(lines), env, None,
                     opts=opts, engine=engine, llm=llm_info)

    # ---------------------------------------------------------- 需要 task_id 的意图
    if name in NEEDS_TASK:
        tid, source = _pick_task_id(params, ctx)
        if not tid:
            return _pack(name, "还没有任何任务，先说「清洗并评估」发起一个吧。",
                         None, None, ok=False, opts=opts, engine=engine, llm=llm_info)

        note = ""
        if source == "latest":
            note = "（你没指定任务，我用的是最近一个：%s）\n" % tid

        if name == "task_status":
            env = tools.get_task_status(tid)
            return _pack(name, note + explain.explain_status(env), env, tid,
                         ok=env.get("ok", False), opts=opts, engine=engine, llm=llm_info)

        if name == "task_result":
            env = tools.get_task_result(tid)
            return _pack(name, note + explain.explain_result(env), env, tid,
                         ok=env.get("ok", False), opts=opts, engine=engine, llm=llm_info)

        if name == "get_samples":
            env = tools.get_samples(tid,
                                    params.get("sample_type", "quarantine"),
                                    params.get("table", "ratings"),
                                    params.get("n", 5))
            return _pack(name, note + explain.explain_samples(env), env, tid,
                         ok=env.get("ok", False), opts=opts, engine=engine, llm=llm_info)

        if name == "get_report":
            env = tools.get_report(tid, "md")
            if not env.get("ok"):
                return _pack(name, note + explain.explain_error(env), env, tid,
                             ok=False, opts=opts, engine=engine, llm=llm_info)
            return _pack(name, note + "已生成评估报告（全文见 data.report）。",
                         env, tid, opts=opts, engine=engine, llm=llm_info)

    # ---------------------------------------------------------- 发起清洗 + 评估
    if name == "clean_evaluate":
        # use_default 不再是死参数：用户明说"用默认规则"时，先在发起之前
        # 对默认方案做一次真实自检，过不了就不发任务（不编造、不退让）
        if params.get("use_default"):
            check = tools.validate_config(opts.get("rules"), opts.get("scoring"))
            if not isinstance(check, dict) or not check.get("ok"):
                reply = ("你说要用默认方案，但我按现有配置做了一次自检，没通过，"
                         "所以没有发起任务。下面是自检的真实输出，未做任何推测：\n"
                         + explain.explain_error(check))
                return _pack(name, reply, check, None, ok=False,
                             opts=opts, engine=engine, llm=llm_info)

        if not auto_start:
            return _pack(name, say(
                "已理解需求：用默认方案发起清洗 + 五维评估。"
                "（当前为只解析模式，未真正提交任务）", "parse_only"),
                parsed, None, opts=opts, engine=engine, llm=llm_info)

        env = tools.start_cleaning_task(
            rules=opts.get("rules"), scoring=opts.get("scoring"),
            data_version=opts.get("data_version"), tag=opts.get("tag"),
            exec_mode=opts.get("exec_mode"), scope=opts.get("scope"))
        if not env.get("ok"):
            return _pack(name, explain.explain_error(env), env, None, ok=False,
                         opts=opts, engine=engine, llm=llm_info)
        tid = env.get("task_id")
        lines = [
            "任务已提交，ID：%s" % tid,
            "它会依次完成：用户表清洗 → 电影表清洗 → 评分表清洗 → 打标 → "
            "清洗前评分 → 清洗后评分 → 汇总 → 发布。",
        ]
        # 口径是本轮真实生效的，原样说出；不写行数（行数要等 result 回来按真实数据判定）
        if opts.get("scope") == "sample":
            lines.append("本次是样本口径（driver 会加 --sample），跑得快，"
                         "但这些数字不属于正式口径，不能用于汇报。")
        else:
            lines.append("本次是全量口径（正式口径），全量集群跑约 10 分钟，"
                         "期间你可以随时问我「跑到哪一步了」。")
        return _pack(name, say("\n".join(lines), "task_submitted"),
                     env, tid, started=True, opts=opts, engine=engine, llm=llm_info)

    return _pack(name, say(intent.help_text(), "intent_unknown"), parsed, None,
                 opts=opts, engine=engine, llm=llm_info)
