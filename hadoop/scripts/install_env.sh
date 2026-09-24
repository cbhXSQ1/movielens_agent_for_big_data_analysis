#!/usr/bin/env bash
# =============================================================================
# hadoop/scripts/install_env.sh —— 迭代一 M0.5 环境搭建（幂等、可重复执行）
# =============================================================================
# 从裸 VM 到可运行伪分布式 Hadoop 的全过程，供 M6 runbook「照做可复现」使用。
#
# 用法：
#   hadoop/scripts/install_env.sh              # 渲染配置 + 建运行时目录
#   hadoop/scripts/install_env.sh --extract    # 额外从 .cache-dist/ 解压工具链
#   hadoop/scripts/install_env.sh --format     # 额外格式化 HDFS NameNode
#   hadoop/scripts/install_env.sh --start      # 额外启动 HDFS + YARN
#   hadoop/scripts/install_env.sh --stop       # 停止 HDFS + YARN
#   hadoop/scripts/install_env.sh --extract --format --start   # 一步到位
#
# 为何不是「apt install openjdk-11-jdk + 装到 /opt/hadoop」：见 docs/hadoop/decisions.md
# D-002 —— 本机 sudo 被 NoNewPrivs 强制禁用、/opt 不可写，故采用仓库内用户态安装（用户已确认 A 方案）。
# =============================================================================
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=hadoop/scripts/env.sh
source "$SELF_DIR/env.sh"

DO_EXTRACT=0; DO_FORMAT=0; DO_START=0; DO_STOP=0
for a in "$@"; do
  case "$a" in
    --extract) DO_EXTRACT=1 ;;
    --format)  DO_FORMAT=1 ;;
    --start)   DO_START=1 ;;
    --stop)    DO_STOP=1 ;;
    -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

log() { printf '[install_env] %s\n' "$*"; }
die() { printf '[install_env] ERROR: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 1. 工具链解压（从 .cache-dist/ 的官方压缩包）
# ---------------------------------------------------------------------------
if [ "$DO_EXTRACT" = 1 ]; then
  CACHE="$ML_REPO_ROOT/.cache-dist"
  [ -f "$CACHE/hadoop-3.3.6.tar.gz" ] || die "缺少 $CACHE/hadoop-3.3.6.tar.gz（下载：https://archive.apache.org/dist/hadoop/common/hadoop-3.3.6/）"
  [ -f "$CACHE/jdk11.tar.gz" ]         || die "缺少 $CACHE/jdk11.tar.gz（Temurin JDK 11，见 docs/hadoop/decisions.md D-003）"
  mkdir -p "$ML_REPO_ROOT/.vendor"
  if [ ! -x "$JAVA_HOME/bin/java" ]; then
    log "解压 JDK 11 -> $JAVA_HOME"
    tar xzf "$CACHE/jdk11.tar.gz" -C "$ML_REPO_ROOT/.vendor"
    _jdk_dir="$(find "$ML_REPO_ROOT/.vendor" -maxdepth 1 -type d -name 'jdk-11*' ! -name 'jdk-11' | head -1)"
    [ -n "$_jdk_dir" ] && mv "$_jdk_dir" "$JAVA_HOME"
  else
    log "JDK 11 已存在，跳过"
  fi
  if [ ! -x "$HADOOP_HOME/bin/hadoop" ]; then
    log "解压 Hadoop 3.3.6 -> $HADOOP_HOME"
    tar xzf "$CACHE/hadoop-3.3.6.tar.gz" -C "$ML_REPO_ROOT/.vendor"
  else
    log "Hadoop 3.3.6 已存在，跳过"
  fi
fi

# ---------------------------------------------------------------------------
# 2. 前置校验
# ---------------------------------------------------------------------------
[ -x "$JAVA_HOME/bin/java" ]     || die "JAVA_HOME 无效：$JAVA_HOME（先跑 --extract）"
[ -x "$HADOOP_HOME/bin/hadoop" ] || die "HADOOP_HOME 无效：$HADOOP_HOME（先跑 --extract）"
log "java: $("$JAVA_HOME/bin/java" -version 2>&1 | head -1)"

# ---------------------------------------------------------------------------
# 3. 渲染 4 个 XML（模板 -> hadoop/conf/*.xml）+ 生成 hadoop-env.sh
# ---------------------------------------------------------------------------
log "渲染站点配置 -> $HADOOP_CONF_DIR"
mkdir -p "$HADOOP_CONF_DIR"
for t in "$HADOOP_CONF_DIR"/*.xml.tmpl; do
  [ -e "$t" ] || die "找不到配置模板（$HADOOP_CONF_DIR/*.xml.tmpl）"
  out="${t%.tmpl}"
  sed -e "s|@HADOOP_DATA_DIR@|$HADOOP_DATA_DIR|g" \
      -e "s|@HADOOP_HOME@|$HADOOP_HOME|g" \
      "$t" > "$out"
  log "  $(basename "$out")"
done

# hadoop-env.sh：显式钉住 JAVA_HOME，避免守护进程被启动脚本猜错 JDK
cat > "$HADOOP_CONF_DIR/hadoop-env.sh" <<EOF
# 由 hadoop/scripts/install_env.sh 自动生成，请勿手改；改模板或 env.sh 后重跑。
# 显式指定 Java 11（系统默认 java 为 21，Hadoop 3.3.6 官方支持 8/11；见 decisions.md D-003）
export JAVA_HOME=$JAVA_HOME
export HADOOP_HOME=$HADOOP_HOME
export HADOOP_CONF_DIR=$HADOOP_CONF_DIR
export HADOOP_LOG_DIR=$HADOOP_LOG_DIR
export HADOOP_PID_DIR=$HADOOP_PID_DIR
export HADOOP_HEAPSIZE=$HADOOP_HEAPSIZE
export YARN_HEAPSIZE=$YARN_HEAPSIZE
# 伪分布式单机：限制守护进程堆，避免 8GB VM 内存吃紧
export HDFS_NAMENODE_OPTS="-Xmx1024m -XX:+UseParallelGC"
export HDFS_DATANODE_OPTS="-Xmx512m -XX:+UseParallelGC"
export HDFS_SECONDARYNAMENODE_OPTS="-Xmx512m -XX:+UseParallelGC"
export YARN_RESOURCEMANAGER_OPTS="-Xmx768m -XX:+UseParallelGC"
export YARN_NODEMANAGER_OPTS="-Xmx768m -XX:+UseParallelGC"
EOF
log "  hadoop-env.sh"

# 播种上游默认配置（白名单，见下）。
# 为什么必须做：HADOOP_CONF_DIR 一旦指向自定义目录，该目录就**整体取代**了
# $HADOOP_HOME/etc/hadoop 在 classpath 上的位置，上游默认值不再可见，会直接导致：
#   * ResourceManager 启动失败：Queue configuration missing child queue names for root
#     （缺 capacity-scheduler.xml）
#   * 各守护进程告警：log4j.properties is not found
# 为什么用白名单而不是整目录 cp：上游的 yarn-env.sh / mapred-env.sh / hadoop-env.sh
# 反过来会覆盖本仓库在 hadoop-env.sh 里钉死的 JAVA_HOME 与堆参数，必须排除。
UPSTREAM_CONF="$HADOOP_HOME/etc/hadoop"
SEED_ALLOWLIST="capacity-scheduler.xml log4j.properties configuration.xsl hadoop-policy.xml"
if [ -d "$UPSTREAM_CONF" ]; then
  seeded=""
  for _b in $SEED_ALLOWLIST; do
    if [ -f "$UPSTREAM_CONF/$_b" ] && [ ! -e "$HADOOP_CONF_DIR/$_b" ]; then
      cp "$UPSTREAM_CONF/$_b" "$HADOOP_CONF_DIR/$_b"
      seeded="$seeded $_b"
    fi
  done
  log "  播种上游默认配置:${seeded:- （均已存在）}"
else
  log "  WARN: 找不到上游默认配置目录 $UPSTREAM_CONF"
fi

# ---------------------------------------------------------------------------
# 4. 建运行时目录
# ---------------------------------------------------------------------------
mkdir -p "$HADOOP_DATA_DIR"/{name,data,tmp,pids} \
         "$HADOOP_DATA_DIR"/nodemanager/{local,log} \
         "$HADOOP_LOG_DIR" "$ML_VAR_DIR"
log "运行时目录就绪：$HADOOP_DATA_DIR"

# ---------------------------------------------------------------------------
# 5. 可选：格式化 NameNode
# ---------------------------------------------------------------------------
if [ "$DO_FORMAT" = 1 ]; then
  if [ -f "$HADOOP_DATA_DIR/name/current/VERSION" ]; then
    log "NameNode 已格式化，跳过（如需重置请先删除 $HADOOP_DATA_DIR/name）"
  else
    log "格式化 HDFS NameNode"
    "$HADOOP_HOME/bin/hdfs" namenode -format -force -nonInteractive >"$HADOOP_LOG_DIR/format.log" 2>&1 \
      || { tail -30 "$HADOOP_LOG_DIR/format.log" >&2; die "namenode -format 失败，详见 $HADOOP_LOG_DIR/format.log"; }
    log "格式化完成（日志 $HADOOP_LOG_DIR/format.log）"
    grep -E "Storage directory .* has been successfully formatted|successfully formatted" "$HADOOP_LOG_DIR/format.log" | head -2 || true
  fi
fi

# ---------------------------------------------------------------------------
# 6. 可选：启停集群
# ---------------------------------------------------------------------------
if [ "$DO_START" = 1 ]; then
  log "启动 HDFS + YARN"
  "$HADOOP_HOME/sbin/start-dfs.sh"   >"$HADOOP_LOG_DIR/start-dfs.log"  2>&1 || { tail -30 "$HADOOP_LOG_DIR/start-dfs.log" >&2; die "start-dfs.sh 失败"; }
  "$HADOOP_HOME/sbin/start-yarn.sh"  >"$HADOOP_LOG_DIR/start-yarn.log" 2>&1 || { tail -30 "$HADOOP_LOG_DIR/start-yarn.log" >&2; die "start-yarn.sh 失败"; }
  sleep 3
  log "jps 输出："
  "$JAVA_HOME/bin/jps" | sed 's/^/    /'
fi

if [ "$DO_STOP" = 1 ]; then
  log "停止 YARN + HDFS"
  "$HADOOP_HOME/sbin/stop-yarn.sh" >/dev/null 2>&1 || true
  "$HADOOP_HOME/sbin/stop-dfs.sh"  >/dev/null 2>&1 || true
fi

log "完成。source hadoop/scripts/env.sh && ml_env_check 可查看环境。"
