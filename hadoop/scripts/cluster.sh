#!/usr/bin/env bash
# =============================================================================
# hadoop/scripts/cluster.sh —— 伪分布式集群启停（不依赖 SSH）
# =============================================================================
# 用法：
#   hadoop/scripts/cluster.sh start | stop | restart | status
#
# 为什么不用 start-dfs.sh / start-yarn.sh：
#   本机未配置到 localhost 的免密 SSH（~/.ssh/authorized_keys 为空，且该文件在
#   工作区之外，写入需要额外提权）。而 Hadoop 的 sbin/start-*.sh 会通过
#   `hdfs --workers --daemon start ...` 对 workers 列表里的每个主机执行 ssh
#   （见 libexec/hadoop-functions.sh 的 hadoop_actual_ssh / hadoop_connect_to_hosts_without_pdsh，
#   其中没有 localhost 免 ssh 的捷径），因此必然失败。
#
#   本脚本改为在**本机就地**启动同样的 5 个守护进程：
#     hdfs --daemon start namenode|datanode|secondarynamenode
#     yarn --daemon start resourcemanager|nodemanager
#   进程集合、端口、数据目录与 start-dfs.sh + start-yarn.sh 完全一致。
#   记录见 docs/hadoop/decisions.md D-005。
# =============================================================================
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=hadoop/scripts/env.sh
source "$SELF_DIR/env.sh"

HDFS_DAEMONS=(namenode datanode secondarynamenode)
YARN_DAEMONS=(resourcemanager nodemanager)
ALL_DAEMONS=("${HDFS_DAEMONS[@]}" "${YARN_DAEMONS[@]}")

# 守护进程 -> jps 里显示的主类名（用于 status 判定）
jps_name() {
  case "$1" in
    namenode)           echo "NameNode" ;;
    datanode)           echo "DataNode" ;;
    secondarynamenode)  echo "SecondaryNameNode" ;;
    resourcemanager)    echo "ResourceManager" ;;
    nodemanager)        echo "NodeManager" ;;
  esac
}

do_start() {
  for d in "${HDFS_DAEMONS[@]}"; do
    if jps | grep -q "$(jps_name "$d")"; then
      echo "  [skip] $d 已在运行"
    else
      echo "  [start] $d"
      hdfs --daemon start "$d" 2>&1 | grep -v "^WARNING" || true
    fi
  done
  for d in "${YARN_DAEMONS[@]}"; do
    if jps | grep -q "$(jps_name "$d")"; then
      echo "  [skip] $d 已在运行"
    else
      echo "  [start] $d"
      yarn --daemon start "$d" 2>&1 | grep -v "^WARNING" || true
    fi
  done
}

wait_ready() {
  # 等 NameNode 退出安全模式：hdfs 可用后依赖它的作业才能提交
  local i
  for i in $(seq 1 60); do
    if hdfs dfsadmin -safemode get 2>/dev/null | grep -q "Safe mode is OFF"; then
      return 0
    fi
    sleep 2
  done
  echo "WARN: 等待 NameNode 退出安全模式超时（120s）" >&2
  return 1
}

do_stop() {
  for d in "${YARN_DAEMONS[@]}"; do
    echo "  [stop] $d"; yarn --daemon stop "$d" >/dev/null 2>&1 || true
  done
  for d in "${HDFS_DAEMONS[@]}"; do
    echo "  [stop] $d"; hdfs --daemon stop "$d" >/dev/null 2>&1 || true
  done
}

do_status() {
  local running=0 missing=0
  echo "jps:"
  jps | sed 's/^/  /'
  echo "---"
  for d in "${ALL_DAEMONS[@]}"; do
    if jps | grep -q "$(jps_name "$d")"; then
      printf '  %-20s UP\n' "$(jps_name "$d")"; running=$((running+1))
    else
      printf '  %-20s DOWN\n' "$(jps_name "$d")"; missing=$((missing+1))
    fi
  done
  echo "---"
  echo "running=$running/5"
  [ "$missing" = 0 ]
}

case "${1:-}" in
  start)
    do_start
    echo "等待集群就绪…"
    wait_ready && echo "集群就绪"
    echo "---"; do_status
    ;;
  stop)    do_stop; echo "已停止"; do_status || true ;;
  restart) do_stop; sleep 3; do_start; wait_ready && echo "集群就绪"; echo "---"; do_status ;;
  status)  do_status ;;
  *) sed -n '2,10p' "${BASH_SOURCE[0]}"; exit 2 ;;
esac
