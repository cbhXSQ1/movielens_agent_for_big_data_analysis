#!/usr/bin/env bash
# =============================================================================
# hadoop/scripts/reconcile.sh —— 集群对账（plan.md §7.4）
# =============================================================================
# 用法：
#   hadoop/scripts/reconcile.sh [--task-id <id>] [--raw <原始数据目录>]
#
# 做三件事：
#   1) 用**同一份原始数据**在本地跑一遍 runner，取三表 cleaned 内容哈希
#   2) 与任务目录里的集群产物逐表比对 sha256 —— 必须完全一致
#   3) 打印集群 counters 与 §7.3 黄金命中数的差异（逐条规则）
#
# 对账范围（见 decisions.md D-012）：
#   cleaned 三表        逐字节一致
#   规则命中数          逐条一致
#   隔离区明细文件      只保证条数/规则归属/行号一致（含 processed_at 时间戳，
#                      且集群侧是多个 part 文件的集合）
# =============================================================================
set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$SELF_DIR/env.sh"

TASK_ID=""
RAW="${ML_RAW_DIR}"
while [ $# -gt 0 ]; do
  case "$1" in
    --task-id) TASK_ID="$2"; shift 2 ;;
    --raw) RAW="$2"; shift 2 ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

DRIVER="$ML_REPO_ROOT/hadoop/driver/run_task.py"
PY="$PYTHON_BIN"

if [ -z "$TASK_ID" ]; then
  TASK_ID="$("$PY" "$DRIVER" tasks | "$PY" -c '
import json, sys
ts = [t for t in json.load(sys.stdin)["tasks"] if t["status"] == "succeeded"]
print(ts[0]["task_id"] if ts else "")
')"
fi
[ -n "$TASK_ID" ] || { echo "没有成功的任务可对账" >&2; exit 3; }

TASK_DIR="${ML_VAR_DIR}/tasks/${TASK_ID}"
echo "对账任务：$TASK_ID"
echo "任务目录：$TASK_DIR"
echo "原始数据：$RAW"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo
echo "== 1/3 本地 runner 跑同一份输入 =="
ML_RAW_DIR="$RAW" ML_VAR_DIR="$WORK/var" "$PY" "$DRIVER" start \
  --exec local --foreground --tag reconcile > "$WORK/local.json" 2>"$WORK/local.err"
LOCAL_TID="$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["task_id"])' "$WORK/local.json" 2>/dev/null)"
if [ -z "$LOCAL_TID" ]; then
  echo "本地 runner 失败：" >&2; tail -5 "$WORK/local.err" >&2; exit 3
fi
LOCAL_DIR="$WORK/var/tasks/$LOCAL_TID/cleaned/ml1m-clean-v1"

echo
echo "== 2/3 cleaned 三表内容哈希 =="
rc=0
for t in users movies ratings; do
  a="$LOCAL_DIR/$t.dat"; b="$TASK_DIR/cleaned/ml1m-clean-v1/$t.dat"
  if [ ! -f "$b" ]; then printf '%-8s 集群产物缺失：%s\n' "$t" "$b"; rc=1; continue; fi
  ha="$(sha256sum "$a" | cut -c1-16)"; hb="$(sha256sum "$b" | cut -c1-16)"
  if [ "$ha" = "$hb" ]; then printf '%-8s OK   local=%s cluster=%s\n' "$t" "$ha" "$hb"
  else printf '%-8s DIFF local=%s cluster=%s\n' "$t" "$ha" "$hb"; rc=1; fi
done

echo
echo "== 3/3 规则命中数 vs §7.3 黄金基线 =="
"$PY" - "$TASK_DIR" <<'PY'
import json, os, sys
d = sys.argv[1]
counts = json.load(open(os.path.join(d, "counts.json"), encoding="utf-8"))
GOLD_Q = {"P2": 6075, "P3": 7606, "R1": 6752, "R2": 43885, "R3": 3375, "R5": 12003,
          "X1": 10502, "X2": 10502, "M1": 58, "U1": 72}
GOLD_D = {"ratings": 49510, "movies": 454, "users": 726}
GOLD_F = {"P1_text": 48, "R4_ms": 13503, "M2_strip": 47, "U3_zip_plus4": 73}
GOLD_OUT = {"ratings": 1000209, "users": 6040, "movies": 3883}
bad = 0
def cmp(name, got, want):
    global bad
    if got is None:
        print("  %-24s 缺失（该趟未跑或未上报计数器）" % name); return
    ok = got == want
    bad += 0 if ok else 1
    print("  %-24s %-8s got=%s want=%s" % (name, "OK" if ok else "DIFF", got, want))
for k, v in sorted(GOLD_OUT.items()):
    cmp("output.%s" % k, counts["output"].get(k), v)
print("  -- quarantine --")
for k, v in sorted(GOLD_Q.items()):
    cmp("quarantine.%s" % k, counts["quarantine"]["by_rule"].get(k), v)
print("  -- dedupe --")
for k, v in sorted(GOLD_D.items()):
    cmp("dedupe.%s" % k, counts["dedupe"].get(k), v)
print("  -- fix --")
for k, v in sorted(GOLD_F.items()):
    cmp("fix.%s" % k, counts["fix"].get(k), v)
print()
print("规则命中对账：%s" % ("全部一致" if bad == 0 else "%d 项不一致" % bad))
sys.exit(0 if bad == 0 else 1)
PY
rc=$(( rc + $? ))

echo
if [ "$rc" -eq 0 ]; then echo ">>> reconcile.sh: PASS（三表逐字节一致 + 规则命中逐条一致）"
else echo ">>> reconcile.sh: FAILED（见上方 DIFF）" >&2; fi
exit "$rc"
