# -*- coding: utf-8 -*-
"""命令行入口：python3 -m agent <工具> [选项]（等价于 python3 -m agent.cli）。"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
