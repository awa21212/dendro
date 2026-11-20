# -*- coding: utf-8 -*-
"""开工前的第一步：把与任务相关的 SOP / 坑先摆出来。

用法：
    python preflight.py ncnn 测试
    python preflight.py "docker 里编 pnnx"
    python preflight.py "docker build" --k 3 --corpora notes
    python preflight.py "docker build" --corpus demo

默认搜 corpus.config.json 里 default_scope 列出的全部库；
每条结果前面标出它来自哪个库。

设计原则：只做一件事——把最相关的几块读出来，剩下的判断留给人。
不做摘要、不调模型。
"""
import argparse
import os
import sys
import time as _time

import numpy as np

import ask
import corpora

GUIDE = ("读完再动手。同一件事有多条经验时以日期新的为准；"
         "卡住超过 10 分钟，先回来重搜一次关键词，多半是漏读了某张卡。")


def score(task, mat, meta):
    q = ask.embed_one(task)
    cos = mat @ q
    qb = ask.bigrams(task)
    lex = np.array([len(qb & ask.bigrams(m["text"])) / (len(qb) + 1e-9) for m in meta],
                   dtype="float32")
    if lex.max() > 0:
        lex = lex / lex.max()
    sim = np.clip(0.72 * cos + 0.28 * lex, 0, 1)
    now = _time.time()
    ts = np.array([ask.ts_of(m) for m in meta], dtype="float64")
    age = np.maximum(0.0, (now - ts) / 86400.0)
    decay = 0.5 ** (age / 30.0)
    return sim, ask.W_SIM * sim + ask.W_TIME * decay, ts


def is_card(path):
    p = path.replace("\\", "/")
    return "/cards/" in p


def gather(task, names, suffix):
    """把多个库的候选合并成一个列表。"""
    rows = []
    loaded = []
    for name in names:
        try:
            mat, meta = ask.load_corpus(name, suffix)
        except SystemExit as e:
            print("（%s 库没读到：%s）" % (name, e))
            continue
        sim, final, ts = score(task, mat, meta)
        loaded.append((name, meta, sim, final, ts))
        for i, m in enumerate(meta):
            rows.append({
                "corpus": name,
                "meta": m,
                "sim": float(sim[i]),
                "final": float(final[i]),
                "ts": float(ts[i]),
            })
    return rows, loaded


_NOISE_PREFIX = ("可以", "好", "嗯", "行", "对", "ok", "谢谢", "早", "晚安",
                 "早安", "不是", "？", "?", "这大厂", "草率")


def is_task(text):
    """粗筛：这句话像不像一个任务。聊天/确认/感叹不进检索、也不记账。"""
    t = (text or "").strip()
    if len(t) < 6:
        return False
    core = "".join(ch for ch in t if ch.isalnum())
    if len(core) < 3:
        return False
    low = t.lower()
    for p in _NOISE_PREFIX:
        if low.startswith(p.lower()) and len(t) <= 14:
            return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("task", nargs="+", help="这次要做什么，用几个关键词就行")
    ap.add_argument("-k", "--k", type=int, default=4)
    ap.add_argument("--corpora", default=",".join(corpora.DEFAULT_SCOPE),
                    help="逗号分隔，默认 %s" % ",".join(corpora.DEFAULT_SCOPE))
    ap.add_argument("--with-life", action="store_true", help="把 life 库也拉进来")
    ap.add_argument("--suffix", default=None)
    ap.add_argument("--full", action="store_true", help="打印整块，默认只印前 240 字")
    ap.add_argument("--cards", choices=("auto", "only", "off"), default="auto",
                    help="auto=先只在方法卡里找，找不到再搜全库（默认）；only=只搜卡；off=全库")
    args = ap.parse_intermixed_args()

    task = " ".join(args.task)
    if not is_task(task):
        print("（跳过：这条不像任务，不检索也不记账）")
        return 0
    ask.clear_proxy()

    names = [n.strip() for n in args.corpora.split(",") if n.strip()]
    if args.with_life and "life" not in names:
        names.append("life")

    rows, loaded = gather(task, names, args.suffix)
    if not rows:
        print("一个库都没读到，先跑 build.py --corpus <库名>")
        return

    cards = [r for r in rows if is_card(r["meta"]["path"])]
    scope_note = ""
    pool = rows
    if args.cards != "off" and cards and "life" not in names:
        best_card = max(cards, key=lambda r: r["sim"])
        # 只在这张卡「明显对口」时才收窄到卡里；否则宁可多给几块，
        # 免得把「五腿换手」这种真问题带去猜成某个无关的工程卡
        if args.cards == "only" or best_card["sim"] >= 0.62:
            pool = cards
            scope_note = "（只在 %d 块方法卡里找）" % len(pool)
        elif best_card["sim"] >= 0.45:
            scope_note = "（卡片最高分 %.2f 不算高，已在全库里找）" % best_card["sim"]

    pool = sorted(pool, key=lambda r: -r["final"])[: args.k]

    # 状态类问题（接下来做什么 / 我这台机器）先看 task 库。
    # 只有两种情况让状态库上桌：问句本身像在问现状，或者方法卡一条都没命中。
    STATUS_KW = ("接下来", "下一步", "现在", "当前", "状态", "待办", "进度",
                 "我这台", "环境", "谁在跑", "今天要", "还剩")
    statusish = any(k in task for k in STATUS_KW)
    task_rows = [r for r in rows if r["corpus"] == "task"]
    best_task = max(task_rows, key=lambda r: r["final"]) if task_rows else None
    state_hits = []
    if best_task and best_task["final"] >= 0.55 and (statusish or not cards):
        state_hits = sorted(task_rows, key=lambda r: -r["final"])[:2]

    print("\n== 开工前先读 ==")
    state_first = bool(state_hits) and state_hits[0]["final"] > pool[0]["final"] + 0.03
    if state_first:
        print("任务：%s → 这更像「现在是什么状态」，先看下面这段" % task)
        for r in state_hits:
            print("\n[状态·%s] %s  综合 %.3f（相似 %.3f）  %s"
                  % (r["corpus"], os.path.basename(r["meta"]["path"]), r["final"], r["sim"],
                     ask.fmt_ts(r["ts"])))
            print("    " + r["meta"]["text"][:300].replace("\n", " "))
        print("\n--- 方法卡 ---")
        state_hits = []

    total_blocks = sum(len(meta) for _, meta, _, _, _ in loaded)
    print("任务：%s%s" % (task, scope_note))
    print("命中 %d 块 · 语料 %d 块（%s） · 结果里最新：%s"
          % (len(pool), total_blocks, " + ".join(n for n, _, _, _, _ in loaded),
             ask.fmt_ts(max(r["ts"] for r in pool))))
    for rank, r in enumerate(pool, 1):
        m = r["meta"]
        txt = m["text"] if args.full else m["text"][:240].replace("\n", " ")
        decay = 0.5 ** (max(0.0, (_time.time() - r["ts"]) / 86400.0) / 30.0)
        print("\n[%d·%s] %s  综合 %.3f（相似 %.3f × 时间 %.2f）  %s"
              % (rank, r["corpus"], os.path.basename(m["path"]), r["final"], r["sim"],
                 decay, ask.fmt_ts(r["ts"])))
        print("    " + txt.strip())
    try:
        import hitlog
        _top = os.path.basename(pool[0]["meta"]["path"]) if pool else "-"
        _age = max(0.0, (_time.time() - pool[0]["ts"]) / 86400.0) if pool else None
        _rid = hitlog.append(task, _top, "preflight", age=_age)
        if _rid:
            print("（已记录 #%s；答对了敲 python hitlog.py ok %s）" % (_rid, _rid))
    except Exception:
        pass
    print("\n" + GUIDE)
    if state_hits:
        print("\n== 顺带：状态库 ==")
        for r in state_hits:
            print("[%s] 综合 %.3f（相似 %.3f）  %s"
                  % (os.path.basename(r["meta"]["path"]), r["final"], r["sim"], ask.fmt_ts(r["ts"])))
            print("    " + r["meta"]["text"][:200].replace("\n", " "))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
