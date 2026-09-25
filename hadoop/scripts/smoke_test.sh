#!/usr/bin/env bash
# =============================================================================
# hadoop/scripts/smoke_test.sh —— Hadoop Streaming 冒烟测试（M0.5 验收第 3 项）
# =============================================================================
# 目的：在正式写清洗作业之前，确认「YARN + Streaming + Python mapper/reducer
#       + ISO-8859-1 显式编码」这条链路在本机伪分布式集群上真的能跑通。
#
# 做法：一个最小 wordcount。输入 3 行固定文本：
#         'a b a' / 'b c' / 'd c c'
#       期望词频 a=2  b=2  c=3  d=1（c 在第一处 1 次 + 第三处 2 次）
#       任一不符即退出码非 0。
#
# 显式指定 ISO-8859-1 读写，与 plan.md §4.6 的作业脚本约定保持一致，
# 提前验证「节点 locale 不影响编码」这一风险对策（plan.md §10）。
# =============================================================================
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SELF_DIR/env.sh"

WORK="$HADOOP_DATA_DIR/tmp/smoke"
HDFS_IN="$HDFS_BASE/smoke/input"
HDFS_OUT="$HDFS_BASE/smoke/output"

echo "=== [1/5] 准备 mapper/reducer（显式 ISO-8859-1）==="
rm -rf "$WORK"; mkdir -p "$WORK"

cat > "$WORK/mapper.py" <<'PY'
#!/usr/bin/env python3
import sys, io
IN  = io.TextIOWrapper(sys.stdin.buffer,  encoding='iso-8859-1', newline='\n')
OUT = io.TextIOWrapper(sys.stdout.buffer, encoding='iso-8859-1', newline='\n')
for line in IN:
    for tok in line.rstrip('\n').split():
        OUT.write(tok + "\t1\n")
OUT.flush()
PY

cat > "$WORK/reducer.py" <<'PY'
#!/usr/bin/env python3
import sys, io
IN  = io.TextIOWrapper(sys.stdin.buffer,  encoding='iso-8859-1', newline='\n')
OUT = io.TextIOWrapper(sys.stdout.buffer, encoding='iso-8859-1', newline='\n')
cur, n = None, 0
for line in IN:
    parts = line.rstrip('\n').split('\t')
    if len(parts) != 2:
        continue
    k, v = parts[0], parts[1]
    if k != cur:
        if cur is not None:
            OUT.write("%s\t%d\n" % (cur, n))
        cur, n = k, 0
    n += int(v)
if cur is not None:
    OUT.write("%s\t%d\n" % (cur, n))
OUT.flush()
PY

printf 'a b a\nb c\nd c c\n' > "$WORK/input.txt"
echo "  输入：$(tr '\n' '|' < "$WORK/input.txt")"

echo "=== [2/5] 上传输入到 HDFS ==="
hdfs dfs -rm -r -f -skipTrash "$HDFS_BASE/smoke" >/dev/null 2>&1 || true
hdfs dfs -mkdir -p "$HDFS_IN"
hdfs dfs -put -f "$WORK/input.txt" "$HDFS_IN/input.txt"
hdfs dfs -ls "$HDFS_IN"

echo "=== [3/5] 提交 Streaming 作业（1 reducer）==="
hadoop jar "$STREAMING_JAR" \
  -D mapreduce.job.name="iter1-m0.5-streaming-smoke" \
  -D mapreduce.job.reduces=1 \
  -files "$WORK/mapper.py,$WORK/reducer.py" \
  -input  "$HDFS_IN" \
  -output "$HDFS_OUT" \
  -mapper "python3 mapper.py" \
  -reducer "python3 reducer.py" \
  2>&1 | tail -5

echo "=== [4/5] 读取结果 ==="
hdfs dfs -getmerge "$HDFS_OUT" "$WORK/result.txt"
sort "$WORK/result.txt" | tee "$WORK/result.sorted.txt"

echo "=== [5/5] 校验期望词频 ==="
EXPECTED="$(printf 'a\t2\nb\t2\nc\t3\nd\t1')"
ACTUAL="$(sort "$WORK/result.txt")"
if [ "$ACTUAL" = "$EXPECTED" ]; then
  echo "SMOKE: PASS"
  echo "  （Streaming + Python + ISO-8859-1 + YARN 链路正常）"
  exit 0
else
  echo "SMOKE: FAIL" >&2
  echo "--- expected ---" >&2; printf '%s\n' "$EXPECTED" >&2
  echo "--- actual ---"   >&2; printf '%s\n' "$ACTUAL"   >&2
  exit 1
fi
