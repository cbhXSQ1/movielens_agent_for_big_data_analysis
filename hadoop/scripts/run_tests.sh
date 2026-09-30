#!/usr/bin/env bash
# =============================================================================
# hadoop/scripts/run_tests.sh —— 全仓库统一测试入口（Hadoop 侧 + Agent 侧）
# =============================================================================
# 用法：
#   hadoop/scripts/run_tests.sh              # 校验配置 + 跑全部单元/集成测试
#   hadoop/scripts/run_tests.sh -k golden    # 额外参数透传给 unittest
#
# 执行内容（plan.md M0）：
#   1) python3 hadoop/tools/validate_configs.py        —— 配置校验必须 PASS
#   2) python3 -m unittest discover -s hadoop/tests -v —— driver/engine/hadoop 全部测试
#   3) python3 -m unittest discover -s agent/tests -v  —— Agent v2 新模块测试（阶段 1 起）
#
# 退出码：0 = 全部通过；非 0 = 失败（可直接用于里程碑门禁）
#
# 说明：把 <repo>/hadoop 放进 PYTHONPATH，测试里即可 `from engine import ...`、
#       `from jobs import ...`，与 Streaming 作业内部的 sys.path 约定一致。
#       agent/tests 从仓库根跑，测试内自行 `sys.path.insert(0, REPO_ROOT)`。
# =============================================================================
set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SELF_DIR/../.." && pwd)"
export PYTHONPATH="$REPO_ROOT/hadoop${PYTHONPATH:+:$PYTHONPATH}"

PY="${PYTHON:-python3}"
rc=0

echo "=============================================================="
echo " 1/3  配置校验 (hadoop/tools/validate_configs.py)"
echo "=============================================================="
if ! "$PY" "$REPO_ROOT/hadoop/tools/validate_configs.py"; then
  echo ">>> 配置校验未通过，终止测试" >&2
  exit 2
fi

echo
echo "=============================================================="
echo " 2/3  单元 + 集成测试 (unittest discover -s hadoop/tests)"
echo "=============================================================="
"$PY" -m unittest discover -s "$REPO_ROOT/hadoop/tests" -t "$REPO_ROOT/hadoop/tests" -v "$@"
rc=$?

if [ "$rc" -eq 0 ]; then
  echo
  echo "=============================================================="
  echo " 3/3  Agent 侧测试 (unittest discover -s agent/tests)"
  echo "=============================================================="
  "$PY" -m unittest discover -s "$REPO_ROOT/agent/tests" -t "$REPO_ROOT/agent/tests" "$@"
  rc=$?
fi

echo
if [ "$rc" -eq 0 ]; then
  echo ">>> run_tests.sh: ALL PASS"
else
  echo ">>> run_tests.sh: FAILED (exit=$rc)" >&2
fi
exit "$rc"
