# -*- coding: utf-8 -*-
"""接口文档 §3.11「资源与文件接口（直通，不经 LLM）」的文件系统落地层。

只搬文件、列目录、回内容：**不产生任何业务数字，也不调用任何模型**
（用户明确要求「上传数据不该让 LLM 插手」）。

http_api.py 只做路由与序列化，真正的文件系统逻辑都收在这里，便于单测与后续
在接入 Hadoop/MCP 时把「已发布列举」换成远程实现而**对外形状不变**。
原始数据上传已下线（远端能力服务不对外提供上传），本机只做方案上传与只读列举；
方案上传经远端 `upload_scheme` 落盘，无远端客户端时退回本机 config/ 落盘。

安全（§3.11.5）：
  * 路径白名单：只允许 **原始区**、`config/`、任务产物目录 `var/tasks/` 之下；
  * 解析真实路径（realpath）后校验仍在根内，拒绝 `..` / 绝对路径 / 符号链接逃逸；
  * 越界 → `FORBIDDEN_PATH`；类型不符 → `UNSUPPORTED_FILE`；过大 → `UPLOAD_TOO_LARGE`；
    文件不存在 → `FILE_NOT_FOUND`。
"""

import json
import os

#: 仓库根（本文件在 <repo>/agent/ 下，上一级即仓库根）
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT_DIR = os.path.dirname(os.path.abspath(__file__))

#: 原始区默认落在被忽略目录 agent/var/raw/（var/ 已在 .gitignore）；可被环境变量覆盖。
ENV_RAW_DIR = "AGENT_DATA_RAW_DIR"

#: 上传/预览大小上限
MAX_REQUEST_BYTES = 512 * 1024 * 1024        # 单次请求体硬上限（防一口气读爆内存）
MAX_SCHEME_UPLOAD_BYTES = 4 * 1024 * 1024    # 单个方案文件上限
MAX_PREVIEW_BYTES = 256 * 1024               # 预览读取上限

SCHEME_EXTS = (".json",)
PREVIEWABLE_KINDS = ("json", "md", "text")


class ResourceError(Exception):
    """结构化错误（对应 §3.10 错误信封）。http_api 直接 `envelope()` 序列化。"""

    def __init__(self, code, message, **detail):
        Exception.__init__(self, message)
        self.code = code
        self.message = message
        self.detail = {k: v for k, v in detail.items() if v is not None}

    def envelope(self):
        err = {"code": self.code, "message": self.message}
        err.update(self.detail)
        return {"ok": False, "error": err}


# ---------------------------------------------------------------------------
# 远端调用接缝：资源类端点改由远端能力服务提供，本层只做字段映射
# ---------------------------------------------------------------------------

def _remote(client, name, args=None):
    """调远端能力并返回**成功**信封；失败如实抛 `ResourceError`。

    约定（与 `agent/mcp_contract.py` 的一致）：
      * `client is None`（本机没有接入远端）→ `MCP_UNAVAILABLE`，不假装成功；
      * 远端 `ok=false` → 原样转述它的 `error.code` / `error.message` 与其余细节
        （如 `reason_kind`），不换成别的结论、不填占位值；
      * 远端返回不是对象信封 → `DRIVER_BAD_OUTPUT`。

    `ResourceError.envelope()` 会把细节并进 `error` 对象，出口形状与前端既有的
    `{"ok": false, "error": {code, message, …}}` 完全一致。
    """
    if client is None:
        raise ResourceError("MCP_UNAVAILABLE",
                            "远端能力服务未接入或未配置：%s" % name)
    env = client.call_tool(name, args or {})
    if not isinstance(env, dict):
        raise ResourceError("DRIVER_BAD_OUTPUT", "远端返回非信封：%r" % (env,))
    if not env.get("ok"):
        err = env.get("error")
        err = err if isinstance(err, dict) else {}
        code = err.get("code") or "DRIVER_BAD_OUTPUT"
        message = err.get("message") or ("远端调用失败：%s" % name)
        detail = {k: v for k, v in err.items() if k not in ("code", "message")}
        raise ResourceError(code, message, **detail)
    return env


# ---------------------------------------------------------------------------
# 白名单根
# ---------------------------------------------------------------------------

def raw_root():
    """原始区根目录（环境变量 `AGENT_DATA_RAW_DIR` 覆盖，默认 agent/var/raw）。"""
    override = os.environ.get(ENV_RAW_DIR)
    if override:
        return os.path.abspath(override)
    return os.path.join(AGENT_DIR, "var", "raw")


def config_root():
    """方案文件根目录（仓库 config/）。"""
    return os.path.join(REPO_ROOT, "config")


def tasks_root():
    """任务产物根目录（与 driver 同约定：ML_VAR_DIR 或 <repo>/var，再 /tasks）。"""
    base = os.environ.get("ML_VAR_DIR") or os.path.join(REPO_ROOT, "var")
    return os.path.join(base, "tasks")


def allowed_roots():
    return [raw_root(), config_root(), tasks_root()]


def _is_absolute(relpath):
    relpath = str(relpath)
    if os.path.isabs(relpath):
        return True
    if relpath.startswith("\\"):
        return True
    if len(relpath) >= 2 and relpath[1] == ":":     # Windows 盘符
        return True
    return False


def _resolve(root, relpath, must_exist=False):
    """把 relpath 解析到 root 之下并校验真实路径未逃逸。

    越界（`..` / 绝对路径 / 符号链接指向根外）一律 `FORBIDDEN_PATH`。
    """
    if relpath is None:
        raise ResourceError("USAGE", "缺少路径参数")
    relpath = str(relpath)
    if _is_absolute(relpath):
        raise ResourceError("FORBIDDEN_PATH", "不允许绝对路径：%s" % relpath)
    root_real = os.path.realpath(root)
    target = os.path.realpath(os.path.join(root_real, relpath))
    if target != root_real and not target.startswith(root_real + os.sep):
        raise ResourceError("FORBIDDEN_PATH", "路径越界（不在白名单内）：%s" % relpath)
    if must_exist and not os.path.exists(target):
        raise ResourceError("FILE_NOT_FOUND", "文件不存在：%s" % relpath)
    return target


def _ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def _ext(name):
    return os.path.splitext(str(name))[1].lower()


def _sanitize_component(name, default):
    """把用户给的名字清洗成单个安全的分量（去目录、去非法字符、防 `..`）。"""
    if name is None:
        return default
    text = str(name).replace("\\", "/")
    text = text.split("/")[-1].strip()
    text = "".join(ch for ch in text if ch >= " " and ch not in '<>:"|?*')
    text = text.strip()
    if not text or text in (".", ".."):
        return default
    return text


def _rel(path, base):
    return os.path.relpath(path, base).replace(os.sep, "/")


def _stat(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st


# ---------------------------------------------------------------------------
# multipart 字段提取（解析在 http_api，这里只按字段名取用）
# ---------------------------------------------------------------------------

def _decode_text(data):
    if data is None:
        return ""
    return data.decode("utf-8", errors="replace")


def _field(parts, name):
    for part in parts:
        if part.get("name") == name:
            return part
    return None


def _file_parts(parts, field):
    """取上传文件段：优先按字段名，退化取任意带 filename 的段（兼容前端差异）。"""
    chosen = [p for p in parts if p.get("name") == field and p.get("filename")]
    if not chosen:
        chosen = [p for p in parts if p.get("filename")]
    return chosen


# ---------------------------------------------------------------------------
# §3.11.1 原始数据（远端只读列举）
# ---------------------------------------------------------------------------

def list_raw(client):
    """列原始区数据集（远端 `list_raw_datasets`）。

    出口形状保持不变：`{"dir", "datasets":[{name,path,files:[{name,size,updated_at}],
    size,updated_at}]}`。`dir` 远端不提供时给空串（描述性前缀，界面按需展示）。
    远端没有原始区 / 目录不存在时如实转述它的错误，不拿空列表冒充。

    现场服务把原始区根下的文件（如 `ml-1m/` 里的三张表直接摊在根目录）放在扁平
    `files` 里、而 `datasets` 为空。前端只认「数据集」一种形状，因此这里把散装文件
    归成**一个**数据集，否则原始数据面板会看不到任何东西。`datasets` 非空时依旧
    逐条映射、优先使用；两者都没有时如实回空列表，绝不凭空造条目。
    """
    env = _remote(client, "list_raw_datasets")
    dir_value = env.get("dir") or env.get("raw_dir") or ""
    datasets = []
    for item in env.get("datasets") or []:
        if not isinstance(item, dict):
            continue
        files = []
        for entry in item.get("files") or []:
            if not isinstance(entry, dict):
                continue
            files.append({"name": entry.get("name"),
                          "size": entry.get("size"),
                          "updated_at": entry.get("updated_at")})
        datasets.append({"name": item.get("name"),
                         "path": item.get("path"),
                         "files": files,
                         "size": item.get("size"),
                         "updated_at": item.get("updated_at")})

    if not datasets:
        files = []
        for entry in env.get("files") or []:
            if not isinstance(entry, dict):
                continue
            files.append({"name": entry.get("name"),
                          "size": entry.get("size"),
                          "updated_at": entry.get("updated_at")})
        if files:
            sizes = [f["size"] for f in files
                     if isinstance(f["size"], (int, float))]
            updated = [f["updated_at"] for f in files
                       if isinstance(f["updated_at"], (int, float))]
            name = os.path.basename(str(dir_value).rstrip("/\\")) or "原始数据"
            datasets.append({"name": name,
                             "path": dir_value,
                             "files": files,
                             "size": sum(sizes) if sizes else None,
                             "updated_at": max(updated) if updated else None})
    return {"dir": dir_value, "datasets": datasets}


# ---------------------------------------------------------------------------
# §3.11.2 清洗后数据（已发布，远端发布区）
# ---------------------------------------------------------------------------

def list_published(client):
    """列远端发布区里的数据版本（`list_published_versions`）。

    出口形状保持不变：`{"datasets":[{data_version, source_task_id, path,
    tables:[{name,file,size}], published_at}]}`。远端排序已给出，原样保留；
    "读不到发布区"与"发布区是空的"由远端分开，这里只如实转述。
    """
    env = _remote(client, "list_published_versions")
    datasets = []
    for item in env.get("versions") or []:
        if not isinstance(item, dict):
            continue
        tables = []
        for table in item.get("tables") or []:
            if not isinstance(table, dict):
                continue
            tables.append({"name": table.get("name"),
                           "file": table.get("file"),
                           "size": table.get("size")})
        datasets.append({"data_version": item.get("data_version"),
                         "source_task_id": item.get("source_task_id"),
                         "path": item.get("path"),
                         "tables": tables,
                         "published_at": item.get("published_at")})
    return {"datasets": datasets}


# ---------------------------------------------------------------------------
# §3.11.3 方案文件（清洗规则 / 评分方案）
# ---------------------------------------------------------------------------

def list_schemes(client):
    """列已登记方案（远端 `list_schemes`）。

    前端接受 `schemes` / `items` / `list` / `data` 任一种键，因此这里保持宽松：
    远端给哪个键就带上哪个键，一个都没有时回一个空 `schemes`，不臆造条目。
    """
    env = _remote(client, "list_schemes")
    out = {}
    for key in ("schemes", "items", "list", "data"):
        if key in env:
            out[key] = env[key]
    if not out:
        out["schemes"] = []
    return out


def scheme_content(client, name):
    """取方案完整内容（远端 `get_scheme_content`；`GET /api/schemes/content?name=`）。

    出口形状保持不变：`{"name", "path", "content"}`。文件不在 / 内容不合法由远端
    如实给出，这里原样转述。
    """
    if name is None or str(name).strip() == "":
        raise ResourceError("USAGE", "缺少参数 name")
    env = _remote(client, "get_scheme_content", {"name": str(name)})
    return {"name": env.get("name"),
            "path": env.get("path"),
            "content": env.get("content")}


def upload_scheme(client, name, content_text, scheme_type=None, overwrite=False):
    """`POST /api/schemes/upload`：上传自定义方案并登记落盘。

    有远端客户端时经远端 `upload_scheme` 转发（`content` 为方案 JSON 文本；`type`
    仅在给出时上送、`overwrite` 仅在开启时上送）；失败如实转述远端错误码与说明。
    没有客户端（离线 / 单测）时退回**本机落盘 + 本地校验**，出口形状保持一致。

    出口形状：`{"saved": {name, path}, "validation", "scheme", "overwritten",
    "size_bytes"}`（后三项远端不提供时为 None）。
    """
    if name is None or str(name).strip() == "":
        raise ResourceError("USAGE", "缺少方案文件名")
    filename = _sanitize_component(name, "")
    if not filename:
        raise ResourceError("USAGE", "缺少方案文件名")
    if _ext(filename) not in SCHEME_EXTS:
        raise ResourceError("UNSUPPORTED_FILE", "方案文件必须是 .json：%s" % filename)

    text = "" if content_text is None else str(content_text)
    if client is None:
        return _save_scheme_local(filename, text)

    args = {"name": filename, "content": text}
    if scheme_type:
        args["type"] = scheme_type
    if overwrite:
        args["overwrite"] = True
    env = _remote(client, "upload_scheme", args)
    scheme = env.get("scheme") if isinstance(env.get("scheme"), dict) else {}
    return {"saved": {"name": filename,
                      "path": scheme.get("path") or ("config/" + filename)},
            "validation": env.get("validation"),
            "scheme": env.get("scheme"),
            "overwritten": env.get("overwritten"),
            "size_bytes": env.get("size_bytes")}


def _save_scheme_local(filename, content_text):
    """本机落盘回退：写 config/ 后复用现有校验，回传 validation（不含远端字段）。"""
    data = content_text.encode("utf-8")
    if len(data) > MAX_SCHEME_UPLOAD_BYTES:
        raise ResourceError("UPLOAD_TOO_LARGE",
                            "方案文件超过上限 %d 字节：%s"
                            % (MAX_SCHEME_UPLOAD_BYTES, filename))
    path = _resolve(config_root(), filename)
    _ensure_dir(config_root())
    with open(path, "wb") as fh:
        fh.write(data)
    return {"saved": {"name": filename, "path": "config/" + filename},
            "validation": _validate_saved(path)}


def _validate_saved(path):
    """复用现有校验：按 config_type 决定放进 rules 侧还是 scoring 侧。"""
    from . import tools                                   # 延迟导入，避免重依赖
    try:
        with open(path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (ValueError, OSError):
        cfg = None
    ctype = cfg.get("config_type") if isinstance(cfg, dict) else None
    rel = os.path.relpath(path, REPO_ROOT).replace(os.sep, "/")
    if ctype == "scoring_scheme":
        return tools.validate_config(rules=None, scoring=rel)
    return tools.validate_config(rules=rel, scoring=None)


# ---------------------------------------------------------------------------
# §3.11.4 产出文件
# ---------------------------------------------------------------------------

def kind_of(name):
    """按扩展名粗分：json / md / dat / text / other（§3.11.4）。"""
    ext = _ext(name)
    if ext == ".json":
        return "json"
    if ext == ".md":
        return "md"
    if ext == ".dat":
        return "dat"
    if ext in (".txt", ".log", ".csv", ".tsv", ".yaml", ".yml",
               ".ini", ".cfg", ".conf", ".jsonl"):
        return "text"
    return "other"


def task_dir(task_id):
    """任务产物目录（白名单校验：必须在 tasks_root 之下且真实存在）。"""
    if not task_id:
        raise ResourceError("USAGE", "缺少 task_id")
    tid = _sanitize_component(task_id, "")
    if not tid:
        raise ResourceError("USAGE", "任务编号非法：%r" % task_id)
    path = _resolve(tasks_root(), tid)
    if not os.path.isdir(path):
        raise ResourceError("FILE_NOT_FOUND", "任务目录不存在：%s" % task_id)
    return path


def local_list_task_files(task_id):
    """列本机任务目录下的全部文件（`path` / `size` / `kind`）。

    保留本机扫描仅供**本机**用途（`runtime/artifacts_ref` 收集对话可见产物），
    对外端点一律走远端 `list_task_files`。两处形状一致，便于对照。
    """
    base = task_dir(task_id)
    files = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames.sort()
        for fn in sorted(filenames):
            fp = os.path.join(dirpath, fn)
            st = _stat(fp)
            if st is None or not os.path.isfile(fp):
                continue
            files.append({"path": _rel(fp, base), "size": st.st_size,
                          "kind": kind_of(fn)})
    return {"task_id": task_id, "files": files}


def list_task_files(client, task_id):
    """列远端任务产物文件（`list_task_files`；`tid` 为 hadoop_task_id）。

    出口形状保持不变：`{"task_id", "files":[{path,size,kind}]}`。
    任务不存在 / 编号非法由远端如实给出，这里原样转述。
    """
    if not task_id or not str(task_id).strip():
        raise ResourceError("USAGE", "缺少 task_id")
    env = _remote(client, "list_task_files", {"task_id": str(task_id)})
    files = []
    for entry in env.get("files") or []:
        if not isinstance(entry, dict):
            continue
        files.append({"path": entry.get("path"),
                      "size": entry.get("size"),
                      "kind": entry.get("kind")})
    return {"task_id": env.get("task_id", str(task_id)), "files": files}


def resolve_file(task_id, relpath):
    """把 task_id + path 解析成任务目录内的真实文件（越界 → FORBIDDEN_PATH）。

    仅供**本机**用途（如 `runtime/artifacts_ref` 据任务编号回收产物）；对外下载已改
    为经远端只读路由带口令流式中转（见 `agent/mcp_download.py`）。
    """
    base = task_dir(task_id)
    if relpath is None or str(relpath).strip() == "":
        raise ResourceError("USAGE", "缺少参数 path")
    target = _resolve(base, relpath, must_exist=True)
    if not os.path.isfile(target):
        raise ResourceError("FILE_NOT_FOUND", "文件不存在：%s" % relpath)
    return target


def preview_file(client, task_id, relpath):
    """预览远端任务产物（`read_task_file`；`task_id` 为 hadoop_task_id）。

    出口形状保持不变：`{"path", "previewable", "kind", "size"?, "content"?,
    "truncated"?}`。二进制/其它类型由远端回 `previewable:false`，不返回内容；
    文件不在、路径越界、内容过大一律原样转述远端错误。
    """
    if not task_id or not str(task_id).strip():
        raise ResourceError("USAGE", "缺少 task_id")
    if relpath is None or str(relpath).strip() == "":
        raise ResourceError("USAGE", "缺少参数 path")
    env = _remote(client, "read_task_file",
                  {"task_id": str(task_id), "path": str(relpath)})
    out = {"path": env.get("path", str(relpath)),
           "previewable": bool(env.get("previewable")),
           "kind": env.get("kind")}
    for key in ("size", "content", "truncated"):
        if key in env:
            out[key] = env[key]
    return out
