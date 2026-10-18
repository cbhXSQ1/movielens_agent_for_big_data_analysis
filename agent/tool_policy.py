# -*- coding: utf-8 -*-
"""第 5 节 Agent v2：策略层 —— 声明式策略表 + 准入校验（阶段 2）。

职责（docs/agent/llm-agent-plan.md §3）：在 LLM 循环（阶段 3）调用工具之前
拦截危险/非法调用；规则与 driver 侧同源（docs/hadoop/driver-refactor-plan.md
§7.2），driver 自身仍必须校验（不能只靠本层），本层负责**调用前拦截与解释**。

策略语义（现有 11 工具；与 tool_specs.TOOL_SPECS 一一对应）：
  * locks=True        —— 占用任务锁（与其它锁工具串行；同一时刻只能一个写任务）
  * requires_raw=True —— 前置依赖：原始数据可读
  * read_only=True    —— 只读，可并行

校验器 `check_call` 吃 facts（运行期事实，由 tool_facts.collect 收集）：
    {"running_task_id": "…"|None, "raw_readable": True/False/None, "raw_dir": "…"|None}
返回 (True, "") 或 (False, 拒绝原因)。**绝不猜测**：facts 缺字段按保守值
处理（见实现注释）。

⚠ 阶段 2 T2.2 修复（计划 §5 死点）：此前 devs 从未真正设置 raw_readable，
`requires_raw` 因此**在真实路径从不触发**。现在事实由 tool_facts 真实收集：

    raw_readable = True   已确认可读        → 放行
    raw_readable = False  已确认不可读      → 拒绝（本层拦截）
    raw_readable = None   无法确定（未知）  → 本层不拦，交由 driver/MCP 返回
                                            结构化错误 DEPENDENCY_MISSING

`check_call` 始终是**纯函数**：不碰文件系统、不碰网络、与传输方式无关；
探测放在 tool_facts 里，可注入、可离线测试。
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
    # 远端取证与自描述：只读，可并行
    "list_published_versions": {"read_only": True},
    "list_task_files": {"read_only": True},
    "read_task_file": {"read_only": True},
    "get_job_counters": {"read_only": True},
    "get_job_logs": {"read_only": True},
    "list_raw_datasets": {"read_only": True},
    "get_scheme_content": {"read_only": True},
    "describe_capabilities": {"read_only": True},
    # 上传方案：写侧，但不占任务锁、不读原始数据（登记方案文件，不影响在跑任务）
    "upload_scheme": {},
    # 删除方案：写侧，但不占任务锁、不读原始数据（删除方案文件；占用判定由远端先行把关）
    "delete_scheme": {},
    "wait_for_task": {"read_only": True},
    "get_scheduler_state": {"read_only": True},
    "health_check": {"read_only": True},
    # 取消：写侧，但不占任务锁（取消必须能穿过任务锁生效）
    "cancel_task": {},
}

#: v1.1 预留位（当前已空：两项已于 2026-09-30 转正）
PENDING_V11 = ()


def rule_for(tool_name):
    """取策略条目；未注册返回 None（调用方据此判「不存在的工具」）。"""
    return POLICY.get(tool_name)


def check_call(tool_name, facts):
    """准入校验：返回 (True, "") 或 (False, 拒绝原因)。LLM 循环在**执行前**调用。

    纯函数：只看 policy 表与传入的 facts，不碰文件系统 / 网络 / 传输。

    facts 语义与缺省：
      running_task_id: 缺省视为 None（无运行中任务）。
      raw_readable:    三态。False（已确认不可读）才拒绝；True 放行；
                       None/缺省（未知）不在本层拦截 —— 交由 driver/MCP
                       返回 DEPENDENCY_MISSING，避免用「未知」冒充「已读」。
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