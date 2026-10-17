# -*- coding: utf-8 -*-
"""接口文档 §3.11「资源与文件接口（直通，不经 LLM）」的文件系统落地层。

只搬文件、列目录、回内容：**不产生任何业务数字，也不调用任何模型**
（用户明确要求「上传数据不该让 LLM 插手」）。

http_api.py 只做路由与序列化，真正的文件系统逻辑都收在这里，便于单测与后续
在接入 Hadoop/MCP 时把「已发布列举」换成远程实现而**对外形状不变**。
原始数据上传已下线（远端能力服务不对外提供上传），本机只做方案上传与只读列举。

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
    """任务产物根目录（与 driver 同口径：ML_VAR_DIR 或 <repo>/var，再 /tasks）。"""
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
# §3.11.1 原始数据
# ---------------------------------------------------------------------------

def _dataset_entry(root, name):
    full = os.path.join(root, name)
    files = []
    total = 0
    newest = 0.0
    if os.path.isdir(full):
        for dirpath, dirnames, filenames in os.walk(full):
            dirnames.sort()
            for fn in sorted(filenames):
                fp = os.path.join(dirpath, fn)
                st = _stat(fp)
                if st is None:
                    continue
                files.append({"name": _rel(fp, full),
                              "size": st.st_size,
                              "updated_at": st.st_mtime})
                total += st.st_size
                newest = max(newest, st.st_mtime)
    else:
        st = _stat(full)
        if st is None:
            return None
        files.append({"name": name, "size": st.st_size, "updated_at": st.st_mtime})
        total = st.st_size
        newest = st.st_mtime
    return {"name": name, "path": name, "files": files,
            "size": total, "updated_at": newest}


def list_raw():
    """列原始区数据集（文件夹/文件）。没有则返回空数组，不造样例。"""
    root = raw_root()
    datasets = []
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            entry = _dataset_entry(root, name)
            if entry is not None:
                datasets.append(entry)
    return {"dir": root, "datasets": datasets}


# ---------------------------------------------------------------------------
# §3.11.2 清洗后数据（已发布）
# ---------------------------------------------------------------------------

def list_published():
    """扫描任务产物 `var/tasks/*/cleaned/<数据版本>/`，列已发布数据版本。

    没有已发布数据时返回空数组，**不造样例**。接入 Hadoop 后改读发布区
    `/data/published/`，对外形状不变。
    """
    root = tasks_root()
    datasets = []
    if os.path.isdir(root):
        for tid in sorted(os.listdir(root)):
            cleaned = os.path.join(root, tid, "cleaned")
            if not os.path.isdir(cleaned):
                continue
            for version in sorted(os.listdir(cleaned)):
                vdir = os.path.join(cleaned, version)
                if not os.path.isdir(vdir):
                    continue
                tables = []
                newest = 0.0
                for fn in sorted(os.listdir(vdir)):
                    fp = os.path.join(vdir, fn)
                    st = _stat(fp)
                    if st is None or not os.path.isfile(fp):
                        continue
                    tables.append({"name": os.path.splitext(fn)[0],
                                   "file": fn, "size": st.st_size})
                    newest = max(newest, st.st_mtime)
                dst = _stat(vdir)
                published_at = dst.st_mtime if dst else newest
                datasets.append({"data_version": version, "source_task_id": tid,
                                 "path": vdir.replace(os.sep, "/"),
                                 "tables": tables, "published_at": published_at})
    datasets.sort(key=lambda d: d["published_at"], reverse=True)
    return {"datasets": datasets}


# ---------------------------------------------------------------------------
# §3.11.3 方案文件（清洗规则 / 评分方案）
# ---------------------------------------------------------------------------

def read_scheme_content(name):
    """取方案完整 JSON 内容（`GET /api/schemes/content?name=<文件名>`）。"""
    if name is None or str(name).strip() == "":
        raise ResourceError("USAGE", "缺少参数 name")
    # 先解析（越界 → FORBIDDEN_PATH），再判类型与存在性
    path = _resolve(config_root(), str(name))
    if _ext(path) not in SCHEME_EXTS:
        raise ResourceError("UNSUPPORTED_FILE", "方案文件名必须是 .json：%s" % name)
    if not os.path.isfile(path):
        raise ResourceError("FILE_NOT_FOUND", "方案文件不存在：%s" % name)
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    try:
        content = json.loads(text)
    except ValueError:
        raise ResourceError("UNSUPPORTED_FILE", "方案文件不是合法 JSON：%s" % name)
    return {"name": os.path.basename(path),
            "path": "config/" + os.path.basename(path),
            "content": content}


def save_scheme(parts):
    """`POST /api/schemes/upload`：落盘到 config/ 后复用现有校验，回传 validation。"""
    field = _field(parts, "file")
    files = _file_parts(parts, "file")
    if not files:
        raise ResourceError("USAGE", "缺少上传文件（字段 file）")
    part = files[0]

    name_field = _field(parts, "name")
    given_name = _decode_text(name_field["data"]).strip() if name_field else None
    filename = _sanitize_component(given_name or part.get("filename"), "")
    if not filename:
        raise ResourceError("USAGE", "缺少方案文件名")
    if _ext(filename) not in SCHEME_EXTS:
        raise ResourceError("UNSUPPORTED_FILE", "方案文件必须是 .json：%s" % filename)
    data = part.get("data") or b""
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


def list_task_files(task_id):
    """列任务目录下全部文件（`path` / `size` / `kind`）。"""
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


def resolve_file(task_id, relpath):
    """把 task_id + path 解析成任务目录内的真实文件（越界 → FORBIDDEN_PATH）。"""
    base = task_dir(task_id)
    if relpath is None or str(relpath).strip() == "":
        raise ResourceError("USAGE", "缺少参数 path")
    target = _resolve(base, relpath, must_exist=True)
    if not os.path.isfile(target):
        raise ResourceError("FILE_NOT_FOUND", "文件不存在：%s" % relpath)
    return target


def preview_file(task_id, relpath):
    """预览文本/JSON/Markdown（大小受限）；其余类型返回 previewable:false。"""
    target = resolve_file(task_id, relpath)
    kind = kind_of(target)
    if kind not in PREVIEWABLE_KINDS:
        return {"path": str(relpath), "previewable": False, "kind": kind}
    size = os.path.getsize(target)
    with open(target, "rb") as fh:
        raw = fh.read(MAX_PREVIEW_BYTES)
    out = {"path": str(relpath), "previewable": True, "kind": kind,
           "size": size, "content": raw.decode("utf-8", errors="replace")}
    if size > MAX_PREVIEW_BYTES:
        out["truncated"] = True
    return out


def download_target(task_id, relpath):
    """下载目标：返回 (绝对路径, 文件名)（http_api 负责写 Content-Disposition）。"""
    target = resolve_file(task_id, relpath)
    return target, os.path.basename(target)
