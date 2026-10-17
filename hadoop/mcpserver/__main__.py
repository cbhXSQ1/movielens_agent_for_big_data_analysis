# -*- coding: utf-8 -*-
"""mcpserver/__main__.py —— 命令行入口（启动 / 初始化 / 取口令 / 探活 / 自检）。

给运维用的四个动作：
    python -m mcpserver init        生成配置与访问口令（幂等：已有就保留）
    python -m mcpserver serve       常驻启动（默认动作）
    python -m mcpserver token       打印当前访问口令（显式索取时才输出）
    python -m mcpserver health      调一次健康检查
    python -m mcpserver status      打印自检信息（口令只给指纹、是否在监听）

为什么单独做入口：常驻启动脚本（`hadoop/scripts/mcp_server.sh`）只做进程管理，
"配置在哪、口令是什么、服务活没活"这些判断放在 Python 里，脚本就不用重复实现一遍。
"""

import argparse
import json
import os
import socket
import sys
import urllib.error
import urllib.request

from . import server as srv


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _print_json(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _base_url(cfg, host=None, port=None):
    """探活用的基地址：配置里若是"所有接口"，探活按本机回环走。"""
    target_host = host or cfg.get("host") or "127.0.0.1"
    if target_host in ("0.0.0.0", "::", ""):
        target_host = "127.0.0.1"
    return "http://%s:%s" % (target_host, port or cfg.get("port"))


def _listening(host, port, timeout=1.0):
    """端口是否有人在听（只试连，不发任何数据）。"""
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "::", "", None) else host
    try:
        with socket.create_connection((probe_host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _read_pid_file(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def _pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------------
# 动作
# ---------------------------------------------------------------------------

def cmd_init(args):
    """生成配置与口令。默认**幂等**（已有配置与口令都保留）。

    `--force` 换一枚新口令（怀疑泄露时用）：只改口令，其余配置原样保留。
    为什么默认不换：误执行一次 init 就把正在用的客户端全踢掉，代价太大；
    而"要换"这个动作应该是**显式**的。
    """
    force = bool(getattr(args, "force", False))
    cfg, created = srv.ensure_config(args.config, create_token=True,
                                    rotate_token=force)
    path = cfg["config_path"]
    print("配置文件：%s" % path)
    if created and force:
        print("访问口令%s（本次已按你的要求换新；旧口令立即失效）"
              % srv.token_fingerprint(cfg["token"]))
    else:
        print("访问口令%s%s" % (srv.token_fingerprint(cfg["token"]),
                              "（本次新生成）" if created else "（沿用原有）"))
    print("监听地址：%s:%s，协议路径 %s，健康检查 %s"
          % (cfg["host"], cfg["port"], cfg["streamable_http_path"], cfg["health_path"]))
    if cfg["host"] == "0.0.0.0":
        print("提示：需要让宿主机访问时，在虚拟机软件里加一条端口转发"
              "（宿主机侧端口由你定，虚拟机侧固定 %s）。" % cfg["port"])
    print("查看口令：python -m mcpserver token    （或 hadoop/scripts/mcp_server.sh token）")
    if not force:
        print("换口令：加 --force（只换口令，其余配置保留）")
    return 0


def cmd_serve(args):
    """常驻启动。"""
    if args.host:
        os.environ["MCP_HOST"] = args.host
    if args.port:
        os.environ["MCP_PORT"] = str(args.port)
    # 首次启动允许自动生成口令，但会明确告知：口令写在配置文件里，日志里不出现
    cfg, created = srv.ensure_config(args.config, create_token=True)
    if created:
        print("首次启动：已生成访问口令并写入 %s（口令不打印，用 token 动作查看）"
              % cfg["config_path"], file=sys.stderr)
    return srv.serve(config_path=cfg["config_path"], log_level=args.log_level)


def cmd_token(args):
    """打印当前口令（仅此动作会输出口令本身，供人工复制使用）。"""
    cfg = srv.load_config(args.config)
    token = (cfg.get("token") or "").strip()
    if not token:
        print("还没有口令。先执行 `python -m mcpserver init` 生成。", file=sys.stderr)
        return 1
    print(token)
    return 0


def cmd_health(args):
    """调一次健康检查（不需要口令）。"""
    cfg = srv.load_config(args.config)
    url = _base_url(cfg, args.host, args.port) + (cfg.get("health_path") or "/health")
    try:
        with urllib.request.urlopen(url, timeout=args.timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        print("健康检查失败：HTTP %s（%s）" % (exc.code, url), file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError) as exc:
        print("健康检查失败：连不上 %s（%s）。服务可能没启动。" % (url, exc), file=sys.stderr)
        return 1
    try:
        _print_json(json.loads(body))
    except ValueError:
        print(body)
    return 0


def cmd_status(args):
    """自检：配置、口令指纹、监听状态、（可选）健康情况。"""
    cfg = srv.load_config(args.config)
    port = int(cfg["port"])
    listening = _listening(cfg["host"], port)
    pid = _read_pid_file(os.path.join(os.path.dirname(cfg["config_path"]), "server.pid"))
    payload = {
        "config_path": cfg["config_path"],
        "config_exists": os.path.isfile(cfg["config_path"]),
        "host": cfg["host"],
        "port": port,
        "streamable_http_path": cfg["streamable_http_path"],
        "health_path": cfg["health_path"],
        "token": srv.token_fingerprint(cfg["token"]),
        # 装机排障时这一项最容易出问题：Host 头对不上会表现为"连不上"，
        # 而真正原因是防重绑定策略把客户端的 Host 判成了非法。
        "allowed_hosts": srv.effective_allowed_hosts(cfg),
        # 只读下载路由开在哪：装机时确认这两条（对端按它们取文件内容）
        "download_routes": srv.download_routes(),
        "enable_dns_rebinding_protection": bool(
            cfg.get("enable_dns_rebinding_protection", True)),
        "listening": listening,
        "pid_file_pid": pid,
        "pid_alive": _pid_alive(pid),
        "interface_version": srv.contract.INTERFACE_VERSION,
        "server_version": cfg.get("version") or srv.SERVER_VERSION,
    }
    # 健康检查路径是否合法：不合法时服务**起不来**（免得把整个服务免口令），
    # 但 status 本身要能跑起来把这个原因说清楚，所以这里只报告不抛错。
    try:
        srv.validate_health_path(cfg.get("health_path"),
                                 cfg.get("streamable_http_path"))
    except ValueError as exc:
        payload["health_path_error"] = str(exc)
    if listening:
        try:
            with urllib.request.urlopen(
                    _base_url(cfg) + (cfg.get("health_path") or "/health"), timeout=3) as resp:
                payload["health"] = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            payload["health_error"] = "%s: %s" % (type(exc).__name__, exc)
    _print_json(payload)
    return 0 if listening else 1


# ---------------------------------------------------------------------------
# 参数
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="python -m mcpserver",
        description="Hadoop 侧能力服务（标准协议，网络方式对外）")
    parser.add_argument("action", nargs="?", default="serve",
                        choices=("init", "serve", "token", "health", "status"),
                        help="要做的动作，缺省为 serve")
    parser.add_argument("--config", help="配置文件路径（缺省 var/mcp/server.json，"
                                         "也可用环境变量 MCP_CONFIG 指定）")
    parser.add_argument("--host", help="临时覆盖监听地址")
    parser.add_argument("--port", type=int, help="临时覆盖端口")
    parser.add_argument("--log-level", default="info",
                        help="日志级别（debug / info / warning / error）")
    parser.add_argument("--timeout", type=float, default=5.0, help="探活的超时秒数")
    # 只在 init 时有意义：换一枚新口令（默认是幂等的，保留旧口令）。
    parser.add_argument("--force", action="store_true",
                        help="init 时换一枚新口令（只换口令，其余配置保留）")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    handlers = {
        "init": cmd_init,
        "serve": cmd_serve,
        "token": cmd_token,
        "health": cmd_health,
        "status": cmd_status,
    }
    try:
        return handlers[args.action](args)
    except ValueError as exc:
        # 配置不合法时如实报错，绝不带着半份配置起服务
        print("配置有问题：%s" % exc, file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print("启动条件不满足：%s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
