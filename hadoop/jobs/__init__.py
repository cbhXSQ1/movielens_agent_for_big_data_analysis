# -*- coding: utf-8 -*-
"""jobs —— Hadoop Streaming 入口（map/reduce 薄壳）。

约定（plan.md §4.6）：
  * 所有作业显式使用 ISO-8859-1 读写标准输入输出，避免节点 locale 影响
  * 计数器通过 stderr 的 `reporter:counter:group,name,n` 协议上报
  * 引擎与配置由 driver 打包为 engine.zip，用 -files 分发；脚本启动时
    `sys.path.insert(0, "engine.zip")` 即可 zipimport

作业清单见 plan.md §5.1（清洗）与 §5.2（评分）。
"""

__all__ = ["_common"]
