#!/usr/bin/env bash
# =============================================================================
# hadoop/scripts/mcp_server.sh —— 能力服务的常驻启停脚本
# =============================================================================
# 用法：
#   hadoop/scripts/mcp_server.sh init        # 生成配置与访问口令（幂等）
#   hadoop/scripts/mcp_server.sh start       # 后台常驻启动
#   hadoop/scripts/mcp_server.sh stop        # 停止（先温和后强制）
#   hadoop/scripts/mcp_server.sh restart     # 重启
#   hadoop/scripts/mcp_server.sh status      # 自检：在不在跑、监听在哪、口令在不在
#   hadoop/scripts/mcp_server.sh token       # 打印访问口令（人工复制用）
#   hadoop/scripts/mcp_server.sh logs [-f]   # 看服务日志
#
# 为什么用仓库内的独立环境：官方协议实现包装在 <repo>/.venv 里，系统自带的解释器
# 没有它。本脚本只用那个环境跑服务；driver 的业务代码仍然用系统解释器执行，
# 两者互不影响（服务层调子进程时用的是同一个系统解释器的既有入口）。
#
# 运行期文件都在 var/mcp/（已在 .gitignore 里）：
#   server.json   配置与访问口令（权限 600）
#   server.pid    进程号
#   server.log    服务日志（口令不会出现在这里，只出现指纹）
#
# 停止的判定：先发终止信号，等它自己退出；超时才强制。进程不在但端口被占时
# 如实报告"端口被别的进程占用"，不假装停干净了。
# =============================================================================
set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SELF_DIR/../.." && pwd)"
# 运行期目录（配置、进程号、日志都在这下面）。默认 var/mcp（不入版本库）。
# 用 MCP_RUNTIME_DIR 可以指向别处：验证脚本要用临时目录、或同一台机器上跑第二份
# 实例（不同端口/口令）时都用得上。
MCP_DIR="${MCP_RUNTIME_DIR:-$REPO_ROOT/var/mcp}"
PID_FILE="$MCP_DIR/server.pid"
LOG_FILE="$MCP_DIR/server.log"
CONFIG_FILE="$MCP_DIR/server.json"

PY="$REPO_ROOT/.venv/bin/python"
export PYTHONPATH="$REPO_ROOT/hadoop${PYTHONPATH:+:$PYTHONPATH}"

ACTION="${1:-status}"
shift || true

log()  { printf '%s\n' "$*"; }
warn() { printf '%s\n' "$*" >&2; }

ensure_python() {
  if [ ! -x "$PY" ]; then
    warn "找不到独立运行环境：$PY"
    warn "先建环境并装官方协议实现包，或改用系统 python3（需另装依赖）。"
    return 1
  fi
  return 0
}

port_from_config() {
  "$PY" - "$CONFIG_FILE" <<'PY'
import json, os, sys
port = ""
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        port = json.load(fh).get("port", "")
except Exception:
    port = ""
print(os.environ.get("MCP_PORT") or port)
PY
}

read_pid() {
  if [ -f "$PID_FILE" ]; then
    tr -d '[:space:]' < "$PID_FILE"
  fi
}

pid_alive() {
  local pid="$1"
  [ -n "$pid" ] || return 1
  kill -0 "$pid" 2>/dev/null
}

wait_listening() {
  local seconds="${1:-15}" port="${2:-}"
  local i=0
  while [ "$i" -lt "$seconds" ]; do
    if [ -n "$port" ] && ss -ltn 2>/dev/null | grep -q ":$port "; then
      return 0
    fi
    sleep 1
    i=$((i + 1))
  done
  return 1
}

#: 按监听端口反查真正的服务进程号。
#: 为什么不用启动时捕获的 `$!`：以独立会话启动时，`$!` 可能不是最终那个监听进程
#: （中间可能有包装进程），把它写进进程号文件会让 stop/status 认错对象 —— 认错的
#: 后果是"停止"时打不动真正的服务，或者把无关进程当成服务。端口反查拿到的才是
#: 真正在监听的那一个。
pid_on_port() {
  local port="${1:-}"
  [ -n "$port" ] || return 1
  ss -ltnp 2>/dev/null \
    | awk -v p=":$port" 'index($4, p) > 0 {print}' \
    | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2
}

#: 某个进程的命令行（把参数间的空字节换成空格，截断到 200 字符便于打印）。
pid_cmdline() {
  local pid="${1:-}"
  [ -n "$pid" ] || return 1
  if [ -r "/proc/$pid/cmdline" ]; then
    tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | cut -c1-200
  else
    ps -o args= -p "$pid" 2>/dev/null | cut -c1-200
  fi
}

#: 这个进程号是不是**我们的服务**。
#:
#: 为什么必须核对：只按端口反查进程号，在"端口被别的进程占用"时会把陌生进程
#: 当成服务进程写进进程号文件；而停止时若只做"还存在吗"的判断，进程号被系统回收后
#: 可能把**无关进程**杀掉（先温和后强制，杀伤面更大）。核对命令行是这一步唯一
#: 可靠的依据：我们的服务命令行一定同时含有 mcpserver 与 serve。
#:
#: 读不到命令行时（进程刚消失、没有 /proc 的环境）一律判"不是"，宁可少认领，
#: 也不要认错对象。
pid_is_service() {
  local pid="${1:-}"
  [ -n "$pid" ] || return 1
  local cmd
  cmd="$(pid_cmdline "$pid")"
  [ -n "$cmd" ] || return 1
  case "$cmd" in
    *mcpserver*serve*) return 0 ;;
    *) return 1 ;;
  esac
}

case "$ACTION" in
  init)
    ensure_python || exit 1
    # 额外参数透传：`mcp_server.sh init --force` 用来换一枚新口令
    # （默认是幂等的：已有配置与口令都保留，避免一次误执行把在用的客户端全踢掉）。
    exec "$PY" -m mcpserver init --config "$CONFIG_FILE" "$@"
    ;;

  start)
    ensure_python || exit 1
    if [ ! -f "$CONFIG_FILE" ]; then
      log "还没有配置，先生成一份（含随机访问口令）："
      "$PY" -m mcpserver init --config "$CONFIG_FILE" || exit 1
    fi
    port="$(port_from_config)"

    # 进程号文件里的进程：只有命令行对得上才算数（否则那是系统回收后复用的号）。
    pid="$(read_pid)"
    if [ -n "$pid" ] && pid_alive "$pid"; then
      if pid_is_service "$pid"; then
        log "服务已经在运行（进程 $pid），不重复启动。"
        exec "$PY" -m mcpserver status --config "$CONFIG_FILE"
      fi
      warn "进程号文件里的 $pid 命令行对不上，不是本服务 —— 清掉这份陈旧记录，继续启动。"
      rm -f "$PID_FILE"
    elif [ -n "$pid" ]; then
      rm -f "$PID_FILE"
    fi

    # 端口已被占用：分清"是我们的服务"与"是别的进程"。
    # 是别的进程时**拒绝启动并且不写进程号文件** —— 否则后面的 stop 会把陌生进程当目标。
    if [ -n "$port" ] && ss -ltn 2>/dev/null | grep -q ":$port "; then
      holder="$(pid_on_port "$port")"
      if [ -n "$holder" ] && pid_is_service "$holder"; then
        log "端口 $port 上的进程 $holder 就是本服务（进程号文件缺了），按它在运行处理。"
        printf '%s\n' "$holder" > "$PID_FILE"
        exec "$PY" -m mcpserver status --config "$CONFIG_FILE"
      fi
      warn "端口 $port 已被其它进程占用（进程 ${holder:-unknown}），拒绝启动。"
      [ -n "$holder" ] && warn "占用者命令行：$(pid_cmdline "$holder")"
      warn "没有写入进程号文件，也不会去动那个进程。换端口（MCP_PORT）或先停掉占用者。"
      exit 1
    fi

    mkdir -p "$MCP_DIR"
    # 以独立会话启动：启动它的终端（或上层进程组）之后被整体收走时，服务不该跟着退出。
    # 实测踩到过：用普通后台方式启动，发起它的那个会话一结束，服务就被一起带走了 ——
    # 对"常驻服务"来说这是致命的，所以优先用 setsid 脱离，取不到时退回 nohup。
    if command -v setsid >/dev/null 2>&1; then
      setsid "$PY" -m mcpserver serve --config "$CONFIG_FILE" >>"$LOG_FILE" 2>&1 &
    else
      warn "没有 setsid，退回普通后台方式启动；启动它的终端退出时服务可能一并退出。"
      nohup "$PY" -m mcpserver serve --config "$CONFIG_FILE" >>"$LOG_FILE" 2>&1 &
    fi
    started_pid=$!
    if wait_listening 20 "$port"; then
      real_pid="$(pid_on_port "$port")"
      # 按端口反查到的进程也必须核对命令行：不经核对就写进进程号文件，
      # 等于给 stop 埋了一个"可能杀错人"的目标。
      if [ -n "$real_pid" ] && pid_is_service "$real_pid"; then
        printf '%s\n' "$real_pid" > "$PID_FILE"
        log "服务已启动（进程 $real_pid），监听端口 $port。"
      elif pid_is_service "$started_pid"; then
        printf '%s\n' "$started_pid" > "$PID_FILE"
        log "服务已启动（进程 $started_pid），监听端口 $port。"
      else
        warn "端口 $port 已在监听，但反查到的进程（${real_pid:-unknown}）命令行对不上本服务。"
        warn "没有写进程号文件；请用 logs 与 status 确认到底是哪个进程占了端口。"
        exit 1
      fi
    elif pid_alive "$started_pid"; then
      printf '%s\n' "$started_pid" > "$PID_FILE"
      warn "进程在跑（$started_pid）但端口 $port 尚未就绪，请用 logs 查看原因。"
    else
      warn "启动失败，进程已退出。日志最后几行："
      tail -n 20 "$LOG_FILE" >&2 || true
      rm -f "$PID_FILE"
      exit 1
    fi
    log "自检："
    exec "$PY" -m mcpserver status --config "$CONFIG_FILE"
    ;;

  stop)
    port="$(port_from_config)"
    pid="$(read_pid)"

    # 一、进程号文件里的进程存在但**命令行对不上**：那是复用号，绝不能杀。
    if [ -n "$pid" ] && pid_alive "$pid" && ! pid_is_service "$pid"; then
      warn "进程号文件记录的进程 $pid 不是本服务（命令行：$(pid_cmdline "$pid")）。"
      warn "这是系统回收后复用的号 —— 只清理陈旧记录，绝不向它发信号。"
      rm -f "$PID_FILE"
      pid=""
    fi

    # 二、没有可用进程号：按端口反查，同样要核对命令行。
    if [ -z "$pid" ] || ! pid_alive "$pid"; then
      rm -f "$PID_FILE"
      holder="$(pid_on_port "$port")"
      if [ -n "$holder" ] && pid_is_service "$holder"; then
        warn "进程号文件缺失或已过期，但端口 $port 上的进程 $holder 确实是本服务，按它来停止。"
        printf '%s\n' "$holder" > "$PID_FILE"
        pid="$holder"
      elif [ -n "$holder" ]; then
        warn "端口 $port 被其它进程占用（进程 $holder，命令行：$(pid_cmdline "$holder")），不动它。"
        exit 1
      else
        log "服务未在运行。"
        exit 0
      fi
    fi

    log "正在停止（进程 $pid）……"
    kill "$pid" 2>/dev/null || true
    i=0
    while [ "$i" -lt 15 ] && pid_alive "$pid"; do
      sleep 1
      i=$((i + 1))
    done
    if pid_alive "$pid"; then
      # 强制结束前再核对一次命令行：这 15 秒里进程号有可能已被回收。
      if pid_is_service "$pid"; then
        warn "15 秒内没退出，强制结束。"
        kill -9 "$pid" 2>/dev/null || true
        sleep 1
      else
        warn "进程 $pid 已经换了主人（命令行对不上），放弃强制结束，只清理进程号文件。"
        rm -f "$PID_FILE"
        exit 1
      fi
    fi
    rm -f "$PID_FILE"
    if pid_alive "$pid"; then
      warn "仍然停不掉，请手工检查进程 $pid。"
      exit 1
    fi
    log "已停止。"
    ;;

  restart)
    "$0" stop
    exec "$0" start
    ;;

  status)
    ensure_python || exit 1
    if [ ! -f "$CONFIG_FILE" ]; then
      warn "还没有配置（$CONFIG_FILE）。先执行：$0 init"
      exit 1
    fi
    "$PY" -m mcpserver status --config "$CONFIG_FILE"
    ;;

  token)
    ensure_python || exit 1
    exec "$PY" -m mcpserver token --config "$CONFIG_FILE"
    ;;

  logs)
    if [ ! -f "$LOG_FILE" ]; then
      warn "还没有日志（$LOG_FILE）。服务启动后会写在这里。"
      exit 1
    fi
    if [ "${1:-}" = "-f" ]; then
      exec tail -f "$LOG_FILE"
    fi
    tail -n "${1:-100}" "$LOG_FILE"
    ;;

  *)
    warn "用法：$0 {init|start|stop|restart|status|token|logs [-f]}"
    exit 2
    ;;
esac
