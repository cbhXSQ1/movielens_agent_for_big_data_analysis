# -*- coding: utf-8 -*-
"""M3a 测试：维表 Streaming 作业的本地 stdin→stdout 行为（plan §7.2、§9.5）。

**真正以子进程方式**运行 `hadoop/jobs/*.py`，用管道喂数据、读 stdout/stderr，
不用 Hadoop。这是 plan §9.5 的硬要求：「所有作业脚本必须通过本地 stdin→stdout
测试后才允许上集群」。

shuffle 由本模块模拟：按 key 排序后分组，再交给同一个脚本的 `--reduce` 形态。
因为 `group_stage_one` 在组内**自己**按原始行排序，value 的到达顺序不影响结果 ——
这正好也把「reduce 结果与 value 顺序无关」这条不变量测掉了。
"""
import io
import json
import os
import re
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.pipeline import (TABLE_FILES, read_raw_table,  # noqa: E402
                             strip_final_prefix)

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
JOBS = os.path.join(REPO_ROOT, "hadoop", "jobs")
FIXTURES = os.path.join(TESTS_DIR, "fixtures")
RULES = os.path.join(REPO_ROOT, "config", "cleaning_rules.v1.json")
SCORING = os.path.join(REPO_ROOT, "config", "scoring_scheme.v1.json")

PY = sys.executable or "python3"


def run_job(script, args, text):
    """以子进程跑一个作业脚本，返回 (stdout, stderr, returncode)。"""
    cmd = [PY, os.path.join(JOBS, script),
           "--rules", RULES, "--scoring", SCORING,
           "--task-id", "T-JOB", "--processed-at", "2026-09-24T00:00:00Z"] + list(args)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, cwd=JOBS)
    out, err = proc.communicate(text.encode("iso-8859-1"))
    return (out.decode("iso-8859-1"), err.decode("iso-8859-1"), proc.returncode)


def numbered(raw_dir, table):
    """把原始表变成 driver 上传的 `<line_no>\\t<raw_line>` 形态（D-012）。"""
    return "".join("%d\t%s\n" % (n, raw) for n, raw in
                   read_raw_table(os.path.join(raw_dir, TABLE_FILES[table])))


def shuffle(text, reduce_args=None):
    """模拟 Hadoop 的 shuffle：按 key 排序后交给 --reduce 形态。

    Hadoop 保证同一 key 的 value 连续且按键有序；此处按键的字典序排序，
    组内 value 顺序故意保持「输入顺序」，以验证 reduce 结果与 value 到达顺序无关。
    """
    rows = sorted(l for l in text.split("\n") if l)
    return ("\n".join(rows) + "\n") if rows else ""


def counters(err):
    """解析 stderr 上的 `reporter:counter:group,name,n` 行。"""
    out = {}
    for m in re.finditer(r"^reporter:counter:([^,]+),([^,]+),(\d+)$", err, re.M):
        out[(m.group(1), m.group(2))] = out.get((m.group(1), m.group(2)), 0) + int(m.group(3))
    return out


def lines_of(text):
    return [l for l in text.split("\n") if l]


def fields_of(internal_line):
    return json.loads(internal_line)["f"]


class StreamingCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = os.path.join(FIXTURES, "raw")
        cls.expected = os.path.join(FIXTURES, "expected", "cleaned")

    def expected_lines(self, table):
        with io.open(os.path.join(self.expected, "%s.dat" % table),
                     encoding="iso-8859-1") as fh:
            return [l for l in fh.read().split("\n") if l]

    def finalize(self, table, records_text):
        """mapper → shuffle → reducer → 剥离零填充键，得到交付格式的 cleaned 表。

        集群上这一步的剥离由 driver 做（见 engine/pipeline.py 的 FINAL_KEYS 注释）。
        """
        out, err, rc = run_job("clean_finalize.py", ["--table", table], records_text)
        self.assertEqual(0, rc, err)
        out2, err2, rc2 = run_job("clean_finalize.py", ["--table", table, "--reduce"],
                                  shuffle(out, None))
        self.assertEqual(0, rc2, err2)
        return [strip_final_prefix(table, l) for l in lines_of(out2)]

    def dim_keep(self, table):
        """跑完该维表的 keep 链，返回内部记录文本与累计计数器。"""
        acc = {}
        if table == "users":
            text = numbered(self.raw, "users")
            out, err, rc = run_job("users_normalize.py", ["--mode", "keep"], text)
            self.assertEqual(0, rc, err)
            _merge(acc, counters(err))
            out, err, rc = run_job("users_resolve.py", [], out)
            self.assertEqual(0, rc, err)
            out, err, rc = run_job("users_resolve.py", ["--reduce"],
                                   shuffle(out, None))
            self.assertEqual(0, rc, err)
            _merge(acc, counters(err))
            return out, acc
        if table == "movies":
            text = numbered(self.raw, "movies")
            out, err, rc = run_job("movies_normalize.py", ["--mode", "keep"], text)
            self.assertEqual(0, rc, err)
            _merge(acc, counters(err))
            out, err, rc = run_job("movies_resolve.py", [], out)
            self.assertEqual(0, rc, err)
            out, err, rc = run_job("movies_resolve.py", ["--reduce"], shuffle(out, None))
            self.assertEqual(0, rc, err)
            _merge(acc, counters(err))
            out, err, rc = run_job("movies_residual.py", ["--mode", "keep"], out)
            self.assertEqual(0, rc, err)
            _merge(acc, counters(err))
            return out, acc
        raise AssertionError(table)


def _merge(acc, new):
    for k, v in new.items():
        acc[k] = acc.get(k, 0) + v


class TestUsersNormalize(StreamingCase):
    def test_keep_output_has_line_numbers_and_original_raw(self):
        text = numbered(self.raw, "users")
        out, err, rc = run_job("users_normalize.py", ["--mode", "keep"], text)
        self.assertEqual(0, rc, err)
        rows = [json.loads(l) for l in lines_of(out)]
        # 15 行 - P2 1 - P3 1 - U1 1 = 12（U5 属下一趟，此处尚未去重）
        self.assertEqual(12, len(rows))
        for r in rows:
            self.assertIn("n", r)
            self.assertIn("raw", r)
            self.assertIn("f", r)
            self.assertTrue(r["raw"])

    def test_quarantine_counts(self):
        text = numbered(self.raw, "users")
        out, err, rc = run_job("users_normalize.py", ["--mode", "quarantine"], text)
        self.assertEqual(0, rc, err)
        self.assertEqual(3, len(lines_of(out)))          # P2 1 + P3 1 + U1 1
        c = counters(err)
        self.assertEqual(1, c[("quarantine", "P2")])
        self.assertEqual(1, c[("quarantine", "P3")])
        self.assertEqual(1, c[("quarantine", "U1")])
        for rec in [json.loads(l) for l in lines_of(out)]:
            self.assertEqual("users.dat", rec["source_file"])
            self.assertEqual("T-JOB", rec["task_id"])

    def test_modes_are_complementary(self):
        """plan §5.1：两模式判定互补，每一行恰好被一边处理一次。"""
        text = numbered(self.raw, "users")
        keep, _, _ = run_job("users_normalize.py", ["--mode", "keep"], text)
        quar, _, _ = run_job("users_normalize.py", ["--mode", "quarantine"], text)
        total = len(lines_of(text))
        self.assertEqual(total, len(lines_of(keep)) + len(lines_of(quar)))

    def test_u2_blanking_and_u3_zip_repair_counters(self):
        text = numbered(self.raw, "users")
        _, err, _ = run_job("users_normalize.py", ["--mode", "keep"], text)
        c = counters(err)
        self.assertEqual(3, c[("marks", "U2")])            # 5/6/7 各一处非法属性
        self.assertEqual(3, c[("marks", "U3")])            # 8 ZIP+4 / 9 / 10
        self.assertEqual(1, c[("fix", "U3_zip_plus4")])
        self.assertEqual(3, c[("detail", "U2_blank_fields")])
        self.assertEqual(2, c[("detail", "U3_blank_fields")])


class TestUsersFullChain(StreamingCase):
    def test_cleaned_users_matches_local_runner(self):
        out, acc = self.dim_keep("users")
        self.assertEqual(self.expected_lines("users"), self.finalize("users", out))
        self.assertEqual(2, acc[("dedupe", "users")])      # 3 完全重复 + 4 冲突

    def test_users_quarantine_total(self):
        text = numbered(self.raw, "users")
        _, err, _ = run_job("users_normalize.py", ["--mode", "quarantine"], text)
        _q, err2, _ = run_job("users_resolve.py", ["--reduce", "--mode", "quarantine"],
                              shuffle(_resolve_input("users", text)))
        c = counters(err)
        _merge(c, counters(err2))
        self.assertEqual(1, c[("quarantine", "U1")])
        self.assertEqual(0, c.get(("quarantine", "U5"), 0),
                         "U5 属去重，不报隔离计数器")

    def test_resolve_mapper_rejects_quarantine_mode(self):
        """分区趟不接受 --mode quarantine：判重只在 reduce 趟发生，必须落错而非静默空输出。"""
        text = numbered(self.raw, "users")
        _o, err, rc = run_job("users_resolve.py", ["--mode", "quarantine"],
                              _resolve_input("users", text))
        self.assertNotEqual(0, rc)
        self.assertIn("--reduce", err)


def _resolve_input(table, text):
    """跑一遍 normalize 的 mapper，得到 resolve 的输入。"""
    script = "users_normalize.py" if table == "users" else "movies_normalize.py"
    out, err, rc = run_job(script, ["--mode", "keep"], text)
    assert rc == 0, err
    mapper = "users_resolve.py" if table == "users" else "movies_resolve.py"
    out2, err2, rc2 = run_job(mapper, [], out)
    assert rc2 == 0, err2
    return out2


class TestMoviesFullChain(StreamingCase):
    def test_cleaned_movies_matches_local_runner(self):
        out, acc = self.dim_keep("movies")
        self.assertEqual(self.expected_lines("movies"), self.finalize("movies", out))
        self.assertEqual(2, acc[("dedupe", "movies")])      # 6 完全重复 + 7 冲突
        self.assertEqual(2, acc[("fix", "P1_text")])
        self.assertEqual(1, acc[("fix", "M2_strip")])
        self.assertEqual(1, acc[("marks", "M6")])
        self.assertEqual(2, acc[("marks", "M7")])
        self.assertEqual(2, acc[("detail", "M9_checked")])

    def test_movies_quarantine_by_rule(self):
        """三趟隔离各管一段：normalize 管 P2/P3/M1，resolve 管 M4（去重），
        residual 管 M3。计数器必须落在正确的趟里。"""
        text = numbered(self.raw, "movies")
        q1, e1, _ = run_job("movies_normalize.py", ["--mode", "quarantine"], text)
        kept1, e2, _ = run_job("movies_normalize.py", ["--mode", "keep"], text)
        mapped, e3, _ = run_job("movies_resolve.py", [], kept1)
        kept2, e4, _ = run_job("movies_resolve.py", ["--reduce"], shuffle(mapped))
        q2, e5, _ = run_job("movies_resolve.py", ["--reduce", "--mode", "quarantine"],
                            shuffle(mapped))
        q3, e6, _ = run_job("movies_residual.py", ["--mode", "quarantine"], kept2)
        c = {}
        for e in (e1, e2, e3, e4, e5, e6):
            _merge(c, counters(e))

        self.assertEqual(1, c[("quarantine", "P2")])
        self.assertEqual(1, c[("quarantine", "P3")])
        self.assertEqual(1, c[("quarantine", "M1")])
        self.assertEqual(1, c[("quarantine", "M3")])
        self.assertEqual(2, c[("dedupe", "movies")])          # 6 完全重复 1 条 + 7 冲突 1 条
        self.assertNotIn(("quarantine", "M4"), c, "M4 属去重，不报隔离计数器")
        self.assertEqual(3, len(lines_of(q1)), "P2/P3/M1 各一条都在 normalize 隔离趟")
        self.assertEqual(2, len(lines_of(q2)),
                         "6 号完全重复副本 + 7 号冲突副本，两条都从 resolve 隔离趟输出")
        self.assertEqual(1, len(lines_of(q3)), "17 号空标题应从 residual 隔离趟输出")
        # 每一阶段 keep + quarantine 覆盖全部输入
        self.assertEqual(len(lines_of(text)),
                         len(lines_of(kept1)) + len(lines_of(q1)))


class TestDeterminism(StreamingCase):
    def test_resolve_result_independent_of_value_order(self):
        """reduce 的 values 顺序不影响结果（plan §5.1 的确定性要求）。"""
        text = numbered(self.raw, "movies")
        mid = _resolve_input("movies", text)
        rows = lines_of(mid)
        _out1, _e1, rc1 = run_job("movies_resolve.py", ["--reduce"], shuffle(mid))
        self.assertEqual(0, rc1)
        # 同一批 value 逆序送入：key 分组不变，组内顺序被打乱
        _out2, _e2, rc2 = run_job("movies_resolve.py", ["--reduce"],
                                  "".join(l + "\n" for l in reversed(rows)))
        self.assertEqual(0, rc2)
        # 逐组比对：组内排序后取优，故两次结果必须一致
        a = sorted(fields_of(l)["MovieID"] + "::" + fields_of(l)["Title"]
                   + "::" + fields_of(l)["Genres"] for l in lines_of(_out1))
        b = sorted(fields_of(l)["MovieID"] + "::" + fields_of(l)["Title"]
                   + "::" + fields_of(l)["Genres"] for l in lines_of(_out2))
        self.assertEqual(a, b)

    def test_finalize_sorts_numerically_not_lexicographically(self):
        """零填充业务键让 Text 字典序等于数值序（D-012 问题 3）。"""
        recs = []
        for uid in ("2", "10", "1"):
            recs.append(json.dumps({"n": 1, "raw": "x",
                                    "f": {"UserID": uid, "Gender": "F", "Age": "1",
                                          "Occupation": "1", "Zip-code": "12345"}},
                                   ensure_ascii=True, sort_keys=True,
                                   separators=(",", ":")))
        out, err, rc = run_job("clean_finalize.py", ["--table", "users"],
                               "\n".join(recs) + "\n")
        self.assertEqual(0, rc, err)
        out2, err2, rc2 = run_job("clean_finalize.py", ["--table", "users", "--reduce"],
                                  shuffle(out, None))
        self.assertEqual(0, rc2, err2)
        rows = [strip_final_prefix("users", l) for l in lines_of(out2)]
        uids = [l.split("::")[0] for l in rows]
        self.assertEqual(["1", "2", "10"], uids,
                         "必须是数值序；字典序会给出 1,10,2")
        # 前缀是定宽零填充键 + TAB，宽度必须与 driver 的剥离逻辑一致
        from engine.pipeline import final_prefix_len
        for raw in lines_of(out2):
            self.assertEqual("\t", raw[final_prefix_len("users") - 1])
            self.assertTrue(raw[:final_prefix_len("users") - 1].isdigit())


class TestJobErrors(unittest.TestCase):
    def test_input_without_line_number_prefix_fails_loudly(self):
        out, err, rc = run_job("users_normalize.py", ["--mode", "keep"],
                               "1::F::1::10::48067\n")
        self.assertNotEqual(0, rc, "缺行号前缀必须报错，不能静默按行号 0 处理")
        self.assertIn("FATAL", err)

    def test_finalize_requires_valid_table(self):
        out, err, rc = run_job("clean_finalize.py", ["--table", "bogus"], "")
        self.assertNotEqual(0, rc)
        self.assertIn("--table", err)


if __name__ == "__main__":
    unittest.main()
