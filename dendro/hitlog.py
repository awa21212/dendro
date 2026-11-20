# -*- coding: utf-8 -*-
"""把「你实际怎么用的」记下来，用它来量「顺手」——不是量算法。

每次 ask / preflight 检索会自动追加一行：
    {"ts": ..., "id": 12, "q": "五腿换手", "top": "brain_复盘_2026-11-12.md", "src": "preflight"}

事后你告诉它答案对不对：
    python hitlog.py ok 12                     # 它答对了，直接用了
    python hitlog.py bad 12                    # 答错了 / 没用上
    python hitlog.py bad 12 --chosen 卡22.md    # 答错了，真正该用的是这个

看统计：
    python hitlog.py --report                  # 最近 30 天：命中率、每周纠正次数、未命中清单
    python hitlog.py --report --days 90
"""
import argparse
import json
import os
import sys
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "log", "queries.jsonl")


def _load():
    if not os.path.exists(LOG):
        return []
    out = []
    with open(LOG, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def _write_all(rows):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    tmp = LOG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, LOG)


def append(q, top, src="ask", age=None, hits=None):
    """检索时调用；任何异常都不许影响检索本身。

    hits：这次返回的 top-k 文件名（按名次），事后能算出「被采纳的那条排第几」。
    """
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        rows = _load()
        rid = (max((r.get("id", 0) for r in rows), default=0) + 1) if rows else 1
        rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "id": rid,
               "q": q, "top": top, "src": src, "verdict": None, "chosen": None,
               "rank": None, "hits": (list(hits)[:20] if hits else None),
               "age": (round(float(age), 2) if age is not None else None)}
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rid
    except Exception:
        return None


def mark(rid, verdict, chosen=None):
    rows = _load()
    hit = False
    for r in rows:
        if r.get("id") == rid:
            r["verdict"] = verdict
            if chosen:
                r["chosen"] = chosen
                hs = r.get("hits") or []
                base = os.path.basename(chosen).lower()
                idx = next((i for i, h in enumerate(hs) if os.path.basename(h).lower() == base), None)
                # 0 = 压根没进这次返回；None = 当时没记名次（老记录）
                r["rank"] = (idx + 1) if idx is not None else (0 if hs else None)
            elif verdict == "ok":
                r["rank"] = 1
            r["marked"] = time.strftime("%Y-%m-%d %H:%M:%S")
            hit = True
            break
    if not hit:
        raise SystemExit("没有第 %s 条记录（先翻下面 --report 里的编号）" % rid)
    _write_all(rows)
    print("已标记 #%s → %s%s" % (rid, verdict, ("（改用 %s）" % chosen) if chosen else ""))


def report(days):
    rows = _load()
    if not rows:
        print("还没有记录。先跑几次 python ask.py / preflight.py。")
        return
    cut = time.time() - days * 86400
    def ts(r):
        try:
            return time.mktime(time.strptime(r["ts"], "%Y-%m-%d %H:%M:%S"))
        except Exception:
            return 0
    rows = [r for r in rows if ts(r) >= cut]
    ok = [r for r in rows if r.get("verdict") == "ok"]
    bad = [r for r in rows if r.get("verdict") == "bad"]
    unk = [r for r in rows if not r.get("verdict")]
    judged = len(ok) + len(bad)

    print("== 最近 %d 天 ==" % days)
    print("查询 %d 次 · 已判定 %d · 未判定 %d" % (len(rows), judged, len(unk)))
    if judged:
        print("首答命中率 %.0f%%（%d/%d）" % (100.0 * len(ok) / judged, len(ok), judged))
        weeks = max(days / 7.0, 1e-9)
        print("每周需要纠正 %.1f 次" % (len(bad) / weeks))
    else:
        print("还没有判定过 —— 用 `python hitlog.py ok <编号>` / `bad <编号>` 标一下才有数")

    # —— 名次分布 + 被采纳条目年龄：只为回答「半衰期该往哪边调」
    adopted = []
    for r in rows:
        if r.get("verdict") == "ok":
            adopted.append((1, r))
        elif r.get("verdict") == "bad" and isinstance(r.get("rank"), int):
            adopted.append((r["rank"], r))
    if adopted:
        tot = len(adopted)

        def _pct(c):
            return int(round(100.0 * c / tot))

        n1 = sum(1 for k, _ in adopted if k == 1)
        n23 = sum(1 for k, _ in adopted if 2 <= k <= 3)
        n4 = sum(1 for k, _ in adopted if k > 3)
        nout = sum(1 for k, _ in adopted if k == 0)
        print("\n被采纳的名次：第1名 %d%% · 第2-3名 %d%% · 第4名后 %d%% · 没进返回 %d%%（n=%d）"
              % (_pct(n1), _pct(n23), _pct(n4), _pct(nout), tot))
        ages = sorted(float(r["age"]) for _, r in adopted if isinstance(r.get("age"), (int, float)))
        if ages:
            fresh = _pct(sum(1 for x in ages if x <= 30))
            old = _pct(sum(1 for x in ages if x > 180))
            print("被采纳条目年龄：中位 %.0f 天 · 30 天内 %d%% · 超过 180 天 %d%%"
                  % (ages[len(ages) // 2], fresh, old))
            if tot < 20:
                advice = "样本只有 %d 次（<20），先积累再谈调参。" % tot
            elif old >= 30:
                advice = "老资料占比高 → 衰减压得太狠，--half-life 往大调（30 → 60）。"
            elif fresh >= 85:
                advice = "几乎全是新资料 → 衰减可以更狠（--half-life 14～21）。"
            else:
                advice = "分布均衡，当前 30 天合适，不用动。"
            print("建议：%s" % advice)
            print("注意：这是相关性线索，不是结论；改完跑一次 regress.py 再决定。")

    if bad:
        print("\n没命中的（按出现次数）:")
        for (q, top), n in Counter((r["q"], r.get("top")) for r in bad).most_common(10):
            chosen = next((r.get("chosen") for r in bad if r["q"] == q and r.get("chosen")), None)
            tag = (" → 应改用 %s" % chosen) if chosen else ""
            print("  x%d  %s   （当时给了 %s）%s" % (n, q, top, tag))
        print("\n建议：上面每一条，挑一张最接近的卡，把这句话原样加进它的「别人可能这么问」段，然后")
        print("      python build.py --corpus code   # 或 finance / task")
    if unk:
        print("\n未判定 %d 条（超过一周未标记的不算进命中率）:" % len(unk))
        for r in unk[-8:]:
            print("  #%s  %s  → %s" % (r["id"], r["q"], r.get("top")))
        print("  标记：python hitlog.py ok <编号> / bad <编号> [--chosen 文件名]")


def todo(n=10):
    """列出最近 n 条未判定，直接给可复制的命令。"""
    rows = _load()
    pend = [r for r in rows if not r.get("verdict")]
    if not pend:
        print("没有未判定的记录 —— 说明要么没查询，要么都标完了。")
        return
    print("未判定 %d 条，最近 %d 条：" % (len(pend), min(n, len(pend))))
    for r in pend[-n:]:
        age = r.get("age")
        age_s = ("  龄 %.1f 天" % age) if isinstance(age, (int, float)) else ""
        print("\n  #%s  %s" % (r.get("id"), r.get("q")))
        print("     给了 %s%s" % (r.get("top"), age_s))
        print("     对 → python hitlog.py ok %s    错 → python hitlog.py bad %s --chosen <该用的文件>"
              % (r.get("id"), r.get("id")))
    print("\n标完跑 python hitlog.py --report 看命中率。")


def _sigmoid(x):
    if x < -30:
        return 0.0
    if x > 30:
        return 1.0
    return 1.0 / (1.0 + pow(2.718281828459045, -x))


def fit(min_judged=30):
    """用「标了 ok/bad 且记了语料年龄」的记录，估计年龄对正确率的边际影响。

    logit P(ok) = a + b * age_days
    b < 0 且显著 → 越老的语料越容易答错，衰减有据；
    语料半衰期 ≈ ln2 / |b|（对数几率减半的年龄）。
    """
    rows = [r for r in _load() if r.get("verdict") in ("ok", "bad")
            and isinstance(r.get("age"), (int, float))]
    n = len(rows)
    print("可用于拟合的判定：%d 条（要求 verdict 已标 + 记录了 age）" % n)
    if n < min_judged:
        print("还不够（至少要 %d 条）。现在半衰期 30 天是拍的，等数据来了再拟合。" % min_judged)
        print("怎么攒：每次 preflight 后跑 `python hitlog.py --todo`，按提示标 ok/bad。")
        return
    xs = [float(r["age"]) for r in rows]
    ys = [1.0 if r["verdict"] == "ok" else 0.0 for r in rows]
    a, b = 0.0, 0.0
    for _ in range(50):
        ga = gb = haa = hab = hbb = 0.0
        for x, y in zip(xs, ys):
            p = _sigmoid(a + b * x)
            w = p * (1 - p)
            ga += (y - p)
            gb += (y - p) * x
            haa += w
            hab += w * x
            hbb += w * x * x
        det = haa * hbb - hab * hab
        if abs(det) < 1e-12:
            break
        da = (hbb * ga - hab * gb) / det
        db = (haa * gb - hab * ga) / det
        a += da
        b += db
        if abs(da) + abs(db) < 1e-9:
            break
    se_b = pow(1.0 / max(hbb, 1e-9), 0.5)
    z = b / se_b if se_b else 0.0
    print("logit P(ok) = %.3f %+.5f * age_days" % (a, b))
    print("b = %+.5f 天^-1, se = %.5f, z = %.2f" % (b, se_b, z))
    if b < 0 and abs(z) >= 1.96:
        print("结论：越老越容易答错（95%% 显著），语料半衰期 ≈ %.0f 天" % (0.693147 / abs(b)))
        print("     当前配置半衰期 30 天 —— 可以考虑改成这个数。")
    elif b < 0:
        print("结论：方向是「越老越容易错」，但不显著（|z|<1.96）。证据不足，先别改半衰期。")
    else:
        print("结论：没有观察到「越老越错」。若 z 也接近 0，说明这份语料几乎不老化，")
        print("     可以把 --half-life 调大（衰减变成近似无效），别为了「有个衰减」而衰减。")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", nargs="?", choices=("ok", "bad"))
    ap.add_argument("rid", nargs="?", type=int)
    ap.add_argument("--chosen", default=None, help="答错了的话，真正该用的文件名")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--todo", nargs="?", type=int, const=10, default=None,
                    help="列出最近 N 条未判定，给可复制的标记命令")
    ap.add_argument("--fit", action="store_true",
                    help="用已判定记录估计「年龄 → 正确率」，反推语料半衰期")
    ap.add_argument("--mark-last", choices=("ok", "bad"), default=None,
                    help="标最近一条未判定记录，不用记编号")
    a = ap.parse_args()
    if a.todo is not None:
        todo(a.todo)
        return
    if a.fit:
        fit()
        return
    if a.mark_last:
        rows = _load()
        pend = [r for r in rows if not r.get("verdict")]
        if not pend:
            print("没有未判定的记录")
            return
        mark(pend[-1]["id"], a.mark_last, a.chosen)
        return
    if a.report or not a.action:
        report(a.days)
        return
    if not a.rid:
        raise SystemExit("用法：python hitlog.py ok|bad <编号> [--chosen 文件名]")
    mark(a.rid, a.action, a.chosen)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
