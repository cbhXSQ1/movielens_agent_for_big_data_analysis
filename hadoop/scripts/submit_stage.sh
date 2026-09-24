#!/usr/bin/env bash
# =============================================================================
# hadoop/scripts/submit_stage.sh —— 通用的单趟 Streaming 提交封装
# =============================================================================
# 用法：
#   submit_stage.sh <job.py> <输入> <输出> [选项]
#
# 选项：
#   --reduce N         reducer 数；0 = map-only（默认 0）
#   --mapper-args "…"  追加给 mapper 的参数（如 "--mode keep"）
#   --reducer-args "…" 追加给 reducer 的参数
#   --job-name NAME    作业名（默认按脚本与模式拼）
#   --files "a,b"      额外用 -files 分发的本地文件（engine.zip 永远会带上）
#   -D key=value       追加任意 Hadoop 配置（可重复）
#
# 约定：
#   * engine.zip 与两份配置经 -files 分发，作业内 sys.path.insert(0,"engine.zip")
#     即可 zipimport —— 见 jobs/_common.py 的 _ensure_engine_importable
#   * 配置以**本地文件名**出现（cleaning_rules.v1.json），作业直接按名读取
#   * 输出目录存在时先删除（Hadoop 拒绝覆盖）
#
# 这是 plan §5.3 的落地版本；M5 的 driver 会复用它，避免再写一套提交逻辑。
# =============================================================================
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$SELF_DIR/env.sh"

[ $# -ge 3 ] || { sed -n '2,24p' "$0"; exit 2; }

JOB="$1"; INPUT="$2"; OUTPUT="$3"; shift 3
REDUCES=0
MAPPER_ARGS=""
REDUCER_ARGS=""
JOB_NAME=""
EXTRA_FILES=""
EXTRA_D=()

while [ $# -gt 0 ]; do
  case "$1" in
    --reduce)       REDUCES="${2:?}"; shift 2 ;;
    --mapper-args)  MAPPER_ARGS="${2:-}"; shift 2 ;;
    --reducer-args) REDUCER_ARGS="${2:-}"; shift 2 ;;
    --job-name)     JOB_NAME="${2:?}"; shift 2 ;;
    --files)        EXTRA_FILES="${2:-}"; shift 2 ;;
    -D)             EXTRA_D+=("-D" "$2"); shift 2 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

ZIP="$ML_REPO_ROOT/hadoop/engine.zip"
[ -f "$ZIP" ] || { echo "缺少 $ZIP，请先跑 upload_raw.sh" >&2; exit 2; }

JOB_PATH="$ML_REPO_ROOT/hadoop/jobs/$JOB"
[ -f "$JOB_PATH" ] || { echo "找不到作业脚本 $JOB_PATH" >&2; exit 2; }

RULES_NAME="cleaning_rules.v1.json"
SCORING_NAME="scoring_scheme.v1.json"
COMMON_ARGS="--rules $RULES_NAME --scoring $SCORING_NAME --task-id ${ML_TASK_ID:-T-LOCAL}"

JOB_NAME="${JOB_NAME:-iter1-$(basename "$JOB" .py)-$(echo "${MAPPER_ARGS:-x}" | tr -d ' -')}"
# 作业脚本本身与 _common.py 也必须分发：Streaming 只在任务工作目录里执行命令，
# 不会自动带上 mapper 命令里引用的文件（plan §5.3 的 -files 正是为此）。
COMMON_PY="$ML_REPO_ROOT/hadoop/jobs/_common.py"
FILES="$ZIP,$ML_REPO_ROOT/config/$RULES_NAME,$ML_REPO_ROOT/config/$SCORING_NAME,$JOB_PATH,$COMMON_PY"
[ -n "$EXTRA_FILES" ] && FILES="$FILES,$EXTRA_FILES"

hdfs dfs -rm -r -f "$OUTPUT" >/dev/null 2>&1 || true

echo ">>> $JOB_NAME"
echo "    mapper  : $JOB ${MAPPER_ARGS:-} $COMMON_ARGS"
if [ "$REDUCES" -gt 0 ]; then
  echo "    reducer : $JOB --reduce ${REDUCER_ARGS:-} $COMMON_ARGS"
else
  echo "    reducer : (map-only)"
fi
echo "    in/out  : $INPUT -> $OUTPUT"

# reduce 趟的输出分隔符必须显式置空：
# Streaming 用 TextOutputFormat 写 reduce 结果，它会写成 `key + sep + value`，
# 并按 stream.reduce.output.field.separator（默认 TAB）把 reducer 的输出行拆成 key/value。
# reducer 只吐一行纯内容（没有 TAB）时，整行成了 key、value 为空，
# 于是被补上一个尾随 TAB —— cleaned 表就多了一列。
# map-only 趟不能这么设：那里 reducer 位是 `cat`，输出本来就是 `key\tvalue`，
# 置空会把键值粘在一起。
SEP_D=()
if [ "$REDUCES" -gt 0 ]; then
  SEP_D=(-D mapreduce.output.textoutputformat.separator= -D mapred.textoutputformat.separator=)
fi

set -x
hadoop jar "$STREAMING_JAR" \
  -D mapreduce.job.name="$JOB_NAME" \
  -D mapreduce.job.reduces="$REDUCES" \
  -D mapreduce.map.speculative=false \
  -D mapreduce.reduce.speculative=false \
  "${SEP_D[@]+"${SEP_D[@]}"}" \
  "${EXTRA_D[@]+"${EXTRA_D[@]}"}" \
  -files "$FILES" \
  -input "$INPUT" \
  -output "$OUTPUT" \
  -mapper "$PYTHON_BIN $JOB ${MAPPER_ARGS:-} $COMMON_ARGS" \
  -reducer "$([ "$REDUCES" -gt 0 ] && echo "$PYTHON_BIN $JOB --reduce ${REDUCER_ARGS:-} $COMMON_ARGS" || echo cat)"
set +x
echo "<<< $JOB_NAME 完成"
