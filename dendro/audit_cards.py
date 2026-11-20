#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audit_cards.py — 「修法必须落卡」的自查工具。

只做一件事：把最近 N 天改过的代码文件，和最近 N 天新增/修改过的卡片摆在一起，
列出「代码动了、卡没动」的候选，提醒你回看是不是漏了记录。

用法：
    python audit_cards.py                # 默认看最近 7 天
    python audit_cards.py --days 2       # 只看这两天
    python audit_cards.py --strict       # 有遗漏则退出码 1（可挂进每日任务）

退出码：默认 0（只提醒）；--strict 且有遗漏时 1。
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root
CARD_DIRS = ["rag_code/cards", "rag_finance/cards", "rag_task/cards"]
SKIP_PARTS = ("\\rag\\index", "\\rag\\log", "\\__pycache__", "\\cache", "\\.git", "\\bench\\")
CODE_EXT = (".py", ".vbs", ".ps1")


def walk_code(root, since):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        if any(p.strip("\\") in dirpath for p in SKIP_PARTS):
            continue
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "cache", "log", "index_code_bge", "index_finance_bge", "index_task_bge", "index_life_bge", ".git")]
        for fn in filenames:
            if not fn.endswith(CODE_EXT):
                continue
            p = os.path.join(dirpath, fn)
            try:
                m = os.path.getmtime(p)
            except OSError:
                continue
            if m >= since:
                out.append((m, p))
    return out


def walk_cards(root, since):
    out = []
    for rel in CARD_DIRS:
        d = os.path.join(root, rel)
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if not fn.endswith(".md"):
                continue
            p = os.path.join(d, fn)
            m = os.path.getmtime(p)
            if m >= since:
                out.append((m, p))
    return out


def fmt(ts):
    return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=7.0)
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()

    since = (datetime.now() - timedelta(days=a.days)).timestamp()
    code = sorted(walk_code(ROOT, since))
    cards = sorted(walk_cards(ROOT, since))

    print(f"== 落卡自查：最近 {a.days:g} 天 ==")
    print(f"根目录：{ROOT}")
    print(f"代码文件改动 {len(code)} 个 · 卡片改动 {len(cards)} 张\n")

    if cards:
        print("已记录（卡片有改动）：")
        for m, p in cards:
            print(f"  {fmt(m)}  {os.path.relpath(p, ROOT)}")
        print()

    gaps = [c for c in code if not cards or c[0] > max(x[0] for x in cards)]
    if not code:
        print("这段时间没有代码改动。")
        return 0
    if not gaps:
        print("代码改动都落在卡片改动的时间范围内，看起来没有漏记。")
        return 0

    print("可能有修法没落卡（代码改了、但之后没有卡片更新）：")
    for m, p in gaps:
        print(f"  {fmt(m)}  {os.path.relpath(p, ROOT)}")
    print("\n处理：能写成「症状 → 原因 → 修法 → 怎么验证」的就补一张卡；纯数值/一次性的就不用写。")
    return 1 if a.strict else 0


if __name__ == "__main__":
    sys.exit(main())