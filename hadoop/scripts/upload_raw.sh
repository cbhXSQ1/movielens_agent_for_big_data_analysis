#!/usr/bin/env bash
# =============================================================================
# hadoop/scripts/upload_raw.sh —— 打包引擎 + 物化行号 + 上传原始三表到 HDFS
# =============================================================================
# 用法：
#   hadoop/scripts/upload_raw.sh                    # 全量上传
#   hadoop/scripts/upload_raw.sh --sample 200       # 评分表前 200 行，维表仍全量
#   hadoop/scripts/upload_raw.sh --sample-all 200   # 三表都取前 200 行
#   hadoop/scripts/upload_raw.sh --check            # 只对账已在 HDFS 上的内容
#
# 为什么默认**不**对维表抽样：X1/X2 是跨表校验，三表各自独立抽样会破坏引用完整性
# —— 抽样后的评分记录，其 UserID/MovieID 几乎必然不在抽样后的维表里，
# 于是全部被当成孤儿隔离，cleaned 评分表变成 0 行（实测踩到过）。
# 维表本身很小（users 750KB、movies 170KB），全量上传没有成本。
#
# 做三件事：
#   1) 把 engine/ + config/ 打成 engine.zip（作业用 -files 分发后 zipimport）
#   2) 把原始三表物化成 "<行号>\t<原始行>" —— 行号规则与 engine.pipeline.read_raw_table
#      完全一致（跳过空行、保留真实行号），这样集群 mapper 与本地 runner 用同一套行号
#   3) 上传到 $HDFS_BASE/raw/<data_version>/<table>.dat
#
# 为什么要物化行号：Streaming mapper 拿不到全局行号，split 边界不可知，
# 在 mapper 内自增会随 split 数漂移。见 docs/hadoop/decisions.md D-012。
#
# 重复执行安全：先删同名目标目录再上传。
# =============================================================================
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$SELF_DIR/env.sh"

SAMPLE_RATINGS=0
SAMPLE_DIMS=0
CHECK_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --sample)     SAMPLE_RATINGS="${2:?--sample 需要行数}"; shift 2 ;;
    --sample-all) SAMPLE_RATINGS="${2:?--sample-all 需要行数}"; SAMPLE_DIMS="$2"; shift 2 ;;
    --check)      CHECK_ONLY=1; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

DATA_VERSION="$("$PYTHON_BIN" - "$ML_REPO_ROOT" <<'PY'
import json, os, sys
cfg = json.load(open(os.path.join(sys.argv[1], "config", "cleaning_rules.v1.json"),
                    encoding="utf-8"))
print(cfg["data_version"]["id"])
PY
)"
HDFS_RAW="$HDFS_BASE/raw/$DATA_VERSION"
ZIP="$ML_REPO_ROOT/hadoop/engine.zip"

# ---- 1) engine.zip ----------------------------------------------------------
build_zip() {
  rm -f "$ZIP"
  "$PYTHON_BIN" - "$ML_REPO_ROOT" "$ZIP" <<'PY'
import os, sys, zipfile
root, out = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for pkg in ("engine",):
        base = os.path.join(root, "hadoop", pkg)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for fn in sorted(filenames):
                if not fn.endswith(".py"):
                    continue
                full = os.path.join(dirpath, fn)
                z.write(full, os.path.relpath(full, os.path.join(root, "hadoop")))
    for fn in sorted(os.listdir(os.path.join(root, "config"))):
        if fn.endswith(".json"):
            full = os.path.join(root, "config", fn)
            z.write(full, os.path.join("config", fn))
print("engine.zip: %d bytes" % os.path.getsize(out))
PY
}

# ---- 2) 物化行号 -------------------------------------------------------------
materialize() {
  local table="$1" dest="$2" sample="$3"
  "$PYTHON_BIN" - "$ML_RAW_DIR" "$table" "$dest" "$sample" "$ML_REPO_ROOT" <<'PY'
import io, os, sys
raw_dir, table, dest, sample, root = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5]
sys.path.insert(0, os.path.join(root, "hadoop"))
from engine.pipeline import TABLE_FILES, read_raw_table
rows = read_raw_table(os.path.join(raw_dir, TABLE_FILES[table]))
if sample:
    rows = rows[:sample]
with io.open(dest, "w", encoding="iso-8859-1", newline="\n") as fh:
    for n, raw in rows:
        fh.write(u"%d\t%s\n" % (n, raw))
print("  %-8s %7d 行 -> %s" % (table, len(rows), dest))
PY
}

if [ "$CHECK_ONLY" -eq 0 ]; then
  echo "== 1/3 打包 engine.zip =="
  build_zip
  echo
  echo "== 2/3 物化行号（ratings sample=$SAMPLE_RATINGS，维表 sample=$SAMPLE_DIMS） =="
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  materialize users   "$TMP/users.dat"   "$SAMPLE_DIMS"
  materialize movies  "$TMP/movies.dat"  "$SAMPLE_DIMS"
  materialize ratings "$TMP/ratings.dat" "$SAMPLE_RATINGS"
  echo
  echo "== 3/3 上传到 $HDFS_RAW =="
  hdfs dfs -rm -r -f "$HDFS_RAW" >/dev/null 2>&1 || true
  hdfs dfs -mkdir -p "$HDFS_RAW"
  for t in users movies ratings; do
    hdfs dfs -put -f "$TMP/$t.dat" "$HDFS_RAW/$t.dat"
  done
fi

echo
echo "== HDFS 内容 =="
hdfs dfs -ls "$HDFS_RAW"
echo
echo "engine.zip: $ZIP"
echo "HDFS_RAW  : $HDFS_RAW"
