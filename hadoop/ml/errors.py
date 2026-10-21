# -*- coding: utf-8 -*-
"""ml/errors.py —— 机器学习核心的结构化异常。

服务层只认这个异常：它带一个与对外错误码同名的 `code`、一句可以直接讲给用户听的 `message`，
以及若干定位字段（`extra`）。这样服务层不必理解机器学习内部，也能把失败如实转成统一信封。

为什么单独一个模块而不是放在 api 里：各实现模块（取数、特征、训练、落盘）都要抛它，
而它们不该反向依赖 api（否则 api 与被它调用的模块互相 import，容易踩循环导入）。
"""


class TrainingError(Exception):
    """结构化错误：`code` 取对外错误码（MODEL_NOT_FOUND / DEPENDENCY_MISSING / USAGE / TASK_FAILED）。"""

    def __init__(self, code, message, **extra):
        Exception.__init__(self, message)
        self.code = code
        self.message = message
        self.extra = dict(extra)

    def as_dict(self):
        """排障与测试用的字典形态（不直接对外，服务层会重新封装成信封）。"""
        out = {"code": self.code, "message": self.message}
        out.update(self.extra)
        return out
