# 由 hadoop/scripts/install_env.sh 自动生成，请勿手改；改模板或 env.sh 后重跑。
# 显式指定 Java 11（系统默认 java 为 21，Hadoop 3.3.6 官方支持 8/11；见 decisions.md D-003）
export JAVA_HOME=/home/ubuntu/movielens_agent_for_big_data_analysis/.vendor/jdk-11
export HADOOP_HOME=/home/ubuntu/movielens_agent_for_big_data_analysis/.vendor/hadoop-3.3.6
export HADOOP_CONF_DIR=/home/ubuntu/movielens_agent_for_big_data_analysis/hadoop/conf
export HADOOP_LOG_DIR=/home/ubuntu/movielens_agent_for_big_data_analysis/.hadoop-logs
export HADOOP_PID_DIR=/home/ubuntu/movielens_agent_for_big_data_analysis/.hadoop-data/pids
export HADOOP_HEAPSIZE=512
export YARN_HEAPSIZE=512
# 伪分布式单机：限制守护进程堆，避免 8GB VM 内存吃紧
export HDFS_NAMENODE_OPTS="-Xmx1024m -XX:+UseParallelGC"
export HDFS_DATANODE_OPTS="-Xmx512m -XX:+UseParallelGC"
export HDFS_SECONDARYNAMENODE_OPTS="-Xmx512m -XX:+UseParallelGC"
export YARN_RESOURCEMANAGER_OPTS="-Xmx768m -XX:+UseParallelGC"
export YARN_NODEMANAGER_OPTS="-Xmx768m -XX:+UseParallelGC"
