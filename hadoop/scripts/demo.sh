#!/usr/bin/env bash
# =============================================================================
# hadoop/scripts/demo.sh —— 一键演示（plan.md M8）
# =============================================================================
# 用法：
#   hadoop/scripts/demo.sh                 # local 后端，全量数据（约 1.5 分钟）
#   hadoop/scripts/demo.sh --cluster       # 走真实 Streaming（约 5–10 分钟）
#   hadoop/scripts/demo.sh --fixture       # 用 60 行小样本，几秒钟出结果
#   hadoop/scripts/demo.sh --task-id <id>  # 复用已有任务，只重放查询步骤
#
# 演示的是一条**完整 Agent 交互**（接口文档 §8）：
#   validate → schemes → start → status → result → samples → report
# 每个子命令的 JSON 信封都打印出来，便于现场对着契约讲。
#
# 只用 CLI，不碰任何内部文件 —— 与 Agent 侧看到的东西完全一致。
# =============================================================================
set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$SELF_DIR/env.sh"

MODE=local
RAW_OVERRIDE=""
TASK_ID=""
while [ $# -gt 0 ]; do
  case "$1" in
    --cluster) MODE=cluster; shift ;;
    --fixture) RAW_OVERRIDE="$ML_REPO_ROOT/hadoop/tests/fixtures/raw"; shift ;;
    --task-id) TASK_ID="$2"; shift 2 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

DRIVER="$ML_REPO_ROOT/hadoop/driver/run_task.py"
PY="$PYTHON_BIN"

step() { printf '\n\033[1m=== %s ===\033[0m\n' "$1"; }
call() { "$PY" "$DRIVER" "$@"; }

if [ -n "$RAW_OVERRIDE" ]; then
  export ML_RAW_DIR="$RAW_OVERRIDE"
  echo "使用 fixture 数据：$ML_RAW_DIR"
fi

step "1/7 validate —— 配置校验"
call validate --rules config/cleaning_rules.v1.json \
              --scoring config/scoring_scheme.v1.json || exit 2

step "2/7 schemes —— 已登记方案"
call schemes

if [ -z "$TASK_ID" ]; then
  step "3/7 start —— 发起任务（$MODE 后端）"
  START_JSON="$(call start --exec "$MODE" --foreground --tag demo --force)"
  echo "$START_JSON"
  TASK_ID="$(printf '%s' "$START_JSON" | "$PY" -c 'import json,sys; print(json.load(sys.stdin).get("task_id",""))')"
  if [ -z "$TASK_ID" ]; then
    echo "任务未成功启动（见上方信封）；后续步骤跳过" >&2
    exit 3
  fi
else
  step "3/7 start —— 复用已有任务 $TASK_ID"
fi

step "4/7 status —— 执行情况"
call status --task-id "$TASK_ID"

step "5/7 result —— 五维对比 / 数据量变化 / 隔离统计"
call result --task-id "$TASK_ID"

step "6/7 samples —— 隔离样例（用户追问「给我看异常记录」）"
call samples --task-id "$TASK_ID" --type quarantine --table ratings --n 3
call samples --task-id "$TASK_ID" --type cleaned --table movies --n 3

step "7/7 report —— 评估报告（markdown）"
call report --task-id "$TASK_ID" --format md | "$PY" -c '
import json, sys
print(json.load(sys.stdin)["report"])
'

printf '\n\033[1m演示完成。\033[0m task_id=%s\n' "$TASK_ID"
printf '产物目录：%s\n' "$(call status --task-id "$TASK_ID" >/dev/null 2>&1; echo "${ML_VAR_DIR}/tasks/${TASK_ID}")"
