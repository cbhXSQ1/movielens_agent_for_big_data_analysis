# -*- coding: utf-8 -*-
"""M3a 测试：维表 Streaming 作业的本地 stdin→stdout 行为（D-014 单趟双流版）。

**真正以子进程方式**运行 `hadoop/jobs/*.py`。D-014 之后每个作业**一趟**同时产出
K（保留）/ Q（隔离）/ D（去重移除）三种标签流，本文件用 `kinds()` 分桶断言。

shuffle 由本模块模拟：按 key 排序后分组，再交给同一个脚本的 `--reduce` 形态；
组内 value 顺序故意打乱，以验证 reduce 结果与 value 到达顺序无关。
"""
import io
import json
import os
import re
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.pipeline import TABLE_FILES, read_raw_table  # noqa: E402

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


def shuffle(text):
    """模拟 Hadoop 的 shuffle：按 key 排序后交给 --reduce 形态。"""
    rows = sorted(l for l in text.split("\n") if l)
    return ("\n".join(rows) + "\n") if rows else ""


def shuffle_within_keys(text):
    """保持 key 有序、把**同一 key 组内**的 value 逆序。

    这是 Hadoop shuffle 允许的不确定性范围：同 key 的 value 连续且按键有序，
    组内顺序不作保证。跨 key 打乱会破坏分组前提，那是测试构造错误。
    """
    groups = []
    for line in sorted(l for l in text.split("\n") if l):
        key = line.partition("\t")[0]
        if not groups or groups[-1][0] != key:
            groups.append([key, []])
        groups[-1][1].append(line)
    out = []
    for _, items in groups:
        out.extend(reversed(items))
    return "\n".join(out) + "\n"


def counters(err):
    """解析 stderr 上的 `reporter:counter:group,name,n` 行。"""
    out = {}
    for m in re.finditer(r"^reporter:counter:([^,]+),([^,]+),(\d+)$", err, re.M):
        out[(m.group(1), m.group(2))] = out.get((m.group(1), m.group(2)), 0) + int(m.group(3))
    return out


def lines_of(text):
    return [l for l in text.split("\n") if l]


def kinds(text):
    """内部标签流（D-014）：按 K/Q/D 分桶。"""
    out = {"K": [], "Q": [], "D": []}
    for l in lines_of(text):
        if len(l) >= 3 and l[1] == "\t" and l[0] in out:
            out[l[0]].append(l[2:])
        else:
            raise AssertionError("内部流行缺少类型标签：%r" % l[:80])
    return out


def k_lines(text):
    return kinds(text)["K"]


def q_lines(text):
    return kinds(text)["Q"]


def fields_of(payload):
    return json.loads(payload)["f"]


def _merge(acc, new):
    for k, v in new.items():
        acc[k] = acc.get(k, 0) + v


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
        """mapper → shuffle → reducer → 剥离零填充键，得到交付格式的 cleaned 表。"""
        from engine.pipeline import strip_final_prefix
        out, err, rc = run_job("clean_finalize.py", ["--table", table], records_text)
        self.assertEqual(0, rc, err)
        out2, err2, rc2 = run_job("clean_finalize.py", ["--table", table, "--reduce"],
                                  shuffle(out))
        self.assertEqual(0, rc2, err2)
        # finalize 的 reducer 输出是纯交付行（无标签、无零填充键）
        return [strip_final_prefix(table, l) for l in lines_of(out2)]

    def dim_keep(self, table):
        """跑完该维表的单趟链，返回 K 流文本与累计计数器。"""
        acc = {}
        if table == "users":
            text = numbered(self.raw, "users")
            out, err, rc = run_job("users_normalize.py", [], text)
            self.assertEqual(0, rc, err)
            _merge(acc, counters(err))
            mapped, err, rc = run_job("users_resolve.py", [], out)
            self.assertEqual(0, rc, err)
            out, err, rc = run_job("users_resolve.py", ["--reduce"], shuffle(mapped))
            self.assertEqual(0, rc, err)
            _merge(acc, counters(err))
            return out, acc
        if table == "movies":
            text = numbered(self.raw, "movies")
            out, err, rc = run_job("movies_normalize.py", [], text)
            self.assertEqual(0, rc, err)
            _merge(acc, counters(err))
            mapped, err, rc = run_job("movies_resolve.py", [], out)
            self.assertEqual(0, rc, err)
            out, err, rc = run_job("movies_resolve.py", ["--reduce"], shuffle(mapped))
            self.assertEqual(0, rc, err)
            _merge(acc, counters(err))
            out, err, rc = run_job("movies_residual.py", [], out)
            self.assertEqual(0, rc, err)
            _merge(acc, counters(err))
            return out, acc
        raise AssertionError(table)


class TestUsersNormalize(StreamingCase):
    """单趟双流：一趟同时出 K（保留）与 Q（隔离）。"""

    def test_single_pass_outputs_both_streams(self):
        text = numbered(self.raw, "users")
        out, err, rc = run_job("users_normalize.py", [], text)
        self.assertEqual(0, rc, err)
        k = k_lines(out)
        q = q_lines(out)
        # 15 行 - P2 1 - P3 1 - U1 1 = 12 条保留；隔离 3 条
        self.assertEqual(12, len(k))
        self.assertEqual(3, len(q))
        for r in k:
            obj = json.loads(r)
            self.assertIn("n", obj)
            self.assertIn("raw", obj)
            self.assertIn("f", obj)
            self.assertTrue(obj["raw"])

    def test_quarantine_records_and_counters(self):
        text = numbered(self.raw, "users")
        out, err, rc = run_job("users_normalize.py", [], text)
        self.assertEqual(0, rc, err)
        c = counters(err)
        self.assertEqual(1, c[("quarantine", "P2")])
        self.assertEqual(1, c[("quarantine", "P3")])
        self.assertEqual(1, c[("quarantine", "U1")])
        for payload in q_lines(out):
            rec = json.loads(payload)
            self.assertEqual("users.dat", rec["source_file"])
            self.assertEqual("T-JOB", rec["task_id"])

    def test_streams_are_complementary(self):
        """K + Q 恰好覆盖全部输入行（与原先两趟互补一致）。"""
        text = numbered(self.raw, "users")
        out, _err, _rc = run_job("users_normalize.py", [], text)
        self.assertEqual(len(lines_of(text)),
                         len(k_lines(out)) + len(q_lines(out)) + len(kinds(out)["D"]),
                         "stdout 前几行=%r rc=%s err=%r" % (out.split("\n")[:3], _rc, _err[-200:]))

    def test_u2_blanking_and_u3_zip_repair_counters(self):
        text = numbered(self.raw, "users")
        _out, err, rc = run_job("users_normalize.py", [], text)
        self.assertEqual(0, rc, err)
        c = counters(err)
        self.assertEqual(3, c[("marks", "U2")])
        self.assertEqual(3, c[("marks", "U3")])
        self.assertEqual(1, c[("fix", "U3_zip_plus4")])
        self.assertEqual(3, c[("detail", "U2_blank_fields")])
        self.assertEqual(2, c[("detail", "U3_blank_fields")])


class TestUsersFullChain(StreamingCase):
    def test_cleaned_users_matches_local_runner(self):
        out, acc = self.dim_keep("users")
        self.assertEqual(self.expected_lines("users"), self.finalize("users", out))
        self.assertEqual(2, acc[("dedupe", "users")])      # 3 完全重复 + 4 冲突

    def test_quarantine_flows_through_resolve(self):
        """normalize 的 Q 流被 resolve 原样转发；去重移除走 D 流。"""
        text = numbered(self.raw, "users")
        out_n, err_n, rc = run_job("users_normalize.py", [], text)
        self.assertEqual(0, rc, err_n)
        mapped, _e, rc = run_job("users_resolve.py", [], out_n)
        self.assertEqual(0, rc, _e)
        out_r, err_r, rc = run_job("users_resolve.py", ["--reduce"], shuffle(mapped))
        self.assertEqual(0, rc, err_r)
        kn, qn, dn = (k_lines(out_n), q_lines(out_n), kinds(out_n)["D"])
        kr, qr, dr = (k_lines(out_r), q_lines(out_r), kinds(out_r)["D"])
        self.assertEqual(3, len(qn))                 # P2/P3/U1
        self.assertEqual(3, len(qr))                 # 被原样转发
        self.assertEqual(2, len(dr))                 # 用户 3 重复 + 用户 4 冲突移除
        self.assertEqual(10, len(kr))                # 去重后剩 10 条保留
        c = counters(err_r)
        self.assertEqual(2, c[("dedupe", "users")])
        self.assertEqual(0, c.get(("quarantine", "U5"), 0),
                         "U5 属去重，不报隔离计数器")


class TestMoviesPipeline(StreamingCase):
    def test_movies_streams_by_stage(self):
        """normalize 出 P2/P3/M1 的 Q；resolve 出 M4 的 D；residual 出 M3 的 Q。"""
        text = numbered(self.raw, "movies")
        c = {}
        out1, e1, rc = run_job("movies_normalize.py", [], text)
        self.assertEqual(0, rc, e1)
        _merge(c, counters(e1))
        self.assertEqual(3, len(q_lines(out1)))        # P2/P3/M1
        self.assertEqual(17, len(k_lines(out1)))
        mapped, _e, rc = run_job("movies_resolve.py", [], out1)
        self.assertEqual(0, rc, _e)
        out2, e2, rc = run_job("movies_resolve.py", ["--reduce"], shuffle(mapped))
        self.assertEqual(0, rc, e2)
        _merge(c, counters(e2))
        self.assertEqual(2, len(kinds(out2)["D"]))     # 6 完全重复 + 7 冲突副本
        out3, e3, rc = run_job("movies_residual.py", [], out2)
        self.assertEqual(0, rc, e3)
        _merge(c, counters(e3))
        self.assertEqual(4, len(q_lines(out3)))        # 原 3 条 + M3（17 号空标题）
        self.assertEqual(14, len(k_lines(out3)))

        self.assertEqual(1, c[("quarantine", "P2")])
        self.assertEqual(1, c[("quarantine", "P3")])
        self.assertEqual(1, c[("quarantine", "M1")])
        self.assertEqual(1, c[("quarantine", "M3")])
        self.assertEqual(2, c[("dedupe", "movies")])
        self.assertNotIn(("quarantine", "M4"), c, "M4 属去重，不报隔离计数器")

    def test_cleaned_movies_matches_local_runner(self):
        out, acc = self.dim_keep("movies")
        self.assertEqual(self.expected_lines("movies"), self.finalize("movies", out))
        self.assertEqual(2, acc[("fix", "P1_text")])
        self.assertEqual(1, acc[("fix", "M2_strip")])
        self.assertEqual(1, acc[("marks", "M6")])
        self.assertEqual(2, acc[("marks", "M7")])
        self.assertEqual(2, acc[("detail", "M9_checked")])


class TestDeterminism(StreamingCase):
    def test_resolve_result_independent_of_value_order(self):
        """组内 value 逆序不得改变结果（plan §5.1 的确定性要求）。"""
        text = numbered(self.raw, "movies")
        out1, e1, rc = run_job("movies_normalize.py", [], text)
        self.assertEqual(0, rc, e1)
        mapped, _e, rc = run_job("movies_resolve.py", [], out1)
        self.assertEqual(0, rc, _e)
        a, _, _ = run_job("movies_resolve.py", ["--reduce"], shuffle(mapped))
        b, _, _ = run_job("movies_resolve.py", ["--reduce"], shuffle_within_keys(mapped))
        key = lambda t: sorted(fields_of(l)["MovieID"] + "::" + fields_of(l)["Title"]
                               + "::" + fields_of(l)["Genres"] for l in k_lines(t))
        self.assertEqual(key(a), key(b))

    def test_finalize_sorts_numerically_not_lexicographically(self):
        """零填充业务键让 Text 字典序等于数值序（D-012 问题 3）。"""
        from engine.pipeline import final_prefix_len, strip_final_prefix
        recs = []
        for uid in ("2", "10", "1"):
            recs.append("K\t" + json.dumps({"n": 1, "raw": "x",
                                            "f": {"UserID": uid, "Gender": "F",
                                                  "Age": "1", "Occupation": "1",
                                                  "Zip-code": "12345"}},
                                           ensure_ascii=True, sort_keys=True,
                                           separators=(",", ":")))
        out, err, rc = run_job("clean_finalize.py", ["--table", "users"],
                               "\n".join(recs) + "\n")
        self.assertEqual(0, rc, err)
        out2, err2, rc2 = run_job("clean_finalize.py", ["--table", "users", "--reduce"],
                                  shuffle(out))
        self.assertEqual(0, rc2, err2)
        rows = [strip_final_prefix("users", l) for l in lines_of(out2)]
        uids = [l.split("::")[0] for l in rows]
        self.assertEqual(["1", "2", "10"], uids,
                         "必须是数值序；字典序会给出 1,10,2")
        for raw in lines_of(out2):
            self.assertEqual("\t", raw[final_prefix_len("users") - 1])
            self.assertTrue(raw[:final_prefix_len("users") - 1].isdigit())

    def test_final_prefix_len_matches_multi_key_tables(self):
        from engine.pipeline import FINAL_PAD, final_prefix_len
        self.assertEqual(FINAL_PAD + 1, final_prefix_len("users"))
        self.assertEqual(FINAL_PAD + 1, final_prefix_len("movies"))
        self.assertEqual(FINAL_PAD * 3 + 2 * 2 + 1, final_prefix_len("ratings"))


class TestJobErrors(unittest.TestCase):
    def test_input_without_line_number_prefix_fails_loudly(self):
        out, err, rc = run_job("users_normalize.py", [],
                               "1::F::1::10::48067\n")
        self.assertNotEqual(0, rc, "缺行号前缀必须报错，不能静默按行号 0 处理")
        self.assertIn("FATAL", err)

    def test_finalize_requires_valid_table(self):
        out, err, rc = run_job("clean_finalize.py", ["--table", "bogus"], "")
        self.assertNotEqual(0, rc)
        self.assertIn("--table", err)


if __name__ == "__main__":
    unittest.main()