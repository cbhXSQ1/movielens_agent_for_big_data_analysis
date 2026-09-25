#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 §7.2 的 fixture 与期望输出（tests/fixtures/）。

为什么用脚本而不是直接写文件：**编码**。三表是 ISO-8859-1 的，而本仓库的文件都是
UTF-8；直接手写会出现 'LÃ©on' 被存成 UTF-8 的 C3 83 C2 A9，再按 ISO-8859-1 读回来
变成 'LÃƒÂ©on'，fixture 就整个失真了。这里统一用 encoding='iso-8859-1' 落盘。

RAW 与 EXPECTED 的内容全部是**手写**的：期望值由人逐行推导（见各处注释），
不由被测实现生成 —— 否则测试就只是在复述实现。
"""
import io
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# 输入：刻意注入每一类问题（对应 §7.2「覆盖每类注入问题」）
# ---------------------------------------------------------------------------

# users.dat: UserID::Gender::Age::Occupation::Zip-code
#   1,2,3,4,5,6,7,8,9,10        正常 ID
#   U2: 5 Gender=X / 6 Age=30(非法档) / 7 Occupation=99
#   U3: 8 ZIP+4 / 9 'ABCDE' / 10 '123'
#   U5: 3 完全重复 / 4 属性冲突(Occupation 7 vs 1，两项得分并列→字段级合并)
#   U1: 2006040 越界假 ID
#   P3: 11 多一个字段      P2: 12 逗号分隔、无 '::'
USERS_RAW = """1::F::1::10::48067
2::M::56::16::70072
3::M::25::15::55117
3::M::25::15::55117
4::F::45::7::02460
4::F::45::1::02460
5::X::45::7::02460
6::F::30::7::02460
7::F::45::99::02460
8::F::45::7::19087-3622
9::F::45::7::ABCDE
10::F::45::7::123
2006040::F::45::7::99999
11::M::18::4::78701::EXTRA
12,M,25,15,55117
"""

# movies.dat: MovieID::Title::Genres
#   3  M2 首尾空格     4  P1 双重编码      5  P1 HTML 实体（'&#8230;' → '…' → '...'）
#   6  M4 完全重复     7  M4 冲突（[20XX]+UnknownGenre 副本 vs 合法记录）
#   2003952 M1 假 ID   11 P3 多字段        12 P2 逗号分隔
#   13 M7 非法类型     14 M9 空 token      15 M6 缺年份   16 M9 重复 token
#   9,10 M8 同名不同 ID   17 M3 空标题
MOVIES_RAW = """1::Toy Story (1995)::Animation|Children's|Comedy
2::Jumanji (1995)::Adventure|Children's|Fantasy
3::  Grumpier Old Men (1995)  ::Comedy|Romance
4::L\u00c3\u00a9on / Am\u00c3\u00a9lie (1994)::Drama
5::And God Created Woman (Et Dieu&#8230;Cr\u00e9a la Femme) (1956)::Drama
6::Waiting to Exhale (1995)::Comedy|Drama
6::Waiting to Exhale (1995)::Comedy|Drama
7::Sabrina (1995)::Comedy|Romance
7::Movie with malformed year [20XX]::UnknownGenre
2003952::Fake Movie (2000)::Drama
8::Sudden Death (1995)::Action
9::Three Wishes (1995)::Comedy|Drama
10::Three Wishes (1995)::Drama
11::GoldenEye (1995)::Action|Adventure|Thriller::EXTRA
12,Four Rooms (1995),Comedy
13::Bad Genre (2000)::Action|NotAGenre
14::Empty Genre (2000)::Action|
15::No Year Here::Drama
16::Dup Genre (2000)::Action|Action
17::::Drama
"""

# ratings.dat: UserID::MovieID::Rating::Timestamp
#   R2: 7 评分0 / 8 评分6 / 9 评分3.5 / 10 'five' / 11 空评分
#       注意：空字段要写 '::::'（四个冒号）。写成 ':::' 时 '::' 切分会塌成 3 段，
#       那属于 P3（字段数异常）而不是 R2 —— fixture 曾经踩过这个坑。
#   R3: 12 非整数时间戳        R4: 13 毫秒时间戳 1009669071000 → 1009669071
#   R5: 14 时间戳 -1 / 15 2100 年
#   R1: 16 空 UserID / 17 空 MovieID
#   R6: 18,19 与第 1 行同键（(1,1,978824268)）
#   X1: 20 孤儿用户 999        X2: 21 孤儿电影 999
#   P2: 22 逗号分隔            P3: 23 多一个字段
#   R8: 24,25 同一 (用户,电影) 两个不同时间戳 → 标记（不删）
RATINGS_RAW = """1::1::5::978824268
1::2::4::978824268
1::3::3::978824268
2::1::4::978824268
2::2::5::978824268
2::13::3::978824268
1::1::0::978824268
1::1::6::978824268
1::1::3.5::978824268
1::1::five::978824268
1::1::::978824268
1::1::4::five
1::1::4::1009669071000
1::1::4::-1
1::1::4::4102444800
::1::4::978824268
1::::4::978824268
1::1::5::978824268
1::1::5::978824268
999::1::4::978824268
1::999::4::978824268
1,2,4,978824268
1::2::4::978824268::EXTRA
3::1::5::978824268
3::1::3::978824269
"""

# ---------------------------------------------------------------------------
# 期望输出（逐行推导）
# ---------------------------------------------------------------------------

# users：10 条。
#   3 去重后 1 条；4 并列 → 字段级合并（Occupation 冲突置空）；
#   5 Gender 置空；6 Age 置空；7 Occupation 置空；
#   8 ZIP+4 截断；9/10 无法修复 → 置空。按 UserID 整数序输出。
USERS_EXPECTED = """1::F::1::10::48067
2::M::56::16::70072
3::M::25::15::55117
4::F::45::::02460
5::::45::7::02460
6::F::::7::02460
7::F::45::::02460
8::F::45::7::19087
9::F::45::7::
10::F::45::7::
"""

# movies：14 条。
#   3 去空格；4 双重编码还原；5 实体解码 + '…'→'...'（Latin-1 不可表示）；
#   6 去重；7 冲突保留合法记录；13/14/15/16 为标记/检查项，原样保留。
MOVIES_EXPECTED = """1::Toy Story (1995)::Animation|Children's|Comedy
2::Jumanji (1995)::Adventure|Children's|Fantasy
3::Grumpier Old Men (1995)::Comedy|Romance
4::L\u00e9on / Am\u00e9lie (1994)::Drama
5::And God Created Woman (Et Dieu...Cr\u00e9a la Femme) (1956)::Drama
6::Waiting to Exhale (1995)::Comedy|Drama
7::Sabrina (1995)::Comedy|Romance
8::Sudden Death (1995)::Action
9::Three Wishes (1995)::Comedy|Drama
10::Three Wishes (1995)::Drama
13::Bad Genre (2000)::Action|NotAGenre
14::Empty Genre (2000)::Action|
15::No Year Here::Drama
16::Dup Genre (2000)::Action|Action
"""

# ratings：9 条，按 (UserID, MovieID, Timestamp) 整数序。
#   (1,1,978824268) 有 3 行（1/18/19，内容完全相同）→ 保留原始行字典序最小者即第 1 行；
#   第 13 行被 R4 修复为 1009669071，故排在 978824268 之后。
RATINGS_EXPECTED = """1::1::5::978824268
1::1::4::1009669071
1::2::4::978824268
1::3::3::978824268
2::1::4::978824268
2::2::5::978824268
2::13::3::978824268
3::1::5::978824268
3::1::3::978824269
"""

COUNTS_EXPECTED = {
    "input": {"ratings_lines": 25, "users_lines": 15, "movies_lines": 20},
    "output": {"ratings": 9, "users": 10, "movies": 14},
    "quarantine": {
        # 1+1+3+3+2+5+1+2+1+1+1 = 21
        "total": 21,
        "by_rule": {"M1": 1, "M3": 1, "P2": 3, "P3": 3, "R1": 2, "R2": 5, "R3": 1,
                    "R5": 2, "U1": 1, "X1": 1, "X2": 1},
    },
    # movies 2 = 6 号完全重复 1 条 + 7 号冲突副本 1 条
    "dedupe": {"ratings": 2, "movies": 2, "users": 2},
    "fix": {"P1_text": 2, "R4_ms": 1, "M2_strip": 1, "U3_zip_plus4": 1},
}

# 标记/检查类规则的命中数（不改变记录集合）。
# R8 是 4 而不是 2：冲突按 (UserID,MovieID) 判，(3,1) 两个时间戳各标 1 条，
# 另外 (1,1) 因 R4 把毫秒修成秒也形成两个时间戳，再标 2 条。
MARKS_EXPECTED = {"U2": 3, "U3": 3, "M6": 1, "M7": 2, "M8": 2, "R8": 4, "R9": 0}


def _write(path, text):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with io.open(path, "w", encoding="iso-8859-1", newline="\n") as fh:
        fh.write(text)


def main():
    raw = os.path.join(HERE, "raw")
    exp = os.path.join(HERE, "expected")
    _write(os.path.join(raw, "users.dat"), USERS_RAW)
    _write(os.path.join(raw, "movies.dat"), MOVIES_RAW)
    _write(os.path.join(raw, "ratings.dat"), RATINGS_RAW)
    _write(os.path.join(exp, "cleaned", "users.dat"), USERS_EXPECTED)
    _write(os.path.join(exp, "cleaned", "movies.dat"), MOVIES_EXPECTED)
    _write(os.path.join(exp, "cleaned", "ratings.dat"), RATINGS_EXPECTED)
    with io.open(os.path.join(exp, "counts.json"), "w", encoding="utf-8",
                 newline="\n") as fh:
        fh.write(json.dumps({"counts": COUNTS_EXPECTED, "marks": MARKS_EXPECTED},
                            ensure_ascii=False, indent=2, sort_keys=True))
        fh.write(u"\n")
    print("fixtures written to %s" % HERE)


if __name__ == "__main__":
    main()
