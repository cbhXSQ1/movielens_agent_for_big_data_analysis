# -*- coding: utf-8 -*-
import sys, re, html
sys.stdout.reconfigure(encoding='utf-8')
from collections import Counter

BASE = r"E:\学\大数据分析\ml-1m\ml-1m"
OUT = r"E:\学\大数据分析\temp\ml1m_scoring_report.txt"

def load(name):
    with open(BASE + "\\" + name, "rb") as f:
        raw = f.read()
    t = raw.decode("iso-8859-1")
    return [l[:-1] if l.endswith("\r") else l for l in t.split("\n") if l != ""]

def is_int(s):
    try:
        int(s); return True
    except Exception:
        return False

OFFICIAL = {"Action", "Adventure", "Animation", "Children's", "Comedy", "Crime", "Documentary",
            "Drama", "Fantasy", "Film-Noir", "Horror", "Musical", "Mystery", "Romance",
            "Sci-Fi", "Thriller", "War", "Western"}
AGE = {'1', '18', '25', '35', '45', '50', '56'}
OCC = set(str(i) for i in range(21))
LO, HI = 956703932, 1046476799
MS_TH = 100_000_000_000

def year_ok(t):
    m = re.search(r'\((\d{4})\)\s*$', t)
    return bool(m) and 1900 <= int(m.group(1)) <= 2003

def fix_text(s):
    s = s.strip()
    s = html.unescape(s)
    if 'Ã' in s or 'Â' in s or 'â€' in s:
        try:
            s = s.encode('latin-1').decode('utf-8')
        except Exception:
            pass
    return s

rl, ml, ul = load("ratings.dat"), load("movies.dat"), load("users.dat")
R4 = [tuple(l.split("::")) for l in rl if len(l.split("::")) == 4]
M3 = [tuple(l.split("::")) for l in ml if len(l.split("::")) == 3]
U5 = [tuple(l.split("::")) for l in ul if len(l.split("::")) == 5]

def metrics(R, U, M, R_lines, U_lines, M_lines):
    o = {}
    nR, nU, nM = len(R), len(U), len(M)
    Uset = set(r[0] for r in U); Mset = set(r[0] for r in M)
    o['A1'] = sum(1 for r in R if is_int(r[2]) and 1 <= int(r[2]) <= 5) / nR
    o['A2'] = sum(1 for r in R if is_int(r[3])) / nR
    o['A3'] = sum((1 if r[1] in ('M', 'F') else 0) + (1 if r[2] in AGE else 0) + (1 if r[3] in OCC else 0) for r in U) / (3 * nU)
    o['A4'] = sum(1 for r in R if r[0] in Uset and r[1] in Mset) / nR
    o['A5'] = sum(1 for r in M if year_ok(r[1])) / nM
    toks = [g for r in M for g in r[2].split('|')]
    o['A6'] = sum(1 for g in toks if g in OFFICIAL) / len(toks) if toks else 1.0
    fields = 4 * nR + 5 * nU + 3 * nM
    empt = (sum(1 for r in R for x in r if x == '') + sum(1 for r in U for x in r if x == '')
            + sum(1 for r in M for x in r if x == ''))
    o['C1'] = 1 - empt / fields
    nonempty = (sum(1 for r in R if all(x != '' for x in r)) + sum(1 for r in U if all(x != '' for x in r))
                + sum(1 for r in M if all(x != '' for x in r)))
    o['C2'] = nonempty / (R_lines + U_lines + M_lines)
    o['C3'] = (nR + nU + nM) / (R_lines + U_lines + M_lines)
    keys = set((r[0], r[1], r[3]) for r in R)
    o['U1'] = len(keys) / nR if nR else 1.0
    extra = ((nR - len(set(tuple(r) for r in R))) + (nU - len(set(tuple(r) for r in U)))
             + (nM - len(set(tuple(r) for r in M))))
    o['U2'] = 1 - extra / (R_lines + U_lines + M_lines)
    id_extra = (nU - len(set(r[0] for r in U))) + (nM - len(set(r[0] for r in M)))
    o['U3'] = 1 - id_extra / (nU + nM) if (nU + nM) else 1.0
    o['F1'] = sum(1 for r in R if is_int(r[3]) and LO <= int(r[3]) <= HI) / nR
    vts = [int(r[3]) for r in R if is_int(r[3]) and LO <= int(r[3]) <= HI]
    if vts:
        gap_days = (HI - max(vts)) / 86400.0
        o['F2'] = 1.0 if gap_days <= 30 else max(0.0, 1 - (gap_days - 30) / 150.0)
    else:
        o['F2'] = 0.0
    o['S1'] = 1 - ((R_lines - nR) + (U_lines - nU) + (M_lines - nM)) / (R_lines + U_lines + M_lines)
    bad_txt = sum(1 for r in M if ('Ã' in r[1] or 'Â' in r[1] or 'â€' in r[1] or '&#' in r[1]))
    o['S2'] = 1 - bad_txt / nM
    ts_int = [r for r in R if is_int(r[3])]
    ms = sum(1 for r in ts_int if int(r[3]) >= MS_TH)
    o['S3'] = 1 - ms / len(ts_int) if ts_int else 1.0
    def dup_conf(recs, keyf, valf):
        cnt = Counter(keyf(r) for r in recs)
        vals = {}
        for r in recs:
            vals.setdefault(keyf(r), set()).add(valf(r))
        d = sum(1 for k, c in cnt.items() if c > 1)
        c2 = sum(1 for k in cnt if cnt[k] > 1 and len(vals[k]) > 1)
        return d, c2
    d1, c1 = dup_conf(R, lambda r: (r[0], r[1], r[3]), lambda r: r[2])
    d2, c2 = dup_conf(M, lambda r: r[0], lambda r: (r[1], r[2]))
    d3, c3 = dup_conf(U, lambda r: r[0], lambda r: (r[1], r[2], r[3], r[4]))
    dg, cg = d1 + d2 + d3, c1 + c2 + c3
    o['S4'] = 1 - cg / dg if dg else 1.0
    return o

W = {
    'A1': .25, 'A2': .20, 'A3': .15, 'A4': .25, 'A5': .10, 'A6': .05,
    'C1': .40, 'C2': .30, 'C3': .30,
    'U1': .50, 'U2': .25, 'U3': .25,
    'F1': .70, 'F2': .30,
    'S1': .30, 'S2': .20, 'S3': .20, 'S4': .30,
}
DIMS = {'Accurate': ['A1', 'A2', 'A3', 'A4', 'A5', 'A6'], 'Complete': ['C1', 'C2', 'C3'],
        'Unique': ['U1', 'U2', 'U3'], 'Up-to-date': ['F1', 'F2'], 'Consistent': ['S1', 'S2', 'S3', 'S4']}
DIM_W = {'Accurate': .25, 'Complete': .25, 'Unique': .20, 'Up-to-date': .10, 'Consistent': .20}

before = metrics(R4, U5, M3, len(rl), len(ul), len(ml))

# ---------------- CLEANING ----------------
q = Counter()
clean_users = {}
u_by_id = {}
for r in U5:
    uid = r[0]
    if not (is_int(uid) and 1 <= int(uid) <= 6040):
        q['U1_fake_user'] += 1; continue
    g, a, o, z = r[1], r[2], r[3], r[4]
    if g not in ('M', 'F'): g = ''
    if a not in AGE: a = ''
    if o not in OCC: o = ''
    if re.fullmatch(r'\d{5}-\d{4}', z): z = z[:5]
    elif not re.fullmatch(r'\d{5}', z): z = ''
    u_by_id.setdefault(uid, []).append((g, a, o, z))
def u_score(rec):
    return sum((1 if rec[0] else 0) + (1 if rec[1] else 0) + (1 if rec[2] else 0) + (1 if re.fullmatch(r'\d{5}', rec[3]) else 0) for _ in [0])
for uid, recs in u_by_id.items():
    uniq = list(set(recs))
    if len(uniq) == 1:
        clean_users[uid] = uniq[0]
    else:
        uniq.sort(key=lambda x: -u_score(x))
        if u_score(uniq[0]) > u_score(uniq[1]):
            clean_users[uid] = uniq[0]
        else:
            merged = []
            for i in range(4):
                vals = set(x[i] for x in uniq)
                merged.append(vals.pop() if len(vals) == 1 else '')
            clean_users[uid] = tuple(merged)

clean_movies = {}
m_by_id = {}
for r in M3:
    mid = r[0]
    if not (is_int(mid) and 1 <= int(mid) <= 3952):
        q['M1_fake_movie'] += 1; continue
    m_by_id.setdefault(mid, []).append((fix_text(r[1]), r[2]))
def m_score(rec):
    t, g = rec
    s = 0
    if t != '': s += 2
    if t and '[20XX]' not in t: s += 1
    if t and 'Ã' not in t: s += 1
    if all(x in OFFICIAL for x in g.split('|')): s += 1
    return s
for mid, recs in m_by_id.items():
    uniq = list(set(recs))
    if len(uniq) == 1:
        clean_movies[mid] = uniq[0]
    else:
        uniq.sort(key=lambda x: -m_score(x))
        if m_score(uniq[0]) > m_score(uniq[1]):
            clean_movies[mid] = uniq[0]
            q['M4_conflict_removed'] += len(uniq) - 1
        else:
            clean_movies[mid] = uniq[0]
            q['M4_conflict_removed'] += len(uniq) - 1

clean_ratings = {}
for r in R4:
    uid, mid, rat, ts = r
    if not (is_int(uid) and int(uid) > 0 and is_int(mid) and int(mid) > 0):
        q['R1_bad_id'] += 1; continue
    if not (is_int(rat) and 1 <= int(rat) <= 5):
        q['R2_bad_rating'] += 1; continue
    if not is_int(ts):
        q['R3_bad_ts'] += 1; continue
    t = int(ts)
    if t >= MS_TH:
        t //= 1000
        q['R4_ms_fixed'] += 1
    if not (LO <= t <= HI):
        q['R5_ts_range'] += 1; continue
    if uid not in clean_users:
        q['X1_orphan_user'] += 1; continue
    if mid not in clean_movies:
        q['X2_orphan_movie'] += 1; continue
    key = (uid, mid, t)
    if key in clean_ratings:
        q['R6_dedup'] += 1; continue
    clean_ratings[key] = int(rat)

R_after = [(u, m, str(v), str(t)) for (u, m, t), v in clean_ratings.items()]
M_after = [(mid, t, g) for mid, (t, g) in clean_movies.items()]
U_after = [(uid, g, a, o, z) for uid, (g, a, o, z) in clean_users.items()]

after = metrics(R_after, U_after, M_after, len(R_after), len(U_after), len(M_after))

lines = []
def p(s=""):
    lines.append(s)
    print(s)

p("=== DATA SIZES ===")
p(f"before: ratings={len(R4)} (lines {len(rl)}), users={len(U5)} (lines {len(ul)}), movies={len(M3)} (lines {len(ml)})")
p(f"after : ratings={len(R_after)}, users={len(U_after)}, movies={len(M_after)}")
p(f"quarantine/fix: {dict(q)}")
p("")
p("=== METRICS (same formulas both sides) ===")
p(f"{'metric':<4} {'before':>8} {'after':>8} {'delta':>8}")
for dim, ms in DIMS.items():
    for k in ms:
        p(f"{k:<4} {before[k]*100:7.2f}% {after[k]*100:7.2f}% {(after[k]-before[k])*100:+7.2f}")
p("")
p("=== DIMENSION SCORES ===")
comp_b = comp_a = 0.0
for dim, ms in DIMS.items():
    sb = sum(before[k] * W[k] for k in ms) * 100
    sa = sum(after[k] * W[k] for k in ms) * 100
    comp_b += sb * DIM_W[dim]; comp_a += sa * DIM_W[dim]
    p(f"{dim:<11} {sb:7.2f} -> {sa:7.2f}  ({sa-sb:+.2f})")
p(f"{'COMPOSITE':<11} {comp_b:7.2f} -> {comp_a:7.2f}  ({comp_a-comp_b:+.2f})")

with open(OUT, 'w', encoding='utf-8') as f:
    f.write("\n".join(lines))
print("saved ->", OUT)
