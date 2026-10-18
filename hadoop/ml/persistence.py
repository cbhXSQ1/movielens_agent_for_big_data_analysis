# -*- coding: utf-8 -*-
"""ml/persistence.py —— 模型与产物的落盘、索引登记、产物清单。

落盘纪律：

  * 一律**先写临时文件再原子改名**：任何一步失败都不会留下半个文件；
  * 产物清单在**文件写完之后**生成，`size` 是磁盘上的真实字节数（不是估算）；
  * 索引与元数据用同一份值：`list_models` 读索引、`get_model` 读 sidecar，两者不会互相矛盾。

目录约定（实现方案 §7）：模型目录下放模型本体、元数据、群体画像、每用户标签、用户坐标；
索引在模型根目录的 `index.json`。
"""

import hashlib
import json
import os

from . import config
from .errors import TrainingError

#: 产物角色 → 中文标题（前端按 `role` 分组展示时直接用这个标题）。
ROLE_TITLES = {
    "model": "模型本体",
    "profile": "群体画像",
    "labels": "每用户群标签",
    "coords": "用户坐标",
    "meta": "模型元数据",
}

#: 文件扩展名 → 文件格式（产物条目的 `kind` 一律表示文件格式）。
KIND_BY_SUFFIX = {
    ".joblib": "joblib",
    ".json": "json",
    ".csv": "csv",
    ".jsonl": "jsonl",
    ".md": "md",
}


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def write_json(path, payload):
    """原子写 JSON（UTF-8、可读缩进）：先临时文件再改名。"""
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_joblib(path, payload):
    """原子写 joblib（模型本体与编码器一起打包）。"""
    import joblib

    tmp = path + ".part"
    joblib.dump(payload, tmp)
    os.replace(tmp, path)
    return path


def read_joblib(path):
    import joblib

    return joblib.load(path)


#: 读模型包（与 `read_joblib` 同义；名字更贴近用途，供预测与取模型处使用）。
read_bundle = read_joblib


def write_csv(path, header, rows):
    """原子写 CSV（UTF-8）。"""
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(",".join(header) + "\n")
        for row in rows:
            fh.write(",".join(str(cell) for cell in row) + "\n")
    os.replace(tmp, path)
    return path


def version_fingerprint(data_version, algorithm, params):
    """版本指纹：数据版本 + 算法 + 生效参数的摘要（同一份模型可据此去重与追溯）。"""
    payload = json.dumps({"data_version": data_version, "algorithm": algorithm,
                          "params": params}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def artifact_entry(model_id, model_dir, relpath, role):
    """按磁盘上的真实文件生成一条产物条目（size 用真实字节数）。"""
    full = os.path.join(model_dir, relpath)
    if not os.path.isfile(full):
        raise TrainingError("TASK_FAILED", "产物文件没有落盘：%s" % relpath,
                            missing=relpath, reason="artifact_missing")
    suffix = os.path.splitext(relpath)[1].lower()
    return {
        "id": "%s:%s" % (model_id, relpath),
        "title": ROLE_TITLES.get(role, relpath),
        "kind": KIND_BY_SUFFIX.get(suffix, "other"),
        "role": role,
        "path": relpath,
        "size": int(os.path.getsize(full)),
    }


def collect_artifacts(model_id, model_dir, entries):
    """`entries` 是 (相对路径, 角色) 的序列；返回按路径排序的产物清单。"""
    artifacts = [artifact_entry(model_id, model_dir, relpath, role)
                 for relpath, role in entries]
    return sorted(artifacts, key=lambda item: item["path"])


def update_index(repo_root, entry):
    """把一条模型记录写进索引（同编号覆盖，其余保留；按创建时间倒序）。"""
    root = os.path.join(repo_root, config.ML_ROOT_REL)
    ensure_dir(root)
    path = os.path.join(root, config.INDEX_NAME)
    entries = []
    if os.path.isfile(path):
        try:
            payload = read_json(path)
            if isinstance(payload, list):
                entries = [item for item in payload if isinstance(item, dict)]
        except (OSError, ValueError):
            entries = []          # 索引坏了就重建，不因此让训练失败
    others = [item for item in entries if item.get("model_id") != entry.get("model_id")]
    others.append(entry)
    others.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    write_json(path, others)
    return others


def read_index(repo_root):
    path = os.path.join(repo_root, config.ML_ROOT_REL, config.INDEX_NAME)
    if not os.path.isfile(path):
        return []
    try:
        payload = read_json(path)
    except (OSError, ValueError):
        raise TrainingError(
            "TASK_FAILED", "模型索引文件损坏（%s），无法列出模型" % path,
            missing=config.INDEX_NAME, reason="index_corrupt")
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def drop_tmp_files(directory):
    """清理目录里遗留的 `.part` 临时文件（异常退出时可能留下）。"""
    try:
        for name in os.listdir(directory):
            if name.endswith(".part"):
                try:
                    os.remove(os.path.join(directory, name))
                except OSError:
                    pass
    except OSError:
        pass
