#!/usr/bin/env bash
# =============================================================================
# hadoop/scripts/env.sh —— 迭代一 Hadoop 侧统一环境变量
# =============================================================================
# 用法（必须 source，不能直接执行）：
#     source hadoop/scripts/env.sh
#
# 设计说明（对应 docs/hadoop/decisions.md D-001 / D-002 / D-003）：
#   * 本机无 root：sudo 被 NoNewPrivs 强制禁用，apt 不可用，/opt 不可写。
#     因此 Java 11 与 Hadoop 3.3.6 采用**仓库内自包含用户态安装**（`.vendor/`，已 gitignore）。
#   * 原始数据实际位于 /home/ubuntu/movielensdata/raw/ml-1m/ml-1m（见 D-001），
#     由 ML_RAW_DIR 集中配置，任何脚本都不许硬编码数据路径。
#   * 所有路径均可由外部环境变量覆盖，便于换机器 / CI / 集群。
# =============================================================================

# ---- 仓库根目录：优先外部传入，否则由本文件位置推断（hadoop/scripts/../..）----
if [ -z "${ML_REPO_ROOT:-}" ]; then
  _ml_env_self="${BASH_SOURCE[0]}"
  ML_REPO_ROOT="$(cd "$(dirname "$_ml_env_self")/../.." && pwd)"
  unset _ml_env_self
fi
export ML_REPO_ROOT

# ---- 1. 用户态工具链（decisions.md D-002 / D-003）----
export JAVA_HOME="${JAVA_HOME:-$ML_REPO_ROOT/.vendor/jdk-11}"
export HADOOP_HOME="${HADOOP_HOME:-$ML_REPO_ROOT/.vendor/hadoop-3.3.6}"
export HADOOP_CONF_DIR="${HADOOP_CONF_DIR:-$ML_REPO_ROOT/hadoop/conf}"

# ---- 2. Hadoop Streaming jar（plan.md §1 验收第 4 项要求记录此路径）----
export STREAMING_JAR="${STREAMING_JAR:-$HADOOP_HOME/share/hadoop/tools/lib/hadoop-streaming-3.3.6.jar}"

# ---- 3. Hadoop 运行时目录（全部落在仓库内，gitignore 忽略）----
export HADOOP_DATA_DIR="${HADOOP_DATA_DIR:-$ML_REPO_ROOT/.hadoop-data}"
export HADOOP_LOG_DIR="${HADOOP_LOG_DIR:-$ML_REPO_ROOT/.hadoop-logs}"
export YARN_LOG_DIR="${YARN_LOG_DIR:-$HADOOP_LOG_DIR}"
export HADOOP_PID_DIR="${HADOOP_PID_DIR:-$HADOOP_DATA_DIR/pids}"

# ---- 4. 数据与产物位置 ----
# ML_RAW_DIR：原始 ml-1m 三表所在目录（decisions.md D-001）
export ML_RAW_DIR="${ML_RAW_DIR:-/home/ubuntu/movielensdata/raw/ml-1m/ml-1m}"
# HDFS_BASE：HDFS 上的任务根目录（agent-interface.md §1）
export HDFS_BASE="${HDFS_BASE:-/data}"
# ML_VAR_DIR：driver 任务产物目录（agent-interface.md §1，默认 <repo>/var）
export ML_VAR_DIR="${ML_VAR_DIR:-$ML_REPO_ROOT/var}"

# ---- 5. 作业内存（伪分布式单机，见 hadoop/conf/yarn-site.xml）----
export HADOOP_HEAPSIZE="${HADOOP_HEAPSIZE:-512}"
export YARN_HEAPSIZE="${YARN_HEAPSIZE:-512}"

# ---- 6. PATH ----
case ":$PATH:" in
  *":$HADOOP_HOME/bin:"*) ;;
  *) PATH="$HADOOP_HOME/bin:$HADOOP_HOME/sbin:$JAVA_HOME/bin:$PATH" ;;
esac
export PATH

# 让 Hadoop 自己的脚本能找到配置与 JDK
export HADOOP_MAPRED_HOME="${HADOOP_MAPRED_HOME:-$HADOOP_HOME}"
export HADOOP_COMMON_HOME="${HADOOP_COMMON_HOME:-$HADOOP_HOME}"
export HADOOP_HDFS_HOME="${HADOOP_HDFS_HOME:-$HADOOP_HOME}"
export HADOOP_YARN_HOME="${HADOOP_YARN_HOME:-$HADOOP_HOME}"

# ---- 7. 友好入口：ml_env_check ----
ml_env_check() {
  local ok=0
  echo "ML_REPO_ROOT    = $ML_REPO_ROOT"
  echo "JAVA_HOME       = $JAVA_HOME"
  echo "HADOOP_HOME     = $HADOOP_HOME"
  echo "HADOOP_CONF_DIR = $HADOOP_CONF_DIR"
  echo "STREAMING_JAR   = $STREAMING_JAR"
  echo "HADOOP_DATA_DIR = $HADOOP_DATA_DIR"
  echo "ML_RAW_DIR      = $ML_RAW_DIR"
  echo "HDFS_BASE       = $HDFS_BASE"
  echo "ML_VAR_DIR      = $ML_VAR_DIR"
  echo "---"
  if [ -x "$JAVA_HOME/bin/java" ]; then
    echo "java            : $("$JAVA_HOME/bin/java" -version 2>&1 | head -1)"
  else
    echo "java            : MISSING ($JAVA_HOME/bin/java)"; ok=1
  fi
  if [ -x "$HADOOP_HOME/bin/hadoop" ]; then
    echo "hadoop          : $("$HADOOP_HOME/bin/hadoop" version 2>/dev/null | head -1)"
  else
    echo "hadoop          : MISSING ($HADOOP_HOME/bin/hadoop)"; ok=1
  fi
  if [ -f "$STREAMING_JAR" ]; then
    echo "streaming jar   : OK"
  else
    echo "streaming jar   : MISSING ($STREAMING_JAR)"; ok=1
  fi
  echo "---"
  if [ -d "$ML_RAW_DIR" ]; then
    echo "raw data        : OK ($(wc -l < "$ML_RAW_DIR/ratings.dat" 2>/dev/null || echo '?') ratings lines)"
  else
    echo "raw data        : MISSING ($ML_RAW_DIR)"; ok=1
  fi
  return $ok
}
export -f ml_env_check 2>/dev/null || true
