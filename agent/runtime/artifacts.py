# -*- coding: utf-8 -*-
"""阶段 3a（ag-runtime）· T3.1：大数据本体外置存放。

阶段 0 结论文档 §5.2 的关键约束：**LangGraph 检查点状态必须可序列化**，状态里
放不可序列化的大对象（DataFrame / 连接 / 文件句柄）会报错或撑爆存储。因此：

    状态里只放**引用**（ref 字符串）；本模块负责把大数据本体落到磁盘文件。

工具返回的信封超过阈值时，运行时不把它塞进检查点，而是：
    ① ArtifactStore.save(payload) → 写一个 JSON 文件 → 返回 ref；
    ② 状态里只记 ref + 一句摘要。
需要时（数字护栏重建可溯源索引）再用 ArtifactStore.load(ref) 读回。

文件放在被忽略的运行期目录（默认 `agent/var/artifacts/`），不提交。
"""

import json
import os
import threading
import uuid


class ArtifactStore(object):
    """把大数据本体存成磁盘 JSON 文件，用短引用（ref）指代。"""

    def __init__(self, root_dir):
        self.root_dir = root_dir
        os.makedirs(self.root_dir, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, ref):
        return os.path.join(self.root_dir, "%s.json" % ref)

    def save(self, payload, kind="json"):
        """存一份 payload，返回引用串（形如 `art-<uuid>`）。

        kind 仅作命名提示（写进文件内的 envelope），不参与寻址。
        """
        ref = "art-%s" % uuid.uuid4().hex
        record = {"kind": kind, "payload": payload}
        blob = json.dumps(record, ensure_ascii=False)
        with self._lock:
            with open(self._path(ref), "w", encoding="utf-8") as fh:
                fh.write(blob)
        return ref

    def load(self, ref):
        """读回 ref 指向的 payload；文件缺失或损坏时返回 None（不抛）。"""
        if not ref:
            return None
        try:
            with self._lock:
                with open(self._path(ref), "r", encoding="utf-8") as fh:
                    record = json.load(fh)
        except (OSError, ValueError):
            return None
        if isinstance(record, dict) and "payload" in record:
            return record["payload"]
        return record

    def size_of(self, ref):
        """ref 对应文件的字节数（排错用）；读不到返回 -1。"""
        try:
            return os.path.getsize(self._path(ref))
        except OSError:
            return -1
