#!/usr/bin/env bash
# =============================================================================
# hadoop/scripts/run_tests.sh —— 迭代一 Hadoop 侧统一测试入口
# =============================================================================
# 用法：
#   hadoop/scripts/run_tests.sh              # 校验配置 + 跑全部单元/集成测试
#   hadoop/scripts/run_tests.sh -k golden    # 额外参数透传给 unittest
#
# 执行内容（plan.md M0）：
#   1) python3 hadoop/tools/validate_configs.py        —— 配置校验必须 PASS
#   2) python3 -m unittest discover -s hadoop/tests -v —— 全部测试必须通过
#
# 退出码：0 = 全部通过；非 0 = 失败（可直接用于里程碑门禁）
#
# 说明：把 <repo>/hadoop 放进 PYTHONPATH，测试里即可 `from engine import ...`、
#       `from jobs import ...`，与 Streaming 作业内部的 sys.path 约定一致。
# =============================================================================
set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SELF_DIR/../.." && pwd)"
export PYTHONPATH="$REPO_ROOT/hadoop${PYTHONPATH:+:$PYTHONPATH}"

PY="${PYTHON:-python3}"
rc=0

echo "=============================================================="
echo " 1/2  配置校验 (hadoop/tools/validate_configs.py)"
echo "=============================================================="
if ! "$PY" "$REPO_ROOT/hadoop/tools/validate_configs.py"; then
  echo ">>> 配置校验未通过，终止测试" >&2
  exit 2
fi

echo
echo "=============================================================="
echo " 2/2  单元 + 集成测试 (unittest discover -s hadoop/tests)"
echo "=============================================================="
"$PY" -m unittest discover -s "$REPO_ROOT/hadoop/tests" -t "$REPO_ROOT/hadoop/tests" -v "$@"
rc=$?

echo
if [ "$rc" -eq 0 ]; then
  echo ">>> run_tests.sh: ALL PASS"
else
  echo ">>> run_tests.sh: FAILED (exit=$rc)" >&2
fi
exit "$rc"
