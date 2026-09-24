#!/usr/bin/env bash
# ============================================================
# MovieLens 实验环境一键安装脚本
# 适用：Ubuntu 22.04（VMware 虚拟机内）
# 作用：装 Java 11 + Hadoop 3.3.6 + 配 SSH 免密 + 写好四个配置文件
# 用法：bash setup_hadoop.sh        （不用 sudo，脚本内部自己会 sudo）
# ============================================================

set -e
USER_NAME=$(whoami)
HADOOP_VERSION=3.3.6
MIRROR="https://mirrors.tuna.tsinghua.edu.cn/apache/hadoop/common"

echo ""
echo "=========================================="
echo " 开始安装（当前用户：$USER_NAME）"
echo "=========================================="

# ---------- 1. 更新系统 ----------
echo ""
echo "[1/8] 更新系统软件包列表..."
sudo apt update -y

# ---------- 2. 装 Java 11 与依赖 ----------
echo ""
echo "[2/8] 安装 Java 11 / SSH / Python3 ..."
sudo apt install -y openjdk-11-jdk openssh-server python3 python3-pip git vim curl wget

java -version 2>&1 | head -1

# ---------- 3. 下载 Hadoop ----------
echo ""
echo "[3/8] 下载 Hadoop $HADOOP_VERSION（清华镜像，约 730MB）..."
cd /tmp
if [ ! -f hadoop-$HADOOP_VERSION.tar.gz ]; then
    curl -L --retry 3 -o hadoop-$HADOOP_VERSION.tar.gz \
        "$MIRROR/hadoop-$HADOOP_VERSION/hadoop-$HADOOP_VERSION.tar.gz"
else
    echo "压缩包已存在，跳过下载"
fi

# ---------- 4. 解压 ----------
echo ""
echo "[4/8] 解压到 /opt/hadoop ..."
sudo rm -rf /opt/hadoop
sudo tar -xzf /tmp/hadoop-$HADOOP_VERSION.tar.gz -C /opt
sudo mv /opt/hadoop-$HADOOP_VERSION /opt/hadoop
sudo chown -R "$USER_NAME:$USER_NAME" /opt/hadoop

# ---------- 5. 环境变量 ----------
echo ""
echo "[5/8] 写入环境变量到 ~/.bashrc ..."

# 先清理旧的，避免重复追加
sed -i '/# ===== MovieLens Hadoop Env =====/,/# ===== End MovieLens =====/d' ~/.bashrc

cat >> ~/.bashrc << 'ENVEOF'

# ===== MovieLens Hadoop Env =====
export JAVA_HOME=/usr/lib/jvm/java-11-openjdk-amd64
export HADOOP_HOME=/opt/hadoop
export PATH=$PATH:$HADOOP_HOME/bin:$HADOOP_HOME/sbin
export HADOOP_STREAMING=$HADOOP_HOME/share/hadoop/tools/lib/hadoop-streaming-3.3.6.jar
export HDFS_NAMENODE_USER=$USER
export HDFS_DATANODE_USER=$USER
export HDFS_SECONDARYNAMENODE_USER=$USER
export YARN_RESOURCEMANAGER_USER=$USER
export YARN_NODEMANAGER_USER=$USER
# ===== End MovieLens =====
ENVEOF

export JAVA_HOME=/usr/lib/jvm/java-11-openjdk-amd64
export HADOOP_HOME=/opt/hadoop
export PATH=$PATH:$HADOOP_HOME/bin:$HADOOP_HOME/sbin

hadoop version | head -1

# ---------- 6. SSH 免密 ----------
echo ""
echo "[6/8] 配置 SSH 免密登录 ..."
if [ ! -f ~/.ssh/id_rsa ]; then
    ssh-keygen -t rsa -P '' -f ~/.ssh/id_rsa
fi
cat ~/.ssh/id_rsa.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
chmod 700 ~/.ssh
sudo service ssh start || sudo systemctl start ssh || true

# ---------- 7. 四个配置文件 ----------
echo ""
echo "[7/8] 写入 Hadoop 配置文件 ..."
HADOOP_CONF=/opt/hadoop/etc/hadoop

cat > "$HADOOP_CONF/core-site.xml" << XMLEOF
<?xml version="1.0" encoding="UTF-8"?>
<?xml-stylesheet type="text/xsl" href="configuration.xsl"?>
<configuration>
  <property>
    <name>fs.defaultFS</name>
    <value>hdfs://localhost:9000</value>
  </property>
  <property>
    <name>hadoop.tmp.dir</name>
    <value>/home/$USER_NAME/hadoop/tmp</value>
  </property>
</configuration>
XMLEOF

cat > "$HADOOP_CONF/hdfs-site.xml" << XMLEOF
<?xml version="1.0" encoding="UTF-8"?>
<?xml-stylesheet type="text/xsl" href="configuration.xsl"?>
<configuration>
  <property>
    <name>dfs.replication</name>
    <value>1</value>
  </property>
  <property>
    <name>dfs.namenode.name.dir</name>
    <value>/home/$USER_NAME/hadoop/namenode</value>
  </property>
  <property>
    <name>dfs.datanode.data.dir</name>
    <value>/home/$USER_NAME/hadoop/datanode</value>
  </property>
</configuration>
XMLEOF

cat > "$HADOOP_CONF/mapred-site.xml" << XMLEOF
<?xml version="1.0" encoding="UTF-8"?>
<?xml-stylesheet type="text/xsl" href="configuration.xsl"?>
<configuration>
  <property>
    <name>mapreduce.framework.name</name>
    <value>yarn</value>
  </property>
</configuration>
XMLEOF

cat > "$HADOOP_CONF/yarn-site.xml" << XMLEOF
<?xml version="1.0" encoding="UTF-8"?>
<?xml-stylesheet type="text/xsl" href="configuration.xsl"?>
<configuration>
  <property>
    <name>yarn.nodemanager.aux-services</name>
    <value>mapreduce_shuffle</value>
  </property>
  <property>
    <name>yarn.nodemanager.env-whitelist</name>
    <value>JAVA_HOME,HADOOP_COMMON_HOME,HADOOP_HDFS_HOME,HADOOP_CONF_DIR,HADOOP_YARN_HOME</value>
  </property>
</configuration>
XMLEOF

# ---------- 8. 创建数据目录 ----------
echo ""
echo "[8/8] 创建 HDFS 数据目录 ..."
mkdir -p ~/hadoop/tmp ~/hadoop/namenode ~/hadoop/datanode ~/data/raw

echo ""
echo "=========================================="
echo " 安装完成！"
echo "=========================================="
echo ""
echo "接下来手动执行这三条命令："
echo ""
echo "  source ~/.bashrc"
echo "  hdfs namenode -format        （只在第一次执行！）"
echo "  start-dfs.sh && start-yarn.sh"
echo ""
echo "然后运行 jps 检查，应该看到 5 个进程："
echo "  NameNode / DataNode / SecondaryNameNode / ResourceManager / NodeManager"
echo ""
echo "每次重启虚拟机后，重新执行："
echo "  sudo service ssh start && start-dfs.sh && start-yarn.sh"
echo ""
