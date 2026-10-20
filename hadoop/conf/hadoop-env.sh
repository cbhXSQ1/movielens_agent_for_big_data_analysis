# 由 hadoop/scripts/install_env.sh 生成，请勿手改；改模板或 env.sh 后重跑。
# 自定位 + 环境变量优先（清单⑫ 可移植性）：本文件不写死任何绝对路径，
# 仓库位置由自身路径推断，工具链优先用外部 export 的值 —— 换机器（JDK/Hadoop
# 装在 /opt、conf 指向系统目录）不必改本文件，跑之前 export 即可。
#
# 唯一例外是 JAVA_HOME：仓库内 .vendor/jdk-11 **存在时优先用它**。原因见
# decisions.md D-003 —— 系统默认 java 常为 21，而 Hadoop 3.3.6 官方只支持
# Java 8/11；没有 .vendor 的机器才尊重外部 export 的 JAVA_HOME。
_ml_conf_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_ml_repo_root="$(cd "$_ml_conf_dir/../.." && pwd)"
if [ -x "$_ml_repo_root/.vendor/jdk-11/bin/java" ]; then
  export JAVA_HOME="$_ml_repo_root/.vendor/jdk-11"
else
  export JAVA_HOME="${JAVA_HOME:-$_ml_repo_root/.vendor/jdk-11}"
fi
export HADOOP_HOME="${HADOOP_HOME:-$_ml_repo_root/.vendor/hadoop-3.3.6}"
export HADOOP_CONF_DIR="${HADOOP_CONF_DIR:-$_ml_conf_dir}"
# HADOOP_DATA_DIR / LOG / PID：conf 下的 *.xml 用 ${env.HADOOP_DATA_DIR} 引用它们，
# 守护进程与 CLI 启动时都会 source 本文件，故替换必然发生（默认落在仓库内，gitignore）。
export HADOOP_DATA_DIR="${HADOOP_DATA_DIR:-$_ml_repo_root/.hadoop-data}"
export HADOOP_LOG_DIR="${HADOOP_LOG_DIR:-$_ml_repo_root/.hadoop-logs}"
export HADOOP_PID_DIR="${HADOOP_PID_DIR:-$HADOOP_DATA_DIR/pids}"
export HADOOP_HEAPSIZE="${HADOOP_HEAPSIZE:-512}"
export YARN_HEAPSIZE="${YARN_HEAPSIZE:-512}"
# mapred-site.xml 的容器环境三行用 ${env.HADOOP_HOME} 提交时替换；这里再兜一层，
# 让未经 env.sh 的提交也能拿到一致的 HADOOP_MAPRED_HOME。
export HADOOP_MAPRED_HOME="${HADOOP_MAPRED_HOME:-$HADOOP_HOME}"
unset _ml_conf_dir _ml_repo_root
# 伪分布式单机：限制守护进程堆，避免 8GB VM 内存吃紧
export HDFS_NAMENODE_OPTS="-Xmx1024m -XX:+UseParallelGC"
export HDFS_DATANODE_OPTS="-Xmx512m -XX:+UseParallelGC"
export HDFS_SECONDARYNAMENODE_OPTS="-Xmx512m -XX:+UseParallelGC"
export YARN_RESOURCEMANAGER_OPTS="-Xmx768m -XX:+UseParallelGC"
export YARN_NODEMANAGER_OPTS="-Xmx768m -XX:+UseParallelGC"
