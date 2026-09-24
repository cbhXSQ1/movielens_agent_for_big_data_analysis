# 环境搭建：Ubuntu 虚拟机（Agent 侧自用）

> 适用人：第 5 节 Agent 负责人（leo63772558）
> 依据：队长 `docs/hadoop/plan.md` §1 运行环境要求（Ubuntu 22.04+ / Java 11 / Hadoop 3.3.6）
> 本机实测：内存 16GB、CPU 32 核、C 盘剩 21GB、**E 盘剩 88GB**

---

## 0. 为什么我（Agent）也要装 Hadoop

队长的方案里写明：**driver 与 Agent 同在 VM 内**。
你的 Agent 要调用 driver，driver 要跑 Hadoop → 所以你的虚拟机里也得有完整的 Hadoop 环境。

好处是：迭代二（机器学习）、迭代三（知识图谱）也都在上面跑，队长说"装一个后面好跑"，就是这个意思。

## 1. 硬件结论（你的机器够用）

| 项目 | 建议分配 |
|---|---|
| 内存 | **8192 MB**（8GB） |
| CPU | **4 核** |
| 硬盘 | **40 GB**，动态分配 |
| 存放位置 | **`E:\VM\Ubuntu22`**（别放 C 盘，只剩 21GB 不够） |

---

## 2. 第一步：装虚拟机软件

下载 **VirtualBox**（免费）：https://www.virtualbox.org/wiki/Downloads
选 **Windows hosts**，一路下一步装完。

> 备选：VMware Workstation Player（个人非商业免费）。二选一即可，别都装。

## 3. 第二步：下 Ubuntu 系统镜像

下载 **Ubuntu 22.04.5 桌面版 ISO**（约 5GB）：
https://releases.ubuntu.com/22.04/ubuntu-22.04.5-desktop-amd64.iso

⚠️ **必须选 22.04，不要装最新的 24.04**——24.04 默认源里装 Java 11 会麻烦，22.04 直接 `apt install openjdk-11-jdk` 就行。

## 4. 第三步：建虚拟机

VirtualBox 点"新建"：

| 选项 | 填什么 |
|---|---|
| 名称 | `Ubuntu22-Hadoop` |
| **文件夹** | **`E:\VM`**（改这里！默认是 C 盘） |
| 类型 | Linux |
| 版本 | Ubuntu (64-bit) |
| **内存** | **8192 MB** |
| **处理器** | **4** |
| 硬盘 | 现在创建虚拟硬盘 → VDI → **动态分配** → **40 GB** |

建好后**先别启动**，做两件事：
1. 选中虚拟机 → 设置 → 存储 → 光驱里挂上刚下的 ISO
2. 设置 → 系统 → 启动顺序，把"光驱"排到"硬盘"前面

## 5. 第四步：装 Ubuntu

启动 → 选 "Install Ubuntu" → 一路默认：

- 语言选 **English**（中文也行，但报错信息英文好搜）
- 用户名密码**记牢**，后面 sudo 要用
- 选 "Minimal installation" 装得更快
- 装完重启，提示移除安装介质时直接回车

## 6. 第五步：装系统后立刻做快照（重要）

VirtualBox 右侧菜单 → 快照 → 生成，命名 `clean-install`。

**以后搞崩了就回滚到这里。** 队长 plan 里也要求这一步。

## 7. 第六步：安装增强功能（让复制粘贴能用）

虚拟机窗口菜单：设备 → 安装增强功能 → 按提示跑完 → 重启。
之后 Windows 和虚拟机之间可以**复制粘贴、共享剪贴板**，方便很多。

## 8. 第七步：装 Java 11 + 依赖

打开终端（Ctrl+Alt+T）：

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y openjdk-11-jdk openssh-server python3 python3-pip git vim

java -version        # 必须看到 openjdk version "11.x"
python3 --version    # 3.10+ 即可
```

## 9. 第八步：装 Hadoop 3.3.6

```bash
cd /tmp
wget https://archive.apache.org/dist/hadoop/common/hadoop-3.3.6/hadoop-3.3.6.tar.gz
sudo tar -xzf hadoop-3.3.6.tar.gz -C /opt
sudo mv /opt/hadoop-3.3.6 /opt/hadoop
sudo chown -R $USER:$USER /opt/hadoop
```

写入 `~/.bashrc`（用 `nano ~/.bashrc`，贴到文件末尾）：

```bash
export JAVA_HOME=/usr/lib/jvm/java-11-openjdk-amd64
export HADOOP_HOME=/opt/hadoop
export PATH=$PATH:$HADOOP_HOME/bin:$HADOOP_HOME/sbin
export HADOOP_STREAMING=$HADOOP_HOME/share/hadoop/tools/lib/hadoop-streaming-3.3.6.jar
export HDFS_NAMENODE_USER=$USER
export HDFS_DATANODE_USER=$USER
export HDFS_SECONDARYNAMENODE_USER=$USER
export YARN_RESOURCEMANAGER_USER=$USER
export YARN_NODEMANAGER_USER=$USER
```

```bash
source ~/.bashrc
hadoop version      # 应输出 Hadoop 3.3.6
```

## 10. 第九步：配 SSH 免密（伪分布式必需）

```bash
ssh-keygen -t rsa -P '' -f ~/.ssh/id_rsa
cat ~/.ssh/id_rsa.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
sudo service ssh start
ssh localhost        # 不用输密码就成功，exit 退出
```

## 11. 第十步：四个配置文件

都在 `/opt/hadoop/etc/hadoop/` 下，用 `nano` 打开改。

**core-site.xml**：
```xml
<configuration>
  <property><name>fs.defaultFS</name><value>hdfs://localhost:9000</value></property>
  <property><name>hadoop.tmp.dir</name><value>/home/你的用户名/hadoop/tmp</value></property>
</configuration>
```

**hdfs-site.xml**（副本数设 1，单机只有一个 DataNode）：
```xml
<configuration>
  <property><name>dfs.replication</name><value>1</value></property>
  <property><name>dfs.namenode.name.dir</name><value>/home/你的用户名/hadoop/namenode</value></property>
  <property><name>dfs.datanode.data.dir</name><value>/home/你的用户名/hadoop/datanode</value></property>
</configuration>
```

**mapred-site.xml**：
```xml
<configuration>
  <property><name>mapreduce.framework.name</name><value>yarn</value></property>
</configuration>
```

**yarn-site.xml**：
```xml
<configuration>
  <property><name>yarn.nodemanager.aux-services</name><value>mapreduce_shuffle</value></property>
</configuration>
```

## 12. 第十一步：格式化并启动

```bash
mkdir -p ~/hadoop/tmp ~/hadoop/namenode ~/hadoop/datanode
hdfs namenode -format      # 只在第一次执行！重复执行会清空数据
start-dfs.sh
start-yarn.sh
jps
```

**验收标准（队长 plan §1）**：`jps` 出现 **5 个**进程——
NameNode、DataNode、SecondaryNameNode、ResourceManager、NodeManager

再跑一下：`hdfs dfsadmin -report`，正常输出就 OK。

> 每次重启虚拟机后要重新执行：
> `sudo service ssh start && start-dfs.sh && start-yarn.sh`

## 13. 第十二步：把数据集放进虚拟机

Windows 上的压缩包在：
`C:\Users\tffnf\Desktop\大数据分析作业及实验\实验2\ml-1m.zip`

用共享剪贴板/U 盘/共享文件夹拷进虚拟机，然后：

```bash
mkdir -p ~/data/raw/ml-1m
cd ~/data/raw/ml-1m
unzip ml-1m.zip          # 解压出 ratings.dat users.dat movies.dat
```

## 14. 常见坑

| 症状 | 原因 | 解决 |
|---|---|---|
| 虚拟机卡死/无法启动 | 虚拟化没开 | 进 BIOS 开 Intel VT-x / AMD-V |
| `java -version` 不是 11 | JAVA_HOME 没生效 | `source ~/.bashrc`；确认路径 `ls /usr/lib/jvm/` |
| `Connection refused` | SSH 没起 | `sudo service ssh start` |
| `Permission denied (publickey)` | 免密没配好 | 重做第 10 步，`chmod 600 ~/.ssh/authorized_keys` |
| DataNode 起来就挂 | 多次 format | 删 `~/hadoop/datanode` 重新 format（**会丢数据**） |
| `Unsupported class file major version` | 用了高版本 Java | JAVA_HOME 必须指向 JDK 11 |

## 15. 装完之后

1. `git clone https://github.com/cbhXSQ1/movielens_agent_for_big_data_analysis.git`
2. 等队长 push driver，拉下来就能在他的环境里跑
3. 把 `hadoop-streaming` 的 jar 路径记到 `hadoop/scripts/env.sh`（队长 plan 要求）

---

## 时间预估

| 环节 | 时间 |
|---|---|
| 下 VirtualBox + Ubuntu ISO | 20–40 分钟（看网速） |
| 装 Ubuntu 系统 | 20–30 分钟 |
| 装 Java + Hadoop + 配置 | 30–60 分钟 |
| **合计** | **约 1.5–2.5 小时** |

**所以现在就开始下 ISO**——下载最耗时间，可以先挂着让它下。
