#!/usr/bin/env bash
# =============================================================================
# hadoop/scripts/export_demo_samples.sh —— 导出前端演示数据（plan.md M8）
# =============================================================================
# 用法：
#   hadoop/scripts/export_demo_samples.sh [--task-id <id>] [--out <dir>] [--n 50]
#
# 产出（默认 .demo/，已在 .gitignore 中）：
#   metrics.json      五维 + 18 指标（before/after/delta）+ counts —— 前端直接画图
#   cleaned_sample.jsonl   清洗后三表各 n 行样本
#   quarantine_sample.jsonl 隔离样例（含规则、原因），带每规则计数
#   numbers.md        汇报用的数字表
#
# 只读 CLI 的 result / samples / report，不解析任何内部实现细节。
# =============================================================================
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$SELF_DIR/env.sh"

OUT="$ML_REPO_ROOT/.demo"
N=50
TASK_ID=""
while [ $# -gt 0 ]; do
  case "$1" in
    --task-id) TASK_ID="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    --n) N="$2"; shift 2 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

DRIVER="$ML_REPO_ROOT/hadoop/driver/run_task.py"
PY="$PYTHON_BIN"
mkdir -p "$OUT"

if [ -z "$TASK_ID" ]; then
  # 取最近一个成功任务
  TASK_ID="$("$PY" "$DRIVER" tasks | "$PY" -c '
import json, sys
ts = [t for t in json.load(sys.stdin)["tasks"] if t["status"] == "succeeded"]
print(ts[0]["task_id"] if ts else "")
')"
fi
if [ -z "$TASK_ID" ]; then
  echo "没有可用的成功任务，请先跑 hadoop/scripts/demo.sh" >&2
  exit 3
fi
echo "导出任务 $TASK_ID 的演示数据到 $OUT"

"$PY" "$DRIVER" result --task-id "$TASK_ID" > "$OUT/result.json"

"$PY" - "$OUT" "$N" "$TASK_ID" "$DRIVER" <<'PY'
import json, os, subprocess, sys
out, n, tid, driver = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))

json.dump({"task_id": tid, "data_version": res["data_version"],
           "versions": res["versions"], "time_boundaries": res["time_boundaries"],
           "counts": res["counts"], "scores": res["scores"],
           "limitations": res["limitations"]},
          open(os.path.join(out, "metrics.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)

for kind, fname in (("cleaned", "cleaned_sample.jsonl"),
                    ("quarantine", "quarantine_sample.jsonl")):
    rows = []
    for table in ("ratings", "users", "movies"):
        env = json.loads(subprocess.check_output(
            [sys.executable, driver, "samples", "--task-id", tid, "--type", kind,
             "--table", table, "--n", str(n)]).decode("utf-8"))
        for s in env.get("samples", []):
            s = dict(s)
            s["table"] = table
            rows.append(s)
    with open(os.path.join(out, fname), "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("  %-24s %d 行" % (fname, len(rows)))
print("  metrics.json 已写出")
PY

# 汇报数字表
"$PY" - "$OUT" <<'PY'
import json, os, sys
out = sys.argv[1]
m = json.load(open(os.path.join(out, "metrics.json"), encoding="utf-8"))
c, s = m["counts"], m["scores"]
L = ["# 迭代一 汇报数字表", "",
     "- task_id：`%s`" % m["task_id"],
     "- data_version：`%s`" % m["data_version"],
     "- rule_version：`%s`，scoring_scheme_version：`%s`"
     % (m["versions"]["rule"]["version"], m["versions"]["scoring"]["version"]),
     "- T1 = %s，T2 = %s" % (m["time_boundaries"]["T1"], m["time_boundaries"]["T2"]),
     "", "## 数据量", "",
     "| 项 | 值 |", "|---|---|",
     "| 输入行数（评分/用户/电影） | %s / %s / %s |" % (
         c["input"]["ratings_lines"], c["input"]["users_lines"], c["input"]["movies_lines"]),
     "| 清洗后（评分/用户/电影） | %s / %s / %s |" % (
         c["output"]["ratings"], c["output"]["users"], c["output"]["movies"]),
     "| 隔离总数 | %s |" % c["quarantine"]["total"],
     "| 去重移除（评分/电影/用户） | %s / %s / %s |" % (
         c["dedupe"].get("ratings", 0), c["dedupe"].get("movies", 0), c["dedupe"].get("users", 0)),
     "", "## 隔离明细（by_rule）", "", "| 规则 | 数量 |", "|---|---|"]
for k, v in sorted(c["quarantine"]["by_rule"].items()):
    L.append("| %s | %s |" % (k, v))
L += ["", "## 修复计数", "", "| 计数器 | 数量 |", "|---|---|"]
for k, v in sorted(c["fix"].items()):
    L.append("| %s | %s |" % (k, v))
L += ["", "## 五维评分", "", "| 维度 | 清洗前 | 清洗后 | 变化 |", "|---|---|---|---|"]
for k, v in s["after"].items():
    before = s["before"].get(k, 0.0)
    L.append("| %s | %.2f | %.2f | %+.2f |" % (k, before, v, s["delta"].get(k, v - before)))
L += ["", "## 18 个指标", "", "| 指标 | 清洗前 | 清洗后 |", "|---|---|---|"]
for k in s["metrics"]["before"]:
    L.append("| %s | %.2f | %.2f |" % (k, s["metrics"]["before"][k],
                                       s["metrics"]["after"].get(k, 0.0)))
L += ["", "## 评价局限", ""] + ["- " + x for x in m["limitations"]] + [""]
open(os.path.join(out, "numbers.md"), "w", encoding="utf-8").write("\n".join(L))
print("  numbers.md 已写出")
PY

echo
echo "完成。演示数据在 $OUT"
ls -la "$OUT"
