# -*- coding: utf-8 -*-
"""MCP 取证与产物读取（`hadoop/mcpserver/readers.py`）单元测试。

覆盖范围（对应 `docs/hadoop/迭代一重构_MCP_实现方案.md` §3.2 / §5 / §6 / §10 / §11
与 `docs/agent/Agent_接口文档.md` §3.11.4 / §3.11.5）：

  * 借现有命令的五个能力（经统一调用通道）：状态、结果、样本、报告、任务列表 ——
    信封字段回传，任务不存在、任务未完成、任务失败这三个码原样上抛（不改写、不另算）；
  * 状态查询的两处增补：①从状态文件补任务类型、数据范围与标签，取不到就不给；
    ②四类版本与两个时间边界优先取元数据文件，缺失时仅对**成功任务**从结果文件取，
    并一律标出 `versions_source`；两处都没有就不出现这些字段；
  * 结果里的产物位置只给"任务编号 + 相对路径"：任务目录那个绝对位置去掉，
    任务目录内的路径转成相对路径，分布式文件系统上的位置原样保留；
  * 结果的产物清单（`artifacts`）与「列任务产物文件」一一对应：同样的排除规则、
    同样的排序，每条给出编号、标题、类别、类型、相对路径与大小；类别里计数、统计、
    结果、状态这类单独归"元数据"，免得只看"数据/报告"的界面把它们一起藏掉；
  * 自己读文件的五个能力：计数、日志、产物清单、读产物、已发布版本；
  * 红线：越界路径拒绝、超过大小上限报错、二进制只回不可预览、缺文件如实报错、
    产物坏掉报"内容不合法"（与"文件不存在"分开）、发布区读不到必须报错而不是回空列表。

测试用临时目录造一个假任务目录（`status.json` / `metadata.json` / `counts.json` /
`report.md` / `metrics/` / `cleaned/<版本>/` / `logs/` 以及一个二进制文件与几条
越界路径），不碰仓库里的 `var/`。集群一侧的行为用两条路子验证：一条是真实的集群
命令指向一个没人监听的地址（集群未启动的真实表现），一条是假的命令脚本（稳定地
造出"读得到、读不到、空目录"三种局面）。

运行方式（仓库根执行）：
    PYTHONPATH=hadoop python3 -m unittest hadoop/tests/test_mcp_readers.py -v
"""

import io
import json
import os
import shutil
import signal
import sys
import tempfile
import time
import unittest
from unittest import mock

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
if os.path.join(REPO_ROOT, "hadoop") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "hadoop"))

from mcpserver import dispatch, readers, tools      # noqa: E402
from mcpserver.contract import StructuredError      # noqa: E402
from mcpserver.dispatch import ToolContext          # noqa: E402

#: 这一个假任务什么都有（元数据、计数、报告、指标、清洗产物、隔离产物、日志）。
TASK_FULL = "fixture-full-01"
#: 这个假任务只有状态文件：没有元数据、没有计数、没有日志。
TASK_BARE = "fixture-bare-02"
#: 这个假任务只有状态与结果（用来验证计数从结果文件回退）。
TASK_RESULT_ONLY = "fixture-result-03"
#: 这个假任务是运行中（结果类能力要拒绝）。
TASK_RUNNING = "fixture-running-04"
#: 这个假任务是失败（结果类能力要把失败原因原样带回）。
TASK_FAILED = "fixture-failed-05"
#: 这个假任务只有执行日志、没有各作业日志（用来验证"某一侧缺席"会被如实标注）。
TASK_DRIVER_LOG_ONLY = "fixture-driverlog-06"
#: 这个假任务的计数文件是坏 JSON（验证坏产物如实报错，而不是静默给空）。
TASK_CORRUPT = "fixture-corrupt-07"
#: 这个假任务是失败状态，却留着一次结果文件（版本不该从失败任务的结果里取）。
TASK_FAILED_WITH_RESULT = "fixture-failed-result-08"
#: 这个假任务的元数据文件是坏的、结果文件是好的（验证带出处地回退）。
TASK_METADATA_CORRUPT = "fixture-corrupt-meta-09"
#: 这个假任务的元数据文件在、但里面没有版本字段（验证"取不出"要被说明）。
TASK_METADATA_EMPTY = "fixture-empty-meta-10"
#: 这个假任务的结构化报告是坏 JSON，但别的产物齐全（验证报告预检与它的边界）。
TASK_REPORT_CORRUPT = "fixture-report-bad-11"
#: 这个假任务的结构化报告是合法 JSON、却不是预期的对象结构。
TASK_REPORT_SHAPE = "fixture-report-shape-12"
#: 这个假任务是**已中止**的终态（取结果要给单独一类错误，不是"任务未完成"）。
TASK_ABORTED = "fixture-aborted-13"
#: 这个假任务的两种报告都超过大小上限（取报告要先挡住，别把几十兆拉回来）。
TASK_BIG_REPORT = "fixture-bigreport-14"
#: 这个假任务的产物位置上放的是**命名管道**：读之前必须先判普通文件。
TASK_PIPE = "fixture-pipe-15"
#: 失败记录里带结构化码（发布被版本保护拦下）：取结果要给出"版本冲突"而不是"任务失败"。
TASK_FAILED_WITH_CODE = "fixture-failed-code-17"
#: 失败记录里的码不是我们契约里的名字：必须原样带出，不许丢弃或改写。
TASK_FAILED_ALIEN_CODE = "fixture-failed-alien-18"
#: 失败记录里用另一个字段名承载码（执行侧将来改名也能取到）。
TASK_FAILED_ALT_KEY = "fixture-failed-alt-19"

DATA_VERSION = "fixture-v1"
CLEANED_RATINGS = "cleaned/%s/ratings.dat" % DATA_VERSION

#: 越界的目标文件放在任务目录之外（同一临时根下），用来验证符号链接逃逸会被拒。
OUTSIDE_NAME = "outside-secret.json"


def write_text(path, text):
    """写一个 UTF-8 文本文件，必要时建目录。"""
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with io.open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def write_json(path, payload):
    write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + u"\n")


def write_bytes(path, data):
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with io.open(path, "wb") as handle:
        handle.write(data)


def read_json(path):
    with io.open(path, encoding="utf-8") as handle:
        return json.load(handle)


class ReaderCase(unittest.TestCase):
    """公共脚手架：一个临时产物根 + 一批假任务目录 + 一个带真值环境的上下文。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="mcp-readers-")
        cls.tasks_root = os.path.join(cls.tmp, "tasks")
        cls.outside = os.path.join(cls.tmp, OUTSIDE_NAME)
        write_json(cls.outside, {"secret": "任务目录之外的文件"})
        cls._build_fixtures()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # -- 造数 ---------------------------------------------------------------

    @classmethod
    def _task_path(cls, task_id, *parts):
        return os.path.join(cls.tasks_root, task_id, *parts)

    @classmethod
    def _build_fixtures(cls):
        status_ok = {
            "task_id": TASK_FULL, "status": "succeeded", "stage": "done",
            "stage_index": 9, "stage_total": 9, "progress_percent": 100,
            "message": "done", "started_at": "2026-10-01T00:00:00Z",
            "updated_at": "2026-10-01T00:10:00Z", "errors": [],
            "data_version": DATA_VERSION,
            # 状态文件里真实存在、但既有命令不回的三项
            "task_type": "full", "scope": "full", "tag": "fixture-tag",
            # 状态文件里还存着方案文件的绝对位置：不该被带出去
            "rules": "/home/nobody/config/cleaning_rules.v1.json",
            "scoring": "/home/nobody/config/scoring_scheme.v1.json",
        }
        counts = {
            "input": {"users_lines": 10, "movies_lines": 10, "ratings_lines": 10},
            "output": {"users": 8, "movies": 8, "ratings": 8},
            "quarantine": {"total": 2, "by_rule": {"R2": 2}},
            "dedupe": {"users": 1, "movies": 1, "ratings": 2},
            "fix": {"R4_ms": 3},
        }

        # ---- 1) 什么都有的任务 ----
        write_json(cls._task_path(TASK_FULL, "status.json"), status_ok)
        write_json(cls._task_path(TASK_FULL, "metadata.json"), {
            "task_id": TASK_FULL, "data_version": DATA_VERSION,
            "rule_version": "1.0.0", "rule_hash": "aa" * 32,
            "scoring_scheme_version": "1.0.0", "scoring_hash": "bb" * 32,
            "policy_version": "policy-1",
            "T1": "2001-12-31T23:59:59Z", "T2": "2002-06-30T23:59:59Z",
            "input_counts": counts["input"], "output_counts": counts["output"],
            "paths": {"task_dir": "/home/nobody/should/not/leak"},
        })
        write_json(cls._task_path(TASK_FULL, "counts.json"), counts)
        write_json(cls._task_path(TASK_FULL, "stats.json"), {"task_id": TASK_FULL})
        write_json(cls._task_path(TASK_FULL, "published_hashes.json"),
                   {"ratings": "aa" * 4})
        full_dir = cls._task_path(TASK_FULL)
        write_json(cls._task_path(TASK_FULL, "result.json"), {
            "status": "succeeded", "data_version": DATA_VERSION,
            "versions": {"rule": {"version": "1.0.0", "sha256": "aa" * 32},
                         "scoring": {"version": "1.0.0", "sha256": "bb" * 32},
                         "policy": {"sha256": "cc" * 32},
                         "operator_library": "fixture-ops.v1"},
            "time_boundaries": {"T1": "2001-12-31T23:59:59Z",
                                "T2": "2002-06-30T23:59:59Z"},
            "counts": counts,
            # 任务目录内的一律是机器上的绝对位置；发布区那条是分布式位置
            "paths": {
                "task_dir": full_dir,
                "cleaned_dir": os.path.join(full_dir, "cleaned", DATA_VERSION),
                "quarantine_dir": os.path.join(full_dir, "quarantine", DATA_VERSION),
                "metrics_dir": os.path.join(full_dir, "metrics"),
                "report_md": os.path.join(full_dir, "report.md"),
                "report_json": os.path.join(full_dir, "report.json"),
                "published_dir": "/data/published/%s" % DATA_VERSION,
            },
        })
        write_text(cls._task_path(TASK_FULL, "report.md"), u"# 假报告\n\n这是测试用的报告正文。\n")
        write_json(cls._task_path(TASK_FULL, "report.json"), {"task_id": TASK_FULL})
        write_text(cls._task_path(TASK_FULL, "dims", "users_dim.jsonl"), u'{"id": 1}\n')
        write_json(cls._task_path(TASK_FULL, "metrics", "before.json"), {"composite": 1.0})
        write_json(cls._task_path(TASK_FULL, "metrics", "after.json"), {"composite": 2.0})
        write_json(cls._task_path(TASK_FULL, "metrics", "composite.json"),
                   {"delta": {"composite": 1.0}})
        for table, name in (("users", "users.dat"), ("movies", "movies.dat"),
                            ("ratings", "ratings.dat")):
            write_text(cls._task_path(TASK_FULL, "cleaned", DATA_VERSION, name),
                       u"1::2::3::4::5\n6::7::8::9::10\n11::12::13::14::15\n")
        write_text(cls._task_path(TASK_FULL, "quarantine", DATA_VERSION, "ratings.dat"),
                   u'{"line_no": 1, "raw_line": "bad", "rule_id": "R2", '
                   u'"stage": "parse", "reason": "假隔离记录"}\n')
        write_text(cls._task_path(TASK_FULL, "driver.log"),
                   u"".join(u"stage-%02d\n" % index for index in range(1, 31)))
        write_text(cls._task_path(TASK_FULL, "logs", "clean_users..log"),
                   u"".join(u"作业行 %02d\n" % index for index in range(1, 13)))
        write_text(cls._task_path(TASK_FULL, "logs", "fetch_ratings.dat.log"), u"取回产物成功\n")
        # 一个校验和残留文件：不该被当成作业日志
        write_bytes(cls._task_path(TASK_FULL, "logs", ".quarantine_ratings.tmp.raw.crc"),
                    b"\x00\x01\x02\x03")
        # 服务自己的控制文件：不是产物，既不该列进清单，也不该读得到
        write_json(cls._task_path(TASK_FULL, "cancel_request.json"),
                   {"requested_at": "2026-10-01T00:05:00Z", "by": "internal"})
        write_json(cls._task_path(TASK_FULL, "mcp_submit.json"),
                   {"submitted_at": "2026-10-01T00:00:01Z", "pid": 12345})
        # 子目录里的同名控制文件同样算控制文件（按文件名判断，任意层级）
        write_json(cls._task_path(TASK_FULL, "metrics", "cancel_request.json"), {"x": 1})
        # 文件系统层面的附属物：隐藏文件与校验和附属文件，都不该出现在清单里
        write_json(cls._task_path(TASK_FULL, ".hidden.json"), {"internal": True})
        write_bytes(cls._task_path(TASK_FULL, "cleaned", DATA_VERSION,
                                  ".ratings.dat.raw.crc"), b"\x00\x01")
        write_bytes(cls._task_path(TASK_FULL, "metrics", ".after.json.raw.crc"), b"\x02\x03")
        # 不以点开头、但后缀是校验和附属的那一类（验证后缀规则单独成立）
        write_bytes(cls._task_path(TASK_FULL, "counts.json.crc"), b"\x04\x05")
        # 流程中途的临时残留：发布环节准备上传的中间物，以及一个分片半成品
        write_text(cls._task_path(TASK_FULL, "logs", "published_hashes.tmp"), u"{}\n")
        write_bytes(cls._task_path(TASK_FULL, "metrics", "composite.json.part"), b"\x06\x07")
        # 二进制与超大文本各一份
        write_bytes(cls._task_path(TASK_FULL, "blob.bin"),
                    bytes(bytearray(range(256))) * 4)
        write_text(cls._task_path(TASK_FULL, "big.json"),
                   json.dumps({"filler": "x" * 4096}) + u"\n")
        # 溢出到任务目录之外的符号链接（不支持符号链接的系统上跳过相关断言）
        link = cls._task_path(TASK_FULL, "escape.json")
        try:
            os.symlink(cls.outside, link)
            cls.has_symlink = True
        except (OSError, NotImplementedError, AttributeError):
            cls.has_symlink = False
        # 换个名字指向控制文件：真实路径仍然指向控制文件，不许借此绕过去读
        disguise = cls._task_path(TASK_FULL, "looks-normal.json")
        try:
            os.symlink(cls._task_path(TASK_FULL, "cancel_request.json"), disguise)
            cls.has_control_link = True
        except (OSError, NotImplementedError, AttributeError):
            cls.has_control_link = False

        # ---- 2) 只有状态文件的任务 ----
        write_json(cls._task_path(TASK_BARE, "status.json"), {
            "task_id": TASK_BARE, "status": "succeeded", "stage": "done",
            "stage_index": 9, "stage_total": 9, "progress_percent": 100,
            "started_at": "2026-10-01T01:00:00Z",
            "updated_at": "2026-10-01T01:01:00Z", "errors": [],
        })

        # ---- 3) 只有状态与结果的任务（计数从结果文件回退）----
        write_json(cls._task_path(TASK_RESULT_ONLY, "status.json"), {
            "task_id": TASK_RESULT_ONLY, "status": "succeeded", "stage": "done",
            "started_at": "2026-10-01T02:00:00Z", "updated_at": "2026-10-01T02:01:00Z",
            "errors": [],
        })
        result_only_dir = cls._task_path(TASK_RESULT_ONLY)
        write_json(cls._task_path(TASK_RESULT_ONLY, "result.json"), {
            "status": "succeeded", "counts": {"output": {"ratings": 3}},
            "data_version": DATA_VERSION,
            # 策略分组只有摘要、没有版本号：取的时候不能拿别的字段顶替
            "versions": {"rule": {"version": "2.0.0", "sha256": "dd" * 32},
                         "scoring": {"version": "2.0.0", "sha256": "ee" * 32},
                         "policy": {"sha256": "ff" * 32},
                         "operator_library": "fixture-ops.v2"},
            "time_boundaries": {"T1": "2001-12-31T23:59:59Z",
                                "T2": "2002-06-30T23:59:59Z"},
            "paths": {"task_dir": result_only_dir, "cleaned_dir": None},
        })

        # ---- 4) 运行中的任务 ----
        write_json(cls._task_path(TASK_RUNNING, "status.json"), {
            "task_id": TASK_RUNNING, "status": "running", "stage": "clean_ratings",
            "stage_index": 3, "stage_total": 9, "progress_percent": 33,
            "started_at": "2026-10-01T03:00:00Z",
            "updated_at": "2026-10-01T03:05:00Z", "errors": [],
        })

        # ---- 5) 失败的任务 ----
        write_json(cls._task_path(TASK_FAILED, "status.json"), {
            "task_id": TASK_FAILED, "status": "failed", "stage": "score_after",
            "stage_index": 6, "stage_total": 9, "progress_percent": 66,
            "started_at": "2026-10-01T04:00:00Z",
            "updated_at": "2026-10-01T04:05:00Z",
            "message": "假失败：作业退出码非零",
            "errors": [{"stage": "score_after", "job": "score_all", "exit_code": 1,
                        "message": "假失败：作业退出码非零"}],
        })

        # ---- 6) 只有执行日志的任务 ----
        write_json(cls._task_path(TASK_DRIVER_LOG_ONLY, "status.json"), {
            "task_id": TASK_DRIVER_LOG_ONLY, "status": "running", "stage": "queued",
            "started_at": "2026-10-01T05:00:00Z", "updated_at": "2026-10-01T05:00:01Z",
            "errors": [],
        })
        write_text(cls._task_path(TASK_DRIVER_LOG_ONLY, "driver.log"), u"stage queued\n")

        # ---- 7) 计数文件是坏 JSON 的任务 ----
        write_json(cls._task_path(TASK_CORRUPT, "status.json"), {
            "task_id": TASK_CORRUPT, "status": "succeeded", "stage": "done",
            "started_at": "2026-10-01T06:00:00Z", "updated_at": "2026-10-01T06:01:00Z",
            "errors": [],
        })
        write_text(cls._task_path(TASK_CORRUPT, "counts.json"), u"{\"input\": 这不合法")

        # ---- 8) 失败状态却留着结果文件的任务 ----
        write_json(cls._task_path(TASK_FAILED_WITH_RESULT, "status.json"), {
            "task_id": TASK_FAILED_WITH_RESULT, "status": "failed", "stage": "score_after",
            "started_at": "2026-10-01T07:00:00Z", "updated_at": "2026-10-01T07:05:00Z",
            "message": "假失败", "errors": [{"stage": "score_after", "message": "假失败"}],
        })
        write_json(cls._task_path(TASK_FAILED_WITH_RESULT, "result.json"), {
            "status": "succeeded", "data_version": "should-not-be-used",
            "versions": {"rule": {"version": "9.9.9"}},
            "time_boundaries": {"T1": "1999-01-01T00:00:00Z"},
        })

        # ---- 9) 元数据坏了、结果文件好着的任务 ----
        write_json(cls._task_path(TASK_METADATA_CORRUPT, "status.json"), {
            "task_id": TASK_METADATA_CORRUPT, "status": "succeeded", "stage": "done",
            "started_at": "2026-10-01T08:00:00Z", "updated_at": "2026-10-01T08:01:00Z",
            "errors": [],
        })
        write_text(cls._task_path(TASK_METADATA_CORRUPT, "metadata.json"), u"这不是 JSON")
        write_json(cls._task_path(TASK_METADATA_CORRUPT, "result.json"), {
            "status": "succeeded", "data_version": DATA_VERSION,
            "versions": {"rule": {"version": "3.0.0", "sha256": "11" * 32}},
        })

        # ---- 10) 元数据文件在、但没有版本字段的任务 ----
        write_json(cls._task_path(TASK_METADATA_EMPTY, "status.json"), {
            "task_id": TASK_METADATA_EMPTY, "status": "succeeded", "stage": "done",
            "started_at": "2026-10-01T09:00:00Z", "updated_at": "2026-10-01T09:01:00Z",
            "errors": [],
        })
        write_json(cls._task_path(TASK_METADATA_EMPTY, "metadata.json"),
                   {"task_id": TASK_METADATA_EMPTY,
                    "paths": {"task_dir": "/home/nobody/should/not/leak"}})

        # ---- 11) 结构化报告坏掉、别的产物齐全的任务 ----
        write_json(cls._task_path(TASK_REPORT_CORRUPT, "status.json"), {
            "task_id": TASK_REPORT_CORRUPT, "status": "succeeded", "stage": "done",
            "started_at": "2026-10-01T10:00:00Z", "updated_at": "2026-10-01T10:01:00Z",
            "errors": [],
        })
        write_text(cls._task_path(TASK_REPORT_CORRUPT, "report.json"),
                   u'{"task_id": "%s", "scores": {"composite": ' % TASK_REPORT_CORRUPT)
        write_json(cls._task_path(TASK_REPORT_CORRUPT, "counts.json"),
                   {"output": {"ratings": 3}})

        # ---- 12) 结构化报告是合法 JSON、但不是对象 ----
        write_json(cls._task_path(TASK_REPORT_SHAPE, "status.json"), {
            "task_id": TASK_REPORT_SHAPE, "status": "succeeded", "stage": "done",
            "started_at": "2026-10-01T11:00:00Z", "updated_at": "2026-10-01T11:01:00Z",
            "errors": [],
        })
        write_json(cls._task_path(TASK_REPORT_SHAPE, "report.json"), [])
        write_json(cls._task_path(TASK_METADATA_EMPTY, "result.json"), {
            "status": "succeeded", "data_version": DATA_VERSION,
            "versions": {"rule": {"version": "4.0.0"}},
        })

        # ---- 13) 已中止的任务：终态，取结果必须单独归类 ----
        write_json(cls._task_path(TASK_ABORTED, "status.json"), {
            "task_id": TASK_ABORTED, "status": "aborted", "stage": "clean_ratings",
            "stage_index": 3, "stage_total": 9, "progress_percent": 33,
            "started_at": "2026-10-01T12:00:00Z",
            "updated_at": "2026-10-01T12:04:00Z",
            "finished_at": "2026-10-01T12:04:00Z",
            "aborted_at": "2026-10-01T12:04:00Z",
            "message": "已按请求中止", "errors": [],
        })
        # 已中止的任务若留下过结果文件，也不该被取出来
        write_json(cls._task_path(TASK_ABORTED, "result.json"), {
            "status": "succeeded", "data_version": "should-not-be-used",
        })

        # ---- 14) 两种报告都超过大小上限的任务 ----
        write_json(cls._task_path(TASK_BIG_REPORT, "status.json"), {
            "task_id": TASK_BIG_REPORT, "status": "succeeded", "stage": "done",
            "started_at": "2026-10-01T13:00:00Z", "updated_at": "2026-10-01T13:01:00Z",
            "finished_at": "2026-10-01T13:01:00Z", "errors": [],
        })
        big_md = u"# 超大报告\n" + (u"填充行\n" * (readers.REPORT_MAX_BYTES // 6 + 16))
        write_text(cls._task_path(TASK_BIG_REPORT, "report.md"), big_md)
        write_text(cls._task_path(TASK_BIG_REPORT, "report.json"),
                   u"{\"filler\": \"" + (u"x" * (readers.REPORT_MAX_BYTES + 2048)) + u"\"}")

        # ---- 15) 产物位置上是命名管道（读之前必须先判普通文件，否则挂住）----
        write_json(cls._task_path(TASK_PIPE, "status.json"), {
            "task_id": TASK_PIPE, "status": "succeeded", "stage": "done",
            "started_at": "2026-10-01T14:00:00Z", "updated_at": "2026-10-01T14:01:00Z",
            "errors": [],
        })
        cls.has_fifo = hasattr(os, "mkfifo")
        if cls.has_fifo:
            try:
                for name in ("pipe.json", "counts.json", "report.json", "driver.log"):
                    os.mkfifo(cls._task_path(TASK_PIPE, name))
            except OSError:
                cls.has_fifo = False

        # ---- 17) 失败记录里带结构化错误码的三个任务 ----
        conflict_message = ("发布版本 %s 已存在且内容哈希不一致：配置或输入已变化，"
                            "必须升 data_version" % DATA_VERSION)
        write_json(cls._task_path(TASK_FAILED_WITH_CODE, "status.json"), {
            "task_id": TASK_FAILED_WITH_CODE, "status": "failed", "stage": "publish",
            "stage_index": 8, "stage_total": 9, "progress_percent": 89,
            "started_at": "2026-10-01T16:00:00Z",
            "updated_at": "2026-10-01T16:30:00Z",
            "finished_at": "2026-10-01T16:30:00Z",
            "message": conflict_message,
            "errors": [{"stage": "publish", "message": conflict_message,
                        "code": "VERSION_CONFLICT"}],
        })
        write_json(cls._task_path(TASK_FAILED_ALIEN_CODE, "status.json"), {
            "task_id": TASK_FAILED_ALIEN_CODE, "status": "failed", "stage": "finalize",
            "started_at": "2026-10-01T17:00:00Z",
            "updated_at": "2026-10-01T17:05:00Z",
            "message": "收尾失败：某个内部错误",
            "errors": [{"stage": "finalize", "message": "收尾失败：某个内部错误",
                        "code": "SOME_NEW_CODE"}],
        })
        write_json(cls._task_path(TASK_FAILED_ALT_KEY, "status.json"), {
            "task_id": TASK_FAILED_ALT_KEY, "status": "failed", "stage": "score_after",
            "started_at": "2026-10-01T18:00:00Z",
            "updated_at": "2026-10-01T18:05:00Z",
            "message": "缺少依赖",
            "errors": [{"stage": "score_after", "message": "缺少依赖",
                        "error_code": "DEPENDENCY_MISSING"}],
        })

        # ---- 16) 原始区（供"列原始数据"用；只读列举，不由本服务上传）----
        cls.raw_root = os.path.join(cls.tmp, "raw")
        write_text(os.path.join(cls.raw_root, "ml-1m", "ratings.dat"), u"1::2::3\n")
        write_text(os.path.join(cls.raw_root, "ml-1m", "users.dat"), u"1::M::25\n")
        write_text(os.path.join(cls.raw_root, "ml-1m", "movies.dat"), u"1::Toy::Action\n")
        os.makedirs(os.path.join(cls.raw_root, "empty-set"), exist_ok=True)
        write_text(os.path.join(cls.raw_root, "extra.dat"), u"9::9::9\n")
        write_text(os.path.join(cls.raw_root, ".hidden.dat"), u"隐藏文件\n")
        write_text(os.path.join(cls.raw_root, "stale.tmp"), u"流程残留\n")
        write_text(os.path.join(cls.raw_root, "x.dat.crc"), u"校验和附属\n")

        # ---- 17) 方案目录（供"取方案内容"用；测试里把方案目录指到这里）----
        cls.scheme_root = os.path.join(cls.tmp, "config")
        write_json(os.path.join(cls.scheme_root, "good_scheme.v1.json"),
                   {"config_type": "cleaning_rules", "version": "1.0.0"})
        write_text(os.path.join(cls.scheme_root, "broken.v1.json"), u"{不是合法 JSON")
        write_json(os.path.join(cls.scheme_root, "array.v1.json"), [])
        os.makedirs(os.path.join(cls.scheme_root, "dir.v1.json"), exist_ok=True)

    # -- 环境与调用 ---------------------------------------------------------

    def setUp(self):
        self.env = {"ML_VAR_DIR": self.tmp, "HDFS_BASE": "/data"}
        self.ctx = ToolContext(repo_root=REPO_ROOT, env=self.env)
        dispatch.reset_handlers()
        dispatch.load_handlers(("mcpserver.readers",))

    def tearDown(self):
        dispatch.reset_handlers()

    def call(self, name, args):
        """走分派层调用（参数校验 + 信封），返回完整信封。"""
        return dispatch.call_tool(name, args, self.ctx)

    def call_ok(self, name, args):
        envelope = self.call(name, args)
        self.assertTrue(envelope.get("ok"),
                        "%s 本该成功，实际：%s" % (name, json.dumps(envelope, ensure_ascii=False)))
        return envelope

    def call_error(self, name, args, code):
        envelope = self.call(name, args)
        self.assertFalse(envelope.get("ok"),
                         "%s 本该失败，实际：%s" % (name, json.dumps(envelope, ensure_ascii=False)))
        actual = (envelope.get("error") or {}).get("code")
        self.assertEqual(actual, code,
                         "%s 的错误码应为 %s，实际 %s（%s）"
                         % (name, code, actual, (envelope.get("error") or {}).get("message")))
        return envelope["error"]


# ---------------------------------------------------------------------------
# 一、契约一致性（清单、常量、注册表不许各说一套）
# ---------------------------------------------------------------------------

class TestContractAlignment(ReaderCase):

    def test_handlers_are_exactly_the_twelve_expected(self):
        expected = {
            "get_task_status", "get_task_result", "get_samples", "get_report", "list_tasks",
            "list_published_versions", "list_task_files", "read_task_file",
            "get_job_counters", "get_job_logs", "list_raw_datasets", "get_scheme_content",
        }
        self.assertEqual(set(readers.HANDLERS), expected)
        # 处理器登记的名字必须与工具表逐字一致，否则分派层永远调不到
        for name in readers.HANDLERS:
            self.assertIsNotNone(tools.get_tool(name), "工具表里没有 %s" % name)

    def test_limits_match_tool_table(self):
        max_bytes = tools.get_tool("read_task_file")["params"]
        by_name = {param["name"]: param for param in max_bytes}
        self.assertEqual(by_name["max_bytes"]["default"], readers.DEFAULT_READ_BYTES)
        self.assertEqual(by_name["max_bytes"]["maximum"], readers.MAX_READ_BYTES)
        lines = {param["name"]: param for param in tools.get_tool("get_job_logs")["params"]}
        self.assertEqual(lines["lines"]["default"], readers.DEFAULT_LOG_LINES)
        self.assertEqual(lines["lines"]["maximum"], readers.MAX_LOG_LINES)

    def test_table_files_mirror_engine(self):
        from engine import pipeline
        self.assertEqual(readers.TABLE_FILES, pipeline.TABLE_FILES)

    def test_handlers_never_emit_envelope_fields(self):
        """处理器只回业务字段；信封由分派层包（否则会出现两套 ok）。"""
        base = self._task_path(TASK_FULL)
        cases = [
            ("get_task_status", {"task_id": TASK_FULL}),
            ("get_job_counters", {"task_id": TASK_FULL}),
            ("list_task_files", {"task_id": TASK_FULL}),
            ("read_task_file", {"task_id": TASK_FULL, "path": "counts.json"}),
            ("get_job_logs", {"task_id": TASK_FULL, "kind": "driver", "lines": 5}),
        ]
        for name, args in cases:
            payload = readers.HANDLERS[name](args, self.ctx) or {}
            self.assertIsInstance(payload, dict)
            self.assertNotIn("ok", payload, "%s 不该自己带 ok" % name)
            self.assertNotIn("interface_version", payload, "%s 不该自己带版本" % name)
        self.assertTrue(os.path.isdir(base))


# ---------------------------------------------------------------------------
# 二、复用 driver 的五个能力
# ---------------------------------------------------------------------------

class TestDriverBacked(ReaderCase):

    def test_status_passes_driver_fields_through(self):
        payload = self.call_ok("get_task_status", {"task_id": TASK_FULL})
        self.assertEqual(payload["task_id"], TASK_FULL)
        self.assertEqual(payload["status"], "succeeded")
        self.assertEqual(payload["stage"], "done")
        self.assertEqual(payload["progress_percent"], 100)
        self.assertIn("updated_at", payload)
        self.assertIn("errors", payload)

    def test_status_carries_versions_and_boundaries_from_metadata(self):
        payload = self.call_ok("get_task_status", {"task_id": TASK_FULL})
        self.assertEqual(payload["versions"], {
            "data": DATA_VERSION, "rule": "1.0.0",
            "scoring": "1.0.0", "policy": "policy-1",
        })
        self.assertEqual(payload["time_boundaries"],
                         {"T1": "2001-12-31T23:59:59Z", "T2": "2002-06-30T23:59:59Z"})
        self.assertEqual(payload["version_hashes"]["rule"], "aa" * 32)
        # 出处必须写明：这一份来自任务目录里的元数据
        self.assertEqual(payload["versions_source"], "metadata")
        # 元数据里的机器绝对位置不外传
        self.assertNotIn("/home/nobody", json.dumps(payload, ensure_ascii=False))

    def test_status_without_any_source_gives_no_such_field(self):
        payload = self.call_ok("get_task_status", {"task_id": TASK_BARE})
        self.assertEqual(payload["status"], "succeeded")
        self.assertNotIn("versions", payload, "两处都没有来源就不该编造版本")
        self.assertNotIn("time_boundaries", payload, "两处都没有来源就不该编造时间边界")
        self.assertNotIn("versions_source", payload, "没有数据时连出处都不该出现")

    def test_status_falls_back_to_result_of_succeeded_task(self):
        payload = self.call_ok("get_task_status", {"task_id": TASK_RESULT_ONLY})
        self.assertEqual(payload["versions_source"], "result", "回退时必须标出出处")
        self.assertEqual(payload["versions"],
                         {"data": DATA_VERSION, "rule": "2.0.0", "scoring": "2.0.0"})
        # 结果文件里策略分组只有摘要、没有版本号：不许拿别的字段顶替
        self.assertNotIn("policy", payload["versions"])
        self.assertEqual(payload["version_hashes"]["policy"], "ff" * 32)
        self.assertEqual(payload["time_boundaries"],
                         {"T1": "2001-12-31T23:59:59Z", "T2": "2002-06-30T23:59:59Z"})
        self.assertEqual(payload["operator_library"], "fixture-ops.v2")
        # 元数据文件根本不在是集群任务的常态，不该每条状态都挂一句"读不出来"
        self.assertNotIn("metadata_error", payload)

    def test_status_never_takes_versions_from_unsucceeded_task(self):
        payload = self.call_ok("get_task_status", {"task_id": TASK_FAILED_WITH_RESULT})
        self.assertEqual(payload["status"], "failed")
        self.assertNotIn("versions", payload, "未成功任务的结果文件不许当版本来源")
        self.assertNotIn("versions_source", payload)
        self.assertNotIn("should-not-be-used", json.dumps(payload, ensure_ascii=False))

    def test_status_broken_metadata_falls_back_and_says_so(self):
        payload = self.call_ok("get_task_status", {"task_id": TASK_METADATA_CORRUPT})
        self.assertEqual(payload["versions_source"], "result")
        self.assertEqual(payload["versions"], {"data": DATA_VERSION, "rule": "3.0.0"})
        self.assertIn("metadata_error", payload, "元数据坏了要如实标注，不能装没这回事")

    def test_status_metadata_without_versions_is_explained(self):
        payload = self.call_ok("get_task_status", {"task_id": TASK_METADATA_EMPTY})
        self.assertEqual(payload["versions_source"], "result")
        self.assertEqual(payload["versions"], {"data": DATA_VERSION, "rule": "4.0.0"})
        self.assertIn("metadata_error", payload)
        self.assertIn("元数据文件", payload["metadata_error"])

    def test_status_adds_task_type_scope_and_tag(self):
        payload = self.call_ok("get_task_status", {"task_id": TASK_FULL})
        self.assertEqual(payload["task_type"], "full")
        self.assertEqual(payload["scope"], "full")
        self.assertEqual(payload["tag"], "fixture-tag")
        # 状态文件里存着方案文件的绝对位置，但这两项不该被带出去
        self.assertNotIn("rules", payload)
        self.assertNotIn("scoring", payload)
        self.assertNotIn("/home/nobody", json.dumps(payload, ensure_ascii=False))

    def test_status_omits_missing_extra_fields(self):
        payload = self.call_ok("get_task_status", {"task_id": TASK_BARE})
        for key in ("task_type", "scope", "tag"):
            self.assertNotIn(key, payload, "状态文件里没有这一项就不该给：%s" % key)

    def test_status_unknown_task_passes_driver_code(self):
        error = self.call_error("get_task_status", {"task_id": "no-such-task"}, "TASK_NOT_FOUND")
        self.assertIn("no-such-task", error["message"])

    def test_result_passes_driver_fields_through(self):
        payload = self.call_ok("get_task_result", {"task_id": TASK_FULL})
        self.assertEqual(payload["task_id"], TASK_FULL)
        self.assertEqual(payload["data_version"], DATA_VERSION)
        self.assertIn("counts", payload)
        self.assertIn("versions", payload)

    def test_result_paths_are_task_relative(self):
        payload = self.call_ok("get_task_result", {"task_id": TASK_FULL})
        paths = payload["paths"]
        # 任务目录那个绝对位置直接去掉
        self.assertNotIn("task_dir", paths)
        # 任务目录内的位置转成相对路径
        self.assertEqual(paths["cleaned_dir"], "cleaned/%s" % DATA_VERSION)
        self.assertEqual(paths["quarantine_dir"], "quarantine/%s" % DATA_VERSION)
        self.assertEqual(paths["metrics_dir"], "metrics")
        self.assertEqual(paths["report_md"], "report.md")
        self.assertEqual(paths["report_json"], "report.json")
        # 分布式文件系统上的位置原样保留，不做相对化
        self.assertEqual(paths["published_dir"], "/data/published/%s" % DATA_VERSION)
        # 任何机器上的本地位置都不许漏出去
        self.assertNotIn(self.tmp, json.dumps(payload, ensure_ascii=False))

    def test_result_carries_artifacts_matching_the_listing(self):
        payload = self.call_ok("get_task_result", {"task_id": TASK_FULL})
        self.assertIn("artifacts", payload)
        artifacts = payload["artifacts"]
        listing = self.call_ok("list_task_files", {"task_id": TASK_FULL})
        # 两处必须一一对应，且顺序一致（都按相对路径字典序）
        self.assertEqual([item["path"] for item in artifacts],
                         [item["path"] for item in listing["files"]])
        self.assertEqual(len(artifacts), listing["count"])
        self.assertEqual([item["path"] for item in artifacts],
                         sorted(item["path"] for item in artifacts))

    def test_artifact_entries_have_the_expected_shape(self):
        artifacts = self.call_ok("get_task_result", {"task_id": TASK_FULL})["artifacts"]
        by_path = {item["path"]: item for item in artifacts}
        for item in artifacts:
            for field in ("id", "title", "category", "kind", "path", "size"):
                self.assertIn(field, item, "产物清单每项都该有 %s" % field)
            self.assertEqual(item["id"], "task:%s:%s" % (TASK_FULL, item["path"]))
            self.assertIsInstance(item["size"], int)
            self.assertFalse(item["path"].startswith("/"))
        # 类别按路径前缀判
        self.assertEqual(by_path["report.md"]["category"], "报告")
        self.assertEqual(by_path["report.json"]["category"], "报告")
        self.assertEqual(by_path["cleaned/%s/ratings.dat" % DATA_VERSION]["category"], "数据")
        self.assertEqual(by_path["quarantine/%s/ratings.dat" % DATA_VERSION]["category"], "隔离")
        self.assertEqual(by_path["metrics/before.json"]["category"], "指标")
        self.assertEqual(by_path["logs/clean_users..log"]["category"], "日志")
        self.assertEqual(by_path["driver.log"]["category"], "日志")
        # 计数、统计、结果、状态这些是用户最想看的，单独归"元数据"
        for name in ("counts.json", "stats.json", "result.json", "status.json",
                     "published_hashes.json"):
            self.assertEqual(by_path[name]["category"], "元数据",
                             "%s 应当归到元数据类，而不是被埋进其它" % name)
        # 真正不好归类的才留在"其它"
        self.assertEqual(by_path["dims/users_dim.jsonl"]["category"], "其它")
        # 标题：登记过的给中文，没登记的就用文件名本身
        self.assertEqual(by_path["report.md"]["title"], "评估报告")
        self.assertEqual(by_path["counts.json"]["title"], "数据量统计")
        self.assertEqual(by_path["metrics/after.json"]["title"], "清洗后评分")
        self.assertEqual(by_path["cleaned/%s/ratings.dat" % DATA_VERSION]["title"],
                         "ratings.dat")
        # 大小与真实文件一致
        self.assertEqual(by_path["report.md"]["size"], os.path.getsize(
            self._task_path(TASK_FULL, "report.md")))

    def test_artifacts_never_include_excluded_files(self):
        artifacts = self.call_ok("get_task_result", {"task_id": TASK_FULL})["artifacts"]
        paths = [item["path"] for item in artifacts]
        self.assertFalse([path for path in paths if readers.exclusion_reason(path)],
                         "产物清单里不该有控制文件、隐藏文件或校验和附属文件：%s" % paths)
        self.assertIn("driver.log", paths, "执行日志是正当产物，应当保留")

    def test_artifacts_list_only_what_really_exists(self):
        # 只有状态与结果的任务：能列多少列多少，不补空壳项
        artifacts = self.call_ok("get_task_result", {"task_id": TASK_RESULT_ONLY})["artifacts"]
        self.assertEqual([item["path"] for item in artifacts], ["result.json", "status.json"])
        categories = {item["category"] for item in artifacts}
        self.assertEqual(categories, {"元数据"},
                         "只有结果与状态时，两份都属元数据；不该凭空给出别的类别")

    def test_failed_task_gets_no_artifacts(self):
        envelope = self.call("get_task_result", {"task_id": TASK_FAILED})
        self.assertFalse(envelope["ok"])
        self.assertNotIn("artifacts", envelope, "失败时不该混进一份半成品清单")

    def test_result_keeps_null_paths_as_is(self):
        payload = self.call_ok("get_task_result", {"task_id": TASK_RESULT_ONLY})
        self.assertIn("paths", payload)
        self.assertNotIn("task_dir", payload["paths"])
        self.assertIsNone(payload["paths"]["cleaned_dir"],
                          "空值表示这份产物不存在，是有意义的信息，不能删也不能编")

    def test_result_running_task_is_not_finished(self):
        error = self.call_error("get_task_result", {"task_id": TASK_RUNNING}, "TASK_NOT_FINISHED")
        self.assertIn("running", error["message"])

    def test_result_failed_task_reports_failure(self):
        error = self.call_error("get_task_result", {"task_id": TASK_FAILED}, "TASK_FAILED")
        self.assertIn("假失败", error["message"])
        # 失败细节要跟着一起回来，否则现场只剩一句"失败了"
        self.assertEqual(error["stage"], "score_after")
        self.assertEqual(error["job"], "score_all")
        self.assertEqual(error["exit_code"], 1)

    def test_timeouts_come_from_the_shared_channel(self):
        """这五个能力真的走统一通道：把通道的超时调到极小，就该回通道的超时码。"""
        ctx = ToolContext(repo_root=REPO_ROOT, env=self.env,
                          config={"driver_timeouts": {"status": 0.001}})
        envelope = dispatch.call_tool("get_task_status", {"task_id": TASK_FULL}, ctx)
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "DRIVER_TIMEOUT")

    def test_samples_cleaned(self):
        payload = self.call_ok("get_samples", {
            "task_id": TASK_FULL, "sample_type": "cleaned", "table": "ratings", "limit": 2})
        self.assertEqual(payload["type"], "cleaned")
        self.assertEqual(payload["total_available"], 3)
        self.assertEqual(len(payload["samples"]), 2)

    def test_samples_quarantine(self):
        payload = self.call_ok("get_samples", {
            "task_id": TASK_FULL, "sample_type": "quarantine", "table": "ratings", "limit": 5})
        self.assertEqual(payload["type"], "quarantine")
        self.assertEqual(payload["samples"][0]["rule_id"], "R2")

    def test_report_markdown_and_json(self):
        md = self.call_ok("get_report", {"task_id": TASK_FULL, "format": "md"})
        self.assertEqual(md["format"], "md")
        self.assertIn("假报告", md["report"])
        structured = self.call_ok("get_report", {"task_id": TASK_FULL, "format": "json"})
        self.assertEqual(structured["format"], "json")
        self.assertEqual(structured["report"]["task_id"], TASK_FULL)

    def test_report_missing_file_is_not_finished(self):
        self.call_error("get_report", {"task_id": TASK_BARE, "format": "md"}, "TASK_NOT_FINISHED")

    # -- 结构化报告的可读性预检：只改报错归类，不改业务结论 --------------------

    def test_corrupt_structured_report_is_product_invalid(self):
        error = self.call_error("get_report", {"task_id": TASK_REPORT_CORRUPT,
                                               "format": "json"}, "PRODUCT_INVALID")
        # 说明里要写清是哪个任务的哪份报告，并给出下一步
        self.assertIn(TASK_REPORT_CORRUPT, error["message"])
        self.assertIn("report.json", error["message"])
        self.assertIn("列任务产物文件", error["message"])
        self.assertEqual(error["task_id"], TASK_REPORT_CORRUPT)
        self.assertEqual(error["path"], "report.json")

    def test_structured_report_with_wrong_shape_is_product_invalid(self):
        error = self.call_error("get_report", {"task_id": TASK_REPORT_SHAPE,
                                               "format": "json"}, "PRODUCT_INVALID")
        self.assertIn("对象结构", error["message"])
        self.assertIn("列任务产物文件", error["message"])

    def test_full_text_report_does_not_go_through_the_check(self):
        # 全文版按文本读：报告不在就仍旧是执行侧那一条"任务未完成"
        self.call_error("get_report", {"task_id": TASK_REPORT_CORRUPT, "format": "md"},
                        "TASK_NOT_FINISHED")

    def test_absent_structured_report_keeps_execution_verdict(self):
        # 文件不存在时不自己判定，仍交给执行侧
        self.call_error("get_report", {"task_id": TASK_BARE, "format": "json"},
                        "TASK_NOT_FINISHED")

    def test_unknown_task_verdict_is_untouched(self):
        self.call_error("get_report", {"task_id": "no-such-task", "format": "json"},
                        "TASK_NOT_FOUND")

    def test_other_products_survive_a_corrupt_report(self):
        # 预检给出的提示必须成立：报告坏了，别的产物照样能看
        payload = self.call_ok("read_task_file", {"task_id": TASK_REPORT_CORRUPT,
                                                  "path": "counts.json"})
        self.assertTrue(payload["previewable"])
        listing = self.call_ok("list_task_files", {"task_id": TASK_REPORT_CORRUPT})
        self.assertIn("report.json", [item["path"] for item in listing["files"]])

    def test_list_tasks_lists_fixture_tasks(self):
        payload = self.call_ok("list_tasks", {})
        ids = [item["task_id"] for item in payload["tasks"]]
        self.assertIn(TASK_FULL, ids)
        self.assertIn(TASK_FAILED, ids)


# ---------------------------------------------------------------------------
# 三、作业计数
# ---------------------------------------------------------------------------

class TestJobCounters(ReaderCase):

    def test_counts_from_task_products(self):
        payload = self.call_ok("get_job_counters", {"task_id": TASK_FULL})
        self.assertEqual(payload["source"], "counts.json")
        self.assertEqual(sorted(payload["counts"]),
                         ["dedupe", "fix", "input", "output", "quarantine"])
        self.assertEqual(payload["counts"]["quarantine"]["total"], 2)
        # 五个分组都齐全时，标注逐项为"有"
        self.assertEqual([item["key"] for item in payload["count_groups"]],
                         ["input", "output", "quarantine", "dedupe", "fix"])
        self.assertTrue(all(item["present"] for item in payload["count_groups"]))

    def test_counts_fall_back_to_result_file(self):
        payload = self.call_ok("get_job_counters", {"task_id": TASK_RESULT_ONLY})
        self.assertEqual(payload["source"], "result.json#counts")
        self.assertEqual(payload["counts"], {"output": {"ratings": 3}})
        present = {item["key"]: item["present"] for item in payload["count_groups"]}
        self.assertTrue(present["output"])
        self.assertFalse(present["quarantine"], "缺的分组要如实标成没有，不能补零")

    def test_counts_missing_reports_which_files(self):
        error = self.call_error("get_job_counters", {"task_id": TASK_BARE}, "FILE_NOT_FOUND")
        self.assertEqual(error["checked"], ["counts.json", "result.json"])
        self.assertIn("counts.json", error["message"])

    def test_corrupt_counts_is_reported_not_silently_empty(self):
        error = self.call_error("get_job_counters", {"task_id": TASK_CORRUPT}, "PRODUCT_INVALID")
        self.assertIn("counts.json", error["message"])
        self.assertNotIn(self.tmp, json.dumps(error, ensure_ascii=False),
                         "错误说明里不该带机器上的绝对位置")

    def test_unknown_task(self):
        self.call_error("get_job_counters", {"task_id": "no-such-task"}, "TASK_NOT_FOUND")


# ---------------------------------------------------------------------------
# 四、作业日志
# ---------------------------------------------------------------------------

class TestJobLogs(ReaderCase):

    def test_driver_log_tail(self):
        payload = self.call_ok("get_job_logs", {"task_id": TASK_FULL, "kind": "driver", "lines": 5})
        self.assertEqual(payload["kind"], "driver")
        self.assertEqual(len(payload["logs"]), 1)
        entry = payload["logs"][0]
        self.assertEqual(entry["path"], "driver.log")
        self.assertEqual(entry["lines"], ["stage-%02d" % index for index in range(26, 31)])
        self.assertTrue(entry["truncated"], "30 行的日志只回 5 行，要标成只给了尾部")
        self.assertEqual(entry["returned_lines"], 5)

    def test_job_logs_only_pick_log_files(self):
        payload = self.call_ok("get_job_logs", {"task_id": TASK_FULL, "kind": "job", "lines": 3})
        paths = [entry["path"] for entry in payload["logs"]]
        self.assertEqual(paths, ["logs/clean_users..log", "logs/fetch_ratings.dat.log"])
        self.assertTrue(all(path.startswith("logs/") and path.endswith(".log") for path in paths),
                        "校验和残留文件不该被当成作业日志：%s" % paths)
        self.assertEqual(payload["logs"][0]["lines"], ["作业行 10", "作业行 11", "作业行 12"])

    def test_all_kind_merges_both_sources(self):
        payload = self.call_ok("get_job_logs", {"task_id": TASK_FULL, "kind": "all", "lines": 2})
        self.assertEqual([entry["path"] for entry in payload["logs"]],
                         ["driver.log", "logs/clean_users..log", "logs/fetch_ratings.dat.log"])
        self.assertNotIn("missing", payload, "两边都在时不该出现「缺失」标注")

    def test_all_kind_marks_the_absent_side(self):
        payload = self.call_ok("get_job_logs",
                               {"task_id": TASK_DRIVER_LOG_ONLY, "kind": "all", "lines": 2})
        self.assertEqual([entry["path"] for entry in payload["logs"]], ["driver.log"])
        self.assertEqual(payload["missing"], ["logs/*.log"],
                         "作业日志整块缺席时如实标注，而不是让人以为没有作业")

    def test_no_logs_at_all_is_an_error(self):
        self.call_error("get_job_logs", {"task_id": TASK_BARE, "kind": "driver"},
                        "FILE_NOT_FOUND")
        self.call_error("get_job_logs", {"task_id": TASK_BARE, "kind": "job"},
                        "FILE_NOT_FOUND")

    def test_line_cap_follows_tool_table(self):
        payload = self.call_ok("get_job_logs", {"task_id": TASK_FULL, "kind": "driver",
                                                "lines": readers.MAX_LOG_LINES})
        self.assertEqual(payload["lines"], readers.MAX_LOG_LINES)
        # 分派层会用工具表的上限挡住更离谱的取值
        self.call_error("get_job_logs", {"task_id": TASK_FULL, "lines": readers.MAX_LOG_LINES + 1},
                        "USAGE")

    def test_unknown_task(self):
        self.call_error("get_job_logs", {"task_id": "no-such-task"}, "TASK_NOT_FOUND")


# ---------------------------------------------------------------------------
# 五、产物清单与读产物
# ---------------------------------------------------------------------------

class TestTaskFiles(ReaderCase):

    def test_listing_covers_nested_and_classifies_kind(self):
        payload = self.call_ok("list_task_files", {"task_id": TASK_FULL})
        by_path = {item["path"]: item for item in payload["files"]}
        self.assertEqual(by_path["counts.json"]["kind"], "json")
        self.assertEqual(by_path["report.md"]["kind"], "md")
        self.assertEqual(by_path[CLEANED_RATINGS]["kind"], "dat")
        self.assertEqual(by_path["driver.log"]["kind"], "text")
        self.assertEqual(by_path["blob.bin"]["kind"], "other")
        self.assertEqual(by_path["metrics/composite.json"]["kind"], "json")
        self.assertEqual(by_path[CLEANED_RATINGS]["size"], os.path.getsize(
            self._task_path(TASK_FULL, "cleaned", DATA_VERSION, "ratings.dat")))
        self.assertEqual(payload["count"], len(payload["files"]))
        self.assertEqual([item["path"] for item in payload["files"]],
                         sorted(item["path"] for item in payload["files"]),
                         "清单要按路径稳定排序")
        for item in payload["files"]:
            self.assertFalse(item["path"].startswith("/"),
                             "只给相对位置，不给机器上的绝对位置：%s" % item["path"])

    def test_listing_excludes_filesystem_sidecars(self):
        payload = self.call_ok("list_task_files", {"task_id": TASK_FULL})
        paths = [item["path"] for item in payload["files"]]
        for hidden in (".hidden.json", "logs/.quarantine_ratings.tmp.raw.crc",
                       "cleaned/%s/.ratings.dat.raw.crc" % DATA_VERSION,
                       "metrics/.after.json.raw.crc", "counts.json.crc"):
            self.assertNotIn(hidden, paths, "隐藏文件与校验和附属文件不该出现在清单里")
        self.assertFalse([path for path in paths if readers.exclusion_reason(path)],
                         "清单里不该残留任何不当作产物的东西：%s" % paths)

    def test_listing_excludes_temp_residue(self):
        payload = self.call_ok("list_task_files", {"task_id": TASK_FULL})
        paths = [item["path"] for item in payload["files"]]
        for leftover in ("logs/published_hashes.tmp", "metrics/composite.json.part"):
            self.assertNotIn(leftover, paths,
                             "流程中途的临时残留不该出现在产物清单里")

    def test_reading_temp_residue_is_refused(self):
        error = self.call_error("read_task_file",
                                {"task_id": TASK_FULL, "path": "logs/published_hashes.tmp"},
                                "FORBIDDEN_PATH")
        self.assertIn("残留", error["message"])

    def test_listing_keeps_genuine_products(self):
        payload = self.call_ok("list_task_files", {"task_id": TASK_FULL})
        paths = [item["path"] for item in payload["files"]]
        for kept in ("driver.log", "logs/clean_users..log", "logs/fetch_ratings.dat.log",
                     "report.md", "report.json", "counts.json", "stats.json",
                     "metrics/before.json", "cleaned/%s/ratings.dat" % DATA_VERSION,
                     "quarantine/%s/ratings.dat" % DATA_VERSION):
            self.assertIn(kept, paths, "正当产物不该被一起排除：%s" % kept)

    def test_reading_sidecars_is_refused(self):
        for bad in ("counts.json.crc", ".hidden.json",
                    "cleaned/%s/.ratings.dat.raw.crc" % DATA_VERSION):
            with self.subTest(path=bad):
                error = self.call_error("read_task_file",
                                        {"task_id": TASK_FULL, "path": bad},
                                        "FORBIDDEN_PATH")
                self.assertIn("附属物", error["message"])
                self.assertNotIn("internal_file", error,
                                 "附属物不是控制文件，不该套用控制文件的标记")

    def test_listing_excludes_service_control_files(self):
        payload = self.call_ok("list_task_files", {"task_id": TASK_FULL})
        paths = [item["path"] for item in payload["files"]]
        for hidden in ("cancel_request.json", "mcp_submit.json",
                       "metrics/cancel_request.json"):
            self.assertNotIn(hidden, paths, "服务自己的控制文件不该出现在产物清单里")
        self.assertFalse(any(os.path.basename(path) in readers.CONTROL_FILE_NAMES
                             for path in paths),
                         "清单里不该残留任何控制文件：%s" % paths)
        # 执行日志仍算产物，不能被一起排除
        self.assertIn("driver.log", paths)
        self.assertIn("logs/clean_users..log", paths)

    def test_listing_skips_symlink_escape(self):
        if not self.has_symlink:
            self.skipTest("当前系统不支持创建符号链接")
        payload = self.call_ok("list_task_files", {"task_id": TASK_FULL})
        paths = [item["path"] for item in payload["files"]]
        self.assertNotIn("escape.json", paths, "指向任务目录之外的文件不该列出")

    def test_read_json(self):
        payload = self.call_ok("read_task_file", {"task_id": TASK_FULL, "path": "counts.json"})
        self.assertTrue(payload["previewable"])
        self.assertEqual(payload["kind"], "json")
        self.assertEqual(json.loads(payload["content"])["quarantine"]["total"], 2)

    def test_read_markdown_and_text(self):
        md = self.call_ok("read_task_file", {"task_id": TASK_FULL, "path": "report.md"})
        self.assertIn("假报告", md["content"])
        text = self.call_ok("read_task_file", {"task_id": TASK_FULL, "path": "driver.log"})
        self.assertEqual(text["kind"], "text")
        self.assertIn("stage-01", text["content"])

    def test_binary_is_not_previewable(self):
        payload = self.call_ok("read_task_file", {"task_id": TASK_FULL, "path": "blob.bin"})
        self.assertFalse(payload["previewable"])
        self.assertEqual(payload["kind"], "other")
        self.assertNotIn("content", payload, "二进制不该回内容")
        # .dat 属不可预览的类型（清洗产物是二进制编码的行数据）
        dat = self.call_ok("read_task_file", {"task_id": TASK_FULL, "path": CLEANED_RATINGS})
        self.assertFalse(dat["previewable"])
        self.assertEqual(dat["kind"], "dat")

    def test_relative_escape_is_forbidden(self):
        for bad in ("../status.json", "../../../etc/passwd", "metrics/../../status.json",
                    ".", "metrics"):
            with self.subTest(path=bad):
                self.call("read_task_file", {"task_id": TASK_FULL, "path": bad})

    def test_dotdot_never_reaches_outside(self):
        error = self.call_error("read_task_file",
                                {"task_id": TASK_FULL, "path": "../../%s" % OUTSIDE_NAME},
                                "FORBIDDEN_PATH")
        self.assertIn("越界", error["message"])

    def test_absolute_path_is_forbidden(self):
        self.call_error("read_task_file", {"task_id": TASK_FULL, "path": self.outside},
                        "FORBIDDEN_PATH")
        self.call_error("read_task_file", {"task_id": TASK_FULL, "path": "/etc/passwd"},
                        "FORBIDDEN_PATH")
        self.call_error("read_task_file", {"task_id": TASK_FULL, "path": "C:\\Windows\\win.ini"},
                        "FORBIDDEN_PATH")

    def test_symlink_escape_is_forbidden(self):
        if not self.has_symlink:
            self.skipTest("当前系统不支持创建符号链接")
        self.call_error("read_task_file", {"task_id": TASK_FULL, "path": "escape.json"},
                        "FORBIDDEN_PATH")

    def test_task_id_with_separator_is_forbidden(self):
        for bad in ("../config", "a/b", "..", "/var", "..\\x"):
            with self.subTest(task_id=bad):
                self.call_error("list_task_files", {"task_id": bad}, "FORBIDDEN_PATH")

    def test_size_over_limit_is_reported(self):
        error = self.call_error(
            "read_task_file",
            {"task_id": TASK_FULL, "path": "big.json", "max_bytes": 64},
            "CONTENT_TOO_LARGE")
        self.assertGreater(error["size"], 64)
        self.assertEqual(error["max_bytes"], 64)

    def test_size_limit_is_validated_by_dispatch(self):
        self.call_error("read_task_file",
                        {"task_id": TASK_FULL, "path": "counts.json",
                         "max_bytes": readers.MAX_READ_BYTES + 1},
                        "USAGE")

    def test_control_files_are_not_readable(self):
        for bad in ("cancel_request.json", "mcp_submit.json",
                    "metrics/cancel_request.json"):
            with self.subTest(path=bad):
                error = self.call_error("read_task_file",
                                        {"task_id": TASK_FULL, "path": bad},
                                        "FORBIDDEN_PATH")
                self.assertIn("内部", error["message"])
                self.assertTrue(error.get("internal_file"))

    def test_control_file_behind_its_own_path_is_refused_even_when_absent(self):
        # 控制文件不给看这件事与"文件在不在"无关：不在也按同一口径拒绝
        error = self.call_error("read_task_file",
                                {"task_id": TASK_BARE, "path": "mcp_submit.json"},
                                "FORBIDDEN_PATH")
        self.assertIn("内部", error["message"])

    def test_symlink_rename_does_not_open_control_files(self):
        if not self.has_control_link:
            self.skipTest("当前系统不支持创建符号链接")
        self.call_error("read_task_file",
                        {"task_id": TASK_FULL, "path": "looks-normal.json"},
                        "FORBIDDEN_PATH")

    def test_missing_file(self):
        self.call_error("read_task_file", {"task_id": TASK_FULL, "path": "nope.json"},
                        "FILE_NOT_FOUND")
        self.call_error("read_task_file",
                        {"task_id": TASK_FULL, "path": "cleaned/%s/nope.dat" % DATA_VERSION},
                        "FILE_NOT_FOUND")

    def test_unknown_task(self):
        self.call_error("list_task_files", {"task_id": "no-such-task"}, "TASK_NOT_FOUND")
        self.call_error("read_task_file", {"task_id": "no-such-task", "path": "counts.json"},
                        "TASK_NOT_FOUND")


# ---------------------------------------------------------------------------
# 五之二、白名单根（任务产物目录 + 方案目录）
# ---------------------------------------------------------------------------

class TestWhitelistRoots(ReaderCase):

    def test_roots_are_task_products_and_schemes(self):
        roots = [os.path.realpath(root) for root in readers.allowed_roots(self.ctx)]
        self.assertIn(os.path.realpath(self.tasks_root), roots)
        self.assertIn(os.path.realpath(os.path.join(REPO_ROOT, "config")), roots)

    def test_scheme_and_task_files_are_reachable(self):
        scheme = readers.resolve_allowed_path(self.ctx, "config/cleaning_rules.v1.json")
        self.assertTrue(os.path.isfile(scheme))
        task_file = readers.resolve_allowed_path(
            self.ctx, "tasks/%s/counts.json" % TASK_FULL)
        self.assertTrue(os.path.isfile(task_file))
        self.assertTrue(task_file.startswith(os.path.realpath(self.tasks_root)))

    def test_outside_roots_is_forbidden(self):
        for bad in ("../etc/passwd", "config/../../etc/passwd", "/etc/passwd",
                    "tasks/../../etc/passwd"):
            with self.subTest(path=bad):
                with self.assertRaises(readers.StructuredError) as caught:
                    readers.resolve_allowed_path(self.ctx, bad)
                self.assertEqual(caught.exception.code, "FORBIDDEN_PATH")

    def test_inside_root_but_absent_is_not_found(self):
        with self.assertRaises(readers.StructuredError) as caught:
            readers.resolve_allowed_path(self.ctx, "tasks/no-such-task/counts.json")
        self.assertEqual(caught.exception.code, "FILE_NOT_FOUND")


# ---------------------------------------------------------------------------
# 六、已发布版本（集群读不到时必须报错，不能回空列表）
# ---------------------------------------------------------------------------

class TestPublishedVersions(ReaderCase):

    def _fake_cluster(self, script_body):
        """造一个假的集群命令：`<HADOOP_HOME>/bin/hdfs`，内容由用例给定。"""
        home = tempfile.mkdtemp(prefix="fake-hadoop-", dir=self.tmp)
        binary = os.path.join(home, "bin", "hdfs")
        write_text(binary, "#!/bin/bash\n" + script_body + "\n")
        os.chmod(binary, 0o755)
        return ToolContext(repo_root=REPO_ROOT,
                           env={"ML_VAR_DIR": self.tmp, "HDFS_BASE": "/data",
                                "HADOOP_HOME": home})

    def _call_with(self, ctx, name, args):
        dispatch.reset_handlers()
        dispatch.load_handlers(("mcpserver.readers",))
        return dispatch.call_tool(name, args, ctx)

    def test_unreachable_cluster_is_an_error_not_an_empty_list(self):
        ctx = self._fake_cluster(
            'echo "ls: Call From host to localhost:9000 failed on connection exception: '
            'java.net.ConnectException: Connection refused" >&2\nexit 1')
        envelope = self._call_with(ctx, "list_published_versions", {})
        self.assertFalse(envelope["ok"], "集群连不上时必须报错")
        self.assertEqual(envelope["error"]["code"], "DRIVER_UNREACHABLE")
        self.assertNotIn("versions", envelope, "失败时不许混进一份空列表")
        self.assertIn("集群", envelope["error"]["message"])

    def test_missing_published_dir_is_empty_but_exists_false(self):
        # 集群确实连得上，只是发布区目录还没建 —— 这属于"还没有发布过"
        ctx = self._fake_cluster(
            'printf "%s\\n" "ls: /data/published: No such file or directory" >&2\nexit 1')
        envelope = self._call_with(ctx, "list_published_versions", {})
        self.assertTrue(envelope["ok"], json.dumps(envelope, ensure_ascii=False))
        self.assertEqual(envelope["versions"], [])
        self.assertFalse(envelope["exists"])
        self.assertEqual(envelope["published_dir"], "/data/published")

    def test_really_empty_published_dir(self):
        ctx = self._fake_cluster('echo "Found 0 items"\nexit 0')
        envelope = self._call_with(ctx, "list_published_versions", {})
        self.assertTrue(envelope["ok"])
        self.assertEqual(envelope["versions"], [])
        self.assertTrue(envelope["exists"])
        self.assertEqual(envelope["count"], 0)

    def test_versions_are_parsed_from_cluster_listing(self):
        listing = "\n".join([
            "Found 5 items",
            "drwxr-xr-x   - ubuntu ubuntu          0 2026-09-29 13:41 /data/published/older-v1",
            "drwxr-xr-x   - ubuntu ubuntu          0 2026-10-01 09:12 /data/published/newer-v2",
            "-rw-r--r--   1 ubuntu ubuntu       1024 2026-09-29 13:41 "
            "/data/published/older-v1/ratings.dat",
            "-rw-r--r--   1 ubuntu ubuntu        512 2026-09-29 13:41 "
            "/data/published/older-v1/users.dat",
            "-rw-r--r--   1 ubuntu ubuntu       2048 2026-09-29 13:41 "
            "/data/published/older-v1/movies.dat",
            "-rw-r--r--   1 ubuntu ubuntu        243 2026-09-29 13:41 "
            "/data/published/older-v1/.published_hashes.json",
            "-rw-r--r--   1 ubuntu ubuntu       4096 2026-10-01 09:12 "
            "/data/published/newer-v2/ratings.dat",
            "-rw-r--r--   1 ubuntu ubuntu       2048 2026-10-01 09:12 "
            "/data/published/newer-v2/users.dat",
        ])
        ctx = self._fake_cluster('cat <<\'EOF\'\n%s\nEOF\nexit 0' % listing)
        envelope = self._call_with(ctx, "list_published_versions", {})
        self.assertTrue(envelope["ok"])
        self.assertEqual(envelope["count"], 2)
        newest, older = envelope["versions"]
        # 按发布时间从新到旧
        self.assertEqual(newest["data_version"], "newer-v2")
        self.assertEqual(older["data_version"], "older-v1")
        # 三表与大小
        tables = {item["name"]: item for item in older["tables"]}
        self.assertEqual(sorted(tables), ["movies", "ratings", "users"])
        self.assertEqual(tables["ratings"]["size"], 1024)
        self.assertEqual(tables["ratings"]["file"], "ratings.dat")
        # 内容清单列在其它文件里，不被当成表
        self.assertEqual([item["name"] for item in older["other_files"]],
                         [".published_hashes.json"])
        # 缺少的那张表如实标注，不补零
        self.assertEqual(newest["missing_tables"], ["movies"])
        # 发布时间：集群给的是本地时间文本，另附时间戳
        self.assertEqual(older["published_at_text"], "2026-09-29 13:41")
        self.assertIsInstance(older["published_at"], float)
        # 发布目录里没有来源任务信息时，不给这个字段（不猜）
        self.assertNotIn("source_task_id", older)

    def test_real_cluster_command_with_unreachable_address(self):
        """用真集群命令指向一个没人监听的地址：这是"集群未启动"的真实表现。"""
        real_hdfs = os.path.join(REPO_ROOT, ".vendor", "hadoop-3.3.6", "bin", "hdfs")
        if not os.path.isfile(real_hdfs):
            self.skipTest("仓库内没有集群工具（.vendor 缺失），跳过真实命令用例")
        conf = tempfile.mkdtemp(prefix="hadoop-conf-", dir=self.tmp)
        write_text(os.path.join(conf, "core-site.xml"),
                   u'<configuration><property><name>fs.defaultFS</name>'
                   u'<value>hdfs://127.0.0.1:1</value></property></configuration>')
        ctx = ToolContext(repo_root=REPO_ROOT,
                          env={"ML_VAR_DIR": self.tmp, "HDFS_BASE": "/data",
                               "HADOOP_CONF_DIR": conf})
        envelope = self._call_with(ctx, "list_published_versions", {})
        self.assertFalse(envelope["ok"], "连不上集群时必须是失败信封")
        self.assertEqual(envelope["error"]["code"], "DRIVER_UNREACHABLE")
        self.assertEqual(envelope["error"]["published_dir"], "/data/published")
        # 说明里要带上集群自己的报错片段，便于排障
        self.assertTrue(envelope["error"].get("stderr"), "应当保留集群命令的报错片段")
        self.assertNotIn("versions", envelope, "失败时不许混进一份空列表")


if __name__ == "__main__":
    unittest.main(verbosity=2)


# ---------------------------------------------------------------------------
# 十三、中文状态名、结束时间、已中止任务的取结果
# ---------------------------------------------------------------------------

class TestStatusDecorations(ReaderCase):
    """状态名与结束时间：对端任务视图直接可用的那两样。"""

    def test_status_carries_both_label_keys(self):
        payload = self.call_ok("get_task_status", {"task_id": TASK_FULL})
        self.assertEqual(payload["status_label"], "成功")
        self.assertEqual(payload["status_cn"], "成功")
        self.assertEqual(payload["status_label"], payload["status_cn"],
                         "两个键必须同值：一个给对端已有命名，一个给我们自己的命名")

    def test_result_carries_both_label_keys(self):
        payload = self.call_ok("get_task_result", {"task_id": TASK_FULL})
        self.assertEqual(payload["status_label"], "成功")
        self.assertEqual(payload["status_cn"], "成功")

    def test_aborted_status_gets_its_own_label(self):
        payload = self.call_ok("get_task_status", {"task_id": TASK_ABORTED})
        self.assertEqual(payload["status"], "aborted")
        self.assertEqual(payload["status_label"], "已中止")
        self.assertEqual(payload["status_cn"], "已中止")

    def test_finished_at_present_when_status_file_has_it(self):
        payload = self.call_ok("get_task_status", {"task_id": TASK_BIG_REPORT})
        self.assertEqual(payload["finished_at"], "2026-10-01T13:01:00Z")

    def test_finished_at_absent_when_status_file_lacks_it(self):
        payload = self.call_ok("get_task_status", {"task_id": TASK_FULL})
        self.assertNotIn("finished_at", payload,
                         "状态文件没有结束时间就不该给，更不能拿更新时间顶替")

    def test_unknown_status_value_gets_no_label(self):
        """状态取值不在清单里时不给中文名，也不猜。"""
        odd = "fixture-odd-status-16"
        write_json(self._task_path(odd, "status.json"), {
            "task_id": odd, "status": "mystery", "stage": "queued",
            "started_at": "2026-10-01T15:00:00Z",
            "updated_at": "2026-10-01T15:01:00Z", "errors": [],
        })
        payload = self.call_ok("get_task_status", {"task_id": odd})
        self.assertEqual(payload["status"], "mystery")
        self.assertNotIn("status_label", payload)
        self.assertNotIn("status_cn", payload)


class TestAbortedResult(ReaderCase):
    """已中止是终态：取结果要单独一类错误，不能报"任务未完成"。"""

    def test_aborted_result_reports_aborted_code(self):
        error = self.call_error("get_task_result", {"task_id": TASK_ABORTED}, "ABORTED")
        self.assertIn("已中止", error["message"])
        self.assertIn("终态", error["message"])
        self.assertIn("重新发起", error["message"])
        self.assertEqual(error["status"], "aborted")
        self.assertEqual(error["status_cn"], "已中止")
        self.assertEqual(error["finished_at"], "2026-10-01T12:04:00Z")
        self.assertEqual(error["aborted_at"], "2026-10-01T12:04:00Z")

    def test_aborted_result_never_returns_the_stale_result_file(self):
        envelope = self.call("get_task_result", {"task_id": TASK_ABORTED})
        self.assertFalse(envelope.get("ok"))
        self.assertNotIn("counts", envelope)
        self.assertNotIn("artifacts", envelope)

    def test_running_task_still_reports_not_finished(self):
        # 未中止的"还没到终态"仍旧按执行侧的结论走，不被这条改动带偏
        self.call_error("get_task_result", {"task_id": TASK_RUNNING}, "TASK_NOT_FINISHED")

    def test_aborted_code_is_registered_in_contract(self):
        from mcpserver import contract
        self.assertIn("ABORTED", contract.STRUCTURED_ERROR_CODES)


# ---------------------------------------------------------------------------
# 十四、报告的大小上限
# ---------------------------------------------------------------------------

class TestReportSizeLimit(ReaderCase):

    def test_markdown_report_over_limit_rejected(self):
        error = self.call_error("get_report",
                                {"task_id": TASK_BIG_REPORT, "format": "md"},
                                "CONTENT_TOO_LARGE")
        self.assertGreater(error["size"], error["max_bytes"])
        self.assertEqual(error["max_bytes"], readers.REPORT_MAX_BYTES)
        self.assertIn("下载", error["message"])

    def test_structured_report_over_limit_rejected(self):
        error = self.call_error("get_report",
                                {"task_id": TASK_BIG_REPORT, "format": "json"},
                                "CONTENT_TOO_LARGE")
        self.assertEqual(error["path"], readers.REPORT_JSON_NAME)
        self.assertGreater(error["size"], readers.REPORT_MAX_BYTES)

    def test_normal_report_still_readable(self):
        payload = self.call_ok("get_report", {"task_id": TASK_FULL, "format": "md"})
        self.assertEqual(payload["format"], "md")

    def test_missing_report_still_reports_not_finished(self):
        # 大小检查不该抢"没有这份报告"的结论：那是执行侧的
        self.call_error("get_report", {"task_id": TASK_BARE, "format": "md"},
                        "TASK_NOT_FINISHED")


# ---------------------------------------------------------------------------
# 十五、非常规文件（管道）必须提前挡住，而不是挂住
# ---------------------------------------------------------------------------

class AlarmTimeout(object):
    """给"可能挂住"的调用加一道闹钟：超时就判失败，避免测试整体卡死。"""

    def __init__(self, seconds):
        self.seconds = seconds
        self.enabled = hasattr(signal, "SIGALRM") and hasattr(signal, "setitimer")

    def _raise(self, signum, frame):
        raise AssertionError(
            "调用在 %s 秒内没有返回：多半是读了管道之类会挂住的东西" % self.seconds)

    def __enter__(self):
        if self.enabled:
            self._old = signal.signal(signal.SIGALRM, self._raise)
            signal.setitimer(signal.ITIMER_REAL, self.seconds)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.enabled:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, self._old)
        return False


class TestNonRegularFiles(ReaderCase):

    def setUp(self):
        super(TestNonRegularFiles, self).setUp()
        if not self.has_fifo:
            self.skipTest("当前系统/环境不支持创建命名管道（os.mkfifo），跳过这一组")

    def test_pipe_is_rejected_not_hung(self):
        started = time.time()
        with AlarmTimeout(5):
            error = self.call_error(
                "read_task_file", {"task_id": TASK_PIPE, "path": "pipe.json"},
                "PRODUCT_INVALID")
        self.assertIn("命名管道", error["message"])
        self.assertIn("不是普通文件", error["message"])
        self.assertLess(time.time() - started, 5, "读管道必须立刻被拒，不能挂住")

    def test_pipe_at_counts_position_rejected(self):
        with AlarmTimeout(5):
            error = self.call_error("get_job_counters", {"task_id": TASK_PIPE},
                                    "PRODUCT_INVALID")
        self.assertIn("命名管道", error["message"])

    def test_pipe_at_report_position_rejected(self):
        with AlarmTimeout(5):
            self.call_error("get_report", {"task_id": TASK_PIPE, "format": "json"},
                            "PRODUCT_INVALID")

    def test_pipe_at_log_position_rejected(self):
        with AlarmTimeout(5):
            self.call_error("get_job_logs", {"task_id": TASK_PIPE}, "PRODUCT_INVALID")

    def test_directory_is_still_reported_as_not_a_file(self):
        # 目录走原来的结论（不是可读文件），与管道的归类分开
        self.call_error("read_task_file", {"task_id": TASK_FULL, "path": "cleaned"},
                        "FILE_NOT_FOUND")


# ---------------------------------------------------------------------------
# 十六、列原始数据（只读，不提供上传）
# ---------------------------------------------------------------------------

class TestRawDatasets(ReaderCase):

    def raw_ctx(self, raw_path):
        return ToolContext(repo_root=REPO_ROOT,
                           env={"ML_VAR_DIR": self.tmp, "HDFS_BASE": "/data",
                                "ML_RAW_DIR": raw_path})

    def call_raw(self, raw_path, name="list_raw_datasets", args=None):
        return dispatch.call_tool(name, args or {}, self.raw_ctx(raw_path))

    def test_lists_datasets_and_loose_files_separately(self):
        payload = self.call_raw(self.raw_root)
        self.assertTrue(payload["ok"], payload)
        names = [item["name"] for item in payload["datasets"]]
        self.assertIn("ml-1m", names)
        dataset = [item for item in payload["datasets"] if item["name"] == "ml-1m"][0]
        self.assertEqual(dataset["file_count"], 3)
        self.assertEqual(sorted(item["name"] for item in dataset["files"]),
                         ["movies.dat", "ratings.dat", "users.dat"])
        for item in dataset["files"]:
            self.assertIn("size", item)
            self.assertIn("updated_at", item)
            self.assertIn("kind", item)
        self.assertEqual(dataset["size"],
                         sum(item["size"] for item in dataset["files"]))
        # 直接放在根下的文件单列，不混进数据集
        loose = [item["name"] for item in payload["files"]]
        self.assertEqual(loose, ["extra.dat"])
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["file_count"], 4)

    def test_empty_dataset_directory_is_kept(self):
        payload = self.call_raw(self.raw_root)
        empty = [item for item in payload["datasets"] if item["name"] == "empty-set"][0]
        self.assertEqual(empty["files"], [])
        self.assertEqual(empty["file_count"], 0)
        self.assertIsNone(empty["updated_at"])

    def test_hidden_and_sidecar_entries_are_reported_as_skipped(self):
        payload = self.call_raw(self.raw_root)
        skipped = {item["name"]: item["reason"] for item in payload.get("skipped") or []}
        self.assertNotIn(".hidden.dat", [item["name"] for item in payload["files"]])
        for name in ("stale.tmp", "x.dat.crc"):
            self.assertNotIn(name, [item["name"] for item in payload["files"]])
            self.assertIn(name, skipped)

    def test_no_absolute_path_leaks(self):
        payload = self.call_raw(self.raw_root)
        text = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn(self.raw_root, text)
        self.assertNotIn(self.tmp, text)

    def test_unconfigured_raw_dir_is_an_error_not_an_empty_list(self):
        with mock.patch.object(readers, "raw_dir", lambda ctx: ""):
            envelope = self.call_raw(self.raw_root)
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "DEPENDENCY_MISSING")
        self.assertEqual(envelope["error"]["reason"], "unconfigured")
        self.assertNotIn("datasets", envelope)

    def test_missing_raw_dir_is_an_error_not_an_empty_list(self):
        missing = os.path.join(self.tmp, "no-such-raw")
        envelope = self.call_raw(missing)
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "DEPENDENCY_MISSING")
        self.assertEqual(envelope["error"]["reason"], "missing")
        self.assertNotIn("datasets", envelope)

    def test_empty_raw_dir_says_empty_not_missing(self):
        empty = tempfile.mkdtemp(prefix="raw-empty-", dir=self.tmp)
        payload = self.call_raw(empty)
        self.assertTrue(payload["ok"], payload)
        self.assertTrue(payload["empty"])
        self.assertEqual(payload["datasets"], [])
        self.assertEqual(payload["files"], [])
        self.assertIn("note", payload)
        self.assertNotIn("没有配置", payload["note"])


# ---------------------------------------------------------------------------
# 十七、取方案内容
# ---------------------------------------------------------------------------

class TestSchemeContent(ReaderCase):

    def setUp(self):
        super(TestSchemeContent, self).setUp()
        patcher = mock.patch.object(readers, "config_root", lambda ctx: self.scheme_root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_reads_registered_scheme(self):
        payload = self.call_ok("get_scheme_content", {"name": "good_scheme.v1.json"})
        self.assertEqual(payload["name"], "good_scheme.v1.json")
        self.assertEqual(payload["path"], "config/good_scheme.v1.json")
        self.assertEqual(payload["content"]["config_type"], "cleaning_rules")
        self.assertIn("size", payload)

    def test_missing_scheme_file(self):
        self.call_error("get_scheme_content", {"name": "no_such.v1.json"}, "FILE_NOT_FOUND")

    def test_broken_scheme_content_is_product_invalid(self):
        error = self.call_error("get_scheme_content", {"name": "broken.v1.json"},
                                "PRODUCT_INVALID")
        self.assertIn("config/broken.v1.json", error["message"])

    def test_scheme_with_wrong_top_level_shape(self):
        self.call_error("get_scheme_content", {"name": "array.v1.json"}, "PRODUCT_INVALID")

    def test_scheme_path_that_is_a_directory(self):
        self.call_error("get_scheme_content", {"name": "dir.v1.json"}, "PRODUCT_INVALID")

    def test_path_like_name_is_rejected_before_reading(self):
        self.call_error("get_scheme_content", {"name": "../../etc/passwd.json"},
                        "FORBIDDEN_PATH")
        self.call_error("get_scheme_content", {"name": "config.json/../x.json"},
                        "FORBIDDEN_PATH")

    def test_non_json_name_is_rejected(self):
        self.call_error("get_scheme_content", {"name": "notes.txt"}, "FORBIDDEN_PATH")


# ---------------------------------------------------------------------------
# 十八、错误里的机器路径必须遮蔽
# ---------------------------------------------------------------------------

class TestPathSanitization(ReaderCase):

    def test_known_roots_are_replaced_by_labels(self):
        text = "%s/var/tasks/x/status.json 与 %s" % (REPO_ROOT, self.tmp)
        masked = readers.sanitize_text(text, self.ctx)
        self.assertNotIn(REPO_ROOT, masked)
        self.assertNotIn(self.tmp, masked)
        self.assertIn("<仓库根>", masked)
        self.assertIn("<产物目录>", masked)

    def test_generic_home_paths_are_masked(self):
        masked = readers.sanitize_text("失败：/home/someone/secret/x.txt 打不开", self.ctx)
        self.assertNotIn("/home/someone", masked)
        self.assertIn("<本机路径>", masked)

    def test_distributed_paths_are_not_masked(self):
        text = "发布区：/data/published/ml1m-clean-v1/ratings.dat"
        self.assertEqual(readers.sanitize_text(text, self.ctx), text,
                         "分布式文件系统上的路径不是本机位置，不该被遮蔽")

    def test_sanitized_error_covers_message_and_details(self):
        exc = StructuredError("TASK_FAILED", "打不开 %s/var/x" % REPO_ROOT,
                              stderr="见 %s/var/tasks/a/logs/b.log" % REPO_ROOT,
                              count=3)
        masked = readers.sanitized_error(exc, self.ctx)
        self.assertNotIn(REPO_ROOT, masked.message)
        self.assertNotIn(REPO_ROOT, masked.extra["stderr"])
        self.assertEqual(masked.extra["count"], 3, "非字符串细节原样保留")
        self.assertEqual(masked.code, "TASK_FAILED")

    def test_excluded_file_error_has_no_machine_path_and_names_the_kind(self):
        error = self.call_error(
            "read_task_file", {"task_id": TASK_FULL, "path": "cancel_request.json"},
            "FORBIDDEN_PATH")
        self.assertEqual(error["reason_kind"], "control")
        self.assertNotIn(self.tmp, error["message"])
        self.assertNotIn(REPO_ROOT, error["message"])

    def test_sidecar_and_residue_kinds_are_named(self):
        sidecar = self.call_error(
            "read_task_file",
            {"task_id": TASK_FULL, "path": "cleaned/%s/.ratings.dat.raw.crc" % DATA_VERSION},
            "FORBIDDEN_PATH")
        self.assertEqual(sidecar["reason_kind"], "sidecar")
        residue = self.call_error(
            "read_task_file", {"task_id": TASK_FULL, "path": "logs/published_hashes.tmp"},
            "FORBIDDEN_PATH")
        self.assertEqual(residue["reason_kind"], "residue")

    def test_every_exclusion_reason_has_a_kind(self):
        for reason in (readers.REASON_CONTROL_FILE, readers.REASON_FILESYSTEM_SIDECAR,
                       readers.REASON_TEMP_RESIDUE):
            self.assertIn(reason, readers.EXCLUSION_KINDS)

    def test_missing_file_message_has_no_machine_path(self):
        error = self.call_error("read_task_file",
                                {"task_id": TASK_FULL, "path": "nope.json"},
                                "FILE_NOT_FOUND")
        self.assertNotIn(self.tmp, error["message"])
        self.assertNotIn(REPO_ROOT, error["message"])


# ---------------------------------------------------------------------------
# 十九、参数默认值只从工具表来，并显式传给执行侧
# ---------------------------------------------------------------------------

class TestDefaultsFromToolTable(ReaderCase):

    def test_param_default_reads_tool_table(self):
        self.assertEqual(readers.param_default("get_samples", "sample_type"),
                         tools.get_tool("get_samples")["params"][1]["default"])
        self.assertIsNone(readers.param_default("get_samples", "no_such_param"))

    def test_samples_always_pass_explicit_options(self):
        """缺省调用时，`type` 与 `table` 必须是工具表里的默认值，不能被处理器另写一套。"""
        captured = {}

        def fake_call(subcommand, options, ctx=None):
            captured["subcommand"] = subcommand
            captured["options"] = dict(options or {})
            return {"task_id": "x", "type": options.get("type"),
                    "table": options.get("table"), "n": options.get("n")}

        with mock.patch.object(readers, "_call", fake_call):
            payload = self.call_ok("get_samples", {"task_id": TASK_FULL})
        self.assertEqual(captured["subcommand"], readers.CMD_SAMPLES)
        self.assertEqual(captured["options"]["type"], "cleaned")
        self.assertEqual(captured["options"]["table"], "movies")
        self.assertEqual(captured["options"]["n"], "5")
        self.assertEqual(payload["type"], "cleaned")

    def test_report_format_default_comes_from_tool_table(self):
        captured = {}

        def fake_call(subcommand, options, ctx=None):
            captured["options"] = dict(options or {})
            return {"task_id": "x", "format": options.get("format")}

        with mock.patch.object(readers, "_call", fake_call):
            self.call_ok("get_report", {"task_id": TASK_FULL})
        self.assertEqual(captured["options"]["format"], "json")


# ---------------------------------------------------------------------------
# 二十、失败任务的分类以任务记录里的结构化码为准
# ---------------------------------------------------------------------------

class TestFailureCodeRefinement(ReaderCase):
    """发布被版本保护拦下时，执行的结论是"任务失败"，真实原因是版本冲突。

    这一组锁定三件事：**有码就用那个码**、**说明与阶段原样带出**、**没码就维持原样**。
    """

    def test_version_conflict_record_refines_the_code(self):
        error = self.call_error("get_task_result", {"task_id": TASK_FAILED_WITH_CODE},
                                "VERSION_CONFLICT")
        # 说明是执行侧的原话，一个字都不改写
        self.assertIn("内容哈希不一致", error["message"])
        self.assertIn("必须升 data_version", error["message"])
        # 失败阶段照原样带出
        self.assertEqual(error["stage"], "publish")
        # 标明这个分类是从任务记录里来的，便于排障
        self.assertEqual(error["code_source"], "task_record")
        self.assertEqual(error["recorded_code"], "VERSION_CONFLICT")

    def test_message_equals_the_execution_side_wording(self):
        record = read_json(self._task_path(TASK_FAILED_WITH_CODE, "status.json"))
        expected = record["errors"][0]["message"]
        error = self.call_error("get_task_result", {"task_id": TASK_FAILED_WITH_CODE},
                                "VERSION_CONFLICT")
        self.assertEqual(error["message"], expected,
                         "分类可以换，说明必须仍是执行侧原话")

    def test_without_code_it_stays_task_failed(self):
        error = self.call_error("get_task_result", {"task_id": TASK_FAILED}, "TASK_FAILED")
        self.assertEqual(error["stage"], "score_after")
        self.assertNotIn("code_source", error)
        self.assertNotIn("recorded_code", error)

    def test_unknown_recorded_code_is_passed_through_unchanged(self):
        error = self.call_error("get_task_result",
                                {"task_id": TASK_FAILED_ALIEN_CODE}, "SOME_NEW_CODE")
        self.assertEqual(error["recorded_code"], "SOME_NEW_CODE")
        self.assertEqual(error["stage"], "finalize")
        self.assertIn("收尾失败", error["message"])

    def test_alternative_field_name_is_also_picked_up(self):
        error = self.call_error("get_task_result", {"task_id": TASK_FAILED_ALT_KEY},
                                "DEPENDENCY_MISSING")
        self.assertEqual(error["code_source"], "task_record")
        self.assertEqual(error["stage"], "score_after")

    def test_successful_task_is_unaffected(self):
        payload = self.call_ok("get_task_result", {"task_id": TASK_FULL})
        self.assertEqual(payload["status"], "succeeded")
        self.assertNotIn("code_source", payload)

    def test_other_conclusions_are_not_touched(self):
        # 运行中仍是"未完成"，已中止仍是"已中止"：只有"任务失败"这一条会看记录
        self.call_error("get_task_result", {"task_id": TASK_RUNNING}, "TASK_NOT_FINISHED")
        self.call_error("get_task_result", {"task_id": TASK_ABORTED}, "ABORTED")


# ---------------------------------------------------------------------------
# 二十一、控制文件名来自契约层（唯一来源），以及新能力确实登记上了
# ---------------------------------------------------------------------------

class TestControlFileNameSource(ReaderCase):

    def test_names_match_contract(self):
        from mcpserver import contract
        self.assertEqual(tuple(readers.CONTROL_FILE_NAMES),
                         tuple(contract.CONTROL_FILE_NAMES),
                         "读取层的控制文件名单必须与契约层逐字一致（改名只改契约层一处）")
        self.assertEqual(tuple(readers.CONTROL_FILE_NAMES),
                         (contract.CONTROL_FILE_CANCEL, contract.CONTROL_FILE_SUBMIT))

    def test_contract_names_are_not_empty(self):
        from mcpserver import contract
        for name in contract.CONTROL_FILE_NAMES:
            self.assertTrue(name and isinstance(name, str))
            self.assertNotIn("/", name)
            self.assertNotIn("\\", name)

    def test_readers_does_not_hardcode_the_names_again(self):
        """源码里除了转发契约常量，不应该再出现这两个字面量。"""
        from mcpserver import contract
        with io.open(readers.__file__, encoding="utf-8") as handle:
            source = handle.read()
        for name in contract.CONTROL_FILE_NAMES:
            occurrences = source.count('"%s"' % name) + source.count("'%s'" % name)
            self.assertEqual(
                occurrences, 0,
                "readers.py 里又写了字面量 %r：应当引用 contract 里的常量" % name)


class TestNewCapabilitiesRegistered(ReaderCase):

    def test_both_new_capabilities_are_in_the_handler_table(self):
        for name in ("list_raw_datasets", "get_scheme_content"):
            self.assertIn(name, readers.HANDLERS, "%s 必须在处理器表里" % name)
            self.assertIsNotNone(tools.get_tool(name), "工具表里必须有 %s" % name)

    def test_they_are_reachable_through_dispatch(self):
        available = dispatch.available_tools()
        for name in ("list_raw_datasets", "get_scheme_content"):
            self.assertIn(name, available, "%s 未登记到分派层，对端一调就会报未接入" % name)

    def test_tool_table_covers_handlers(self):
        for name in readers.HANDLERS:
            self.assertIsNotNone(tools.get_tool(name),
                                 "处理器 %s 在工具表里不存在" % name)


# ---------------------------------------------------------------------------
# 二十二、已中止是终态：取结果 / 取报告 / 取样例的说法必须一致
# ---------------------------------------------------------------------------

class TestAbortedAcrossCapabilities(ReaderCase):
    """三项能力遇到已中止任务都要说"已中止"，不能一处说终态、一处说"再等等"。"""

    def test_aborted_report_reports_aborted_not_unfinished(self):
        error = self.call_error("get_report", {"task_id": TASK_ABORTED, "format": "json"},
                                "ABORTED")
        self.assertIn("已中止", error["message"])
        self.assertIn("终态", error["message"])
        self.assertIn("重新发起", error["message"])
        self.assertEqual(error["requested"], "报告")
        self.assertEqual(error["status_cn"], "已中止")
        self.assertEqual(error["aborted_at"], "2026-10-01T12:04:00Z")
        self.assertNotIn("TASK_NOT_FINISHED", json.dumps(error))

    def test_aborted_samples_reports_aborted_not_unfinished(self):
        error = self.call_error(
            "get_samples", {"task_id": TASK_ABORTED, "sample_type": "cleaned",
                            "table": "movies", "limit": 2}, "ABORTED")
        self.assertIn("已中止", error["message"])
        self.assertIn("重新发起", error["message"])
        self.assertEqual(error["requested"], "样本")
        self.assertEqual(error["status"], "aborted")

    def test_aborted_markdown_report_also_aborted(self):
        # 两种格式都先回答"不会再有报告"，不因格式不同而说法不同
        self.call_error("get_report", {"task_id": TASK_ABORTED, "format": "md"}, "ABORTED")

    def test_unfinished_task_still_reports_not_finished(self):
        # 这三条证明只动了"已中止"，没顺手动别的结论
        self.call_error("get_report", {"task_id": TASK_RUNNING, "format": "md"},
                        "TASK_NOT_FINISHED")
        self.call_error("get_samples", {"task_id": TASK_RUNNING, "sample_type": "cleaned"},
                        "TASK_NOT_FINISHED")
        self.call_error("get_task_result", {"task_id": TASK_RUNNING}, "TASK_NOT_FINISHED")

    def test_failed_task_behaviour_unchanged(self):
        # 失败任务的报告仍由执行侧说"尚未生成"（不是我们改的那一类）
        self.call_error("get_report", {"task_id": TASK_FAILED, "format": "md"},
                        "TASK_NOT_FINISHED")

    def test_unknown_task_stays_not_found_across_all_three(self):
        for name, extra in (("get_task_result", {}),
                            ("get_report", {"format": "md"}),
                            ("get_samples", {"sample_type": "cleaned"})):
            args = {"task_id": "no-such-task-at-all"}
            args.update(extra)
            self.call_error(name, args, "TASK_NOT_FOUND")

    def test_aborted_task_status_query_still_works(self):
        # "已中止"只影响取用类能力；查状态照常能问（它本来就是回答"现在是什么状态"）
        payload = self.call_ok("get_task_status", {"task_id": TASK_ABORTED})
        self.assertEqual(payload["status"], "aborted")
        self.assertEqual(payload["status_cn"], "已中止")


class TestReasonKindUnifiedWithDownload(ReaderCase):
    """细类字段与下载路由同名同值：对端只需判一个字段、一套取值。"""

    def test_field_name_and_values_are_the_unified_ones(self):
        control = self.call_error(
            "read_task_file", {"task_id": TASK_FULL, "path": "cancel_request.json"},
            "FORBIDDEN_PATH")
        self.assertEqual(control["reason_kind"], "control")
        sidecar = self.call_error(
            "read_task_file",
            {"task_id": TASK_FULL,
             "path": "cleaned/%s/.ratings.dat.raw.crc" % DATA_VERSION},
            "FORBIDDEN_PATH")
        self.assertEqual(sidecar["reason_kind"], "sidecar")
        residue = self.call_error(
            "read_task_file", {"task_id": TASK_FULL, "path": "logs/published_hashes.tmp"},
            "FORBIDDEN_PATH")
        self.assertEqual(residue["reason_kind"], "residue")

    def test_old_field_name_is_gone(self):
        error = self.call_error(
            "read_task_file", {"task_id": TASK_FULL, "path": "cancel_request.json"},
            "FORBIDDEN_PATH")
        self.assertNotIn("exclusion_kind", error,
                         "旧字段名不该同时存在：两个名字会让对端以为要判两个字段")

    def test_directory_and_non_regular_use_the_same_field(self):
        directory = self.call_error("read_task_file",
                                    {"task_id": TASK_FULL, "path": "cleaned"},
                                    "FILE_NOT_FOUND")
        self.assertEqual(directory["reason_kind"], "directory")
        if self.has_fifo:
            pipe = self.call_error(
                "read_task_file", {"task_id": TASK_PIPE, "path": "pipe.json"},
                "PRODUCT_INVALID")
            self.assertEqual(pipe["reason_kind"], "not_regular_file")

    def test_all_exclusion_reasons_map_to_the_unified_labels(self):
        labels = set(readers.EXCLUSION_KINDS.values())
        self.assertEqual(labels, {"control", "sidecar", "residue"},
                         "三个排除原因各对应一个细类，取值见模块顶部")

    def test_the_whole_service_has_only_one_reason_value_set(self):
        self.assertEqual(set(readers.REASON_KINDS),
                         {"control", "sidecar", "residue", "directory", "not_regular_file"},
                         "细类取值只有这一套：排除三类 + 不是文件两种")
