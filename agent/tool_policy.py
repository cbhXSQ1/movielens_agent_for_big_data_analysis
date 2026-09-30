# -*- coding: utf-8 -*-
"""第 5 节 Agent v2：策略层 —— 声明式策略表 + 准入校验（阶段 2）。

职责（docs/agent/llm-agent-plan.md §3）：在 LLM 循环（阶段 3）调用工具之前
拦截危险/非法调用；规则与 driver 侧同源（docs/hadoop/driver-refactor-plan.md
§7.2），driver 自身仍必须校验（不能只靠本层），本层负责**调用前拦截与解释**。

策略语义（现有 9 工具；v1.1 的 start_clean_task / score_task 由 PENDING_V11
标注，落地时在此追加）：
  * locks=True        —— 占用任务锁（与其它锁工具串行；同一时刻只能一个写任务）
  * requires_raw=True —— 前置依赖：原始数据可读
  * read_only=True    —— 只读，可并行

校验器 `check_call` 吃 facts（运行期事实，由 llm_loop 收集）：
    {"running_task_id": "…"|None, "raw_readable": bool, "raw_dir": "…"|None}
返回 (True, "") 或 (False, 拒绝原因)。**绝不猜测**：facts 缺字段按保守值
处理（锁字段缺省视为有任务在跑？否 —— 缺省按"无事实"直通会把风险放行；
这里保守：locks 工具在 running_task_id 未提供时直接拒绝，见实现注释）。
"""

# ---------------------------------------------------------------------------
# 策略表（与 llm-agent-plan §3 / driver-refactor-plan §7.2 同源；v1.1 追加点）
# ---------------------------------------------------------------------------

POLICY = {
    # 写：占用任务锁 + 原始数据前置
    "start_cleaning_task": {"locks": True, "requires_raw": True},
    # v1.1 精细任务：锁 与 full 一致；raw 前置由 driver 校验（分层：driver 必须自校验）
    "start_clean_task": {"locks": True, "requires_raw": True},
    "score_task": {"locks": True},
    # 演示：不占锁，但要读原始数据
    "quick_clean_demo": {"requires_raw": True},
    # 只读：无前置，可并行
    "validate_config": {"read_only": True},
    "list_schemes": {"read_only": True},
    "list_tasks": {"read_only": True},
    "get_task_status": {"read_only": True},
    "get_task_result": {"read_only": True},
    "get_samples": {"read_only": True},
    "get_report": {"read_only": True},
}

#: v1.1 预留位（当前已空：两项已于 2026-09-30 转正）
PENDING_V11 = ()


def rule_for(tool_name):
    """取策略条目；未注册返回 None（调用方据此判「不存在的工具」）。"""
    return POLICY.get(tool_name)


def check_call(tool_name, facts):
    """准入校验：返回 (True, "") 或 (False, 拒绝原因)。LLM 循环在**执行前**调用。

    facts 语义与缺省（保守，宁拦勿放）：
      running_task_id: 缺省视为 None（无运行中任务 —— 这是查询到的真实值，
                       llm_loop 必须如实收集，不得省略）
      raw_readable:    缺省 True（quick_clean_demo 等的默认环境可读）；
                       收集到 False 才拒绝
    """
    rule = POLICY.get(tool_name)
    if rule is None:
        return False, "未注册的工具：%s（可用的工具见工具表）" % tool_name
    if rule.get("locks") and facts.get("running_task_id"):
        return False, ("已有任务在运行：%s —— 任务锁串行，等它结束或确认后再说"
                       % facts["running_task_id"])
    if rule.get("requires_raw") and facts.get("raw_readable") is False:
        return False, ("原始数据不可读（%s），无法执行 %s"
                       % (facts.get("raw_dir") or "目录未知", tool_name))
    return True, ""


def describe_policy(tool_name):
    """给 LLM/用户看的策略说明（拒绝时解释用）。"""
    rule = POLICY.get(tool_name)
    if rule is None:
        return "未注册工具"
    bits = []
    if rule.get("locks"):
        bits.append("占用任务锁（与其它写任务串行）")
    if rule.get("requires_raw"):
        bits.append("需要原始数据可读")
    if rule.get("read_only"):
        bits.append("只读")
    return "；".join(bits) if bits else "无特殊约束"