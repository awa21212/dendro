"""Health check: hit rate + score + latency on a case file.

Same scoring as ``ask.py`` (hybrid 0.72 cosine + 0.28 lexical, card bonus,
additive recency, half-life 30 days); it just prints the numbers too.

    python measure.py                          # examples/cases.json
    python measure.py --cases my_cases.json    # your own set

A case file is a JSON list, each item naming the question and the file that
should come back first::

    [{"q": "why does my build fail", "card": "build-errors.md"}, ...]

Ground truth is a file name, so cases survive chunk re-indexing. Twenty cases
is enough to notice a regression; fewer is a smoke test, not a measurement.
"""

import argparse
import json
import os
import statistics
import sys
import time

import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import ask      # noqa: E402
import corpora  # noqa: E402

DEFAULT_CASES = os.path.join(os.path.dirname(BASE), "examples", "cases.json")


def load_all(suffix, scope):
    mat = meta = None
    for c in scope:
        m, mm = ask.load_corpus(c, suffix)
        mat = m if mat is None else np.vstack([mat, m])
        meta = mm if meta is None else meta + mm
    return mat, meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=DEFAULT_CASES, help="case file (JSON list)")
    ap.add_argument("--corpora", default=",".join(corpora.DEFAULT_SCOPE),
                    help="comma separated, default %s" % ",".join(corpora.DEFAULT_SCOPE))
    ap.add_argument("--suffix", default="_bge")
    ap.add_argument("--half-life", type=float, default=30.0)
    ap.add_argument("--card-bonus", type=float, default=0.03)
    ap.add_argument("-k", type=int, default=3)
    a = ap.parse_args()

    if not os.path.isfile(a.cases):
        raise SystemExit("case file not found: %s" % a.cases)
    cases = json.load(open(a.cases, encoding="utf-8"))
    scope = [n.strip() for n in a.corpora.split(",") if n.strip()]

    t0 = time.perf_counter()
    mat, meta = load_all(a.suffix, scope)
    load_ms = (time.perf_counter() - t0) * 1000
    n = len(meta)
    is_card = np.array(["/cards/" in m["path"].replace("\\", "/") for m in meta],
                       dtype="float32")
    ts = np.array([ask.ts_of(m) for m in meta], dtype="float64")
    name_of = [os.path.basename(m["path"]) for m in meta]

    t0 = time.perf_counter()
    bsets = [ask.bigrams(name_of[i] + "\n" + meta[i]["text"]) for i in range(n)]
    prep_ms = (time.perf_counter() - t0) * 1000
    print("corpus: %d blocks (%s) - load %.0f ms - bigram prep %.0f ms - model %s"
          % (n, "/".join(scope), load_ms, prep_ms, ask.EMBED_MODEL))

    hit1 = hit3 = 0
    comp_t1, sim_t1, full_ms, emb_ms, sc_ms = [], [], [], [], []
    for c in cases:
        q = c["q"]
        t1 = time.perf_counter()
        qv = ask.embed_one(q)
        t2 = time.perf_counter()
        cos = mat @ qv
        qb = ask.bigrams(q)
        lex = np.array([len(qb & bsets[i]) / (len(qb) + 1e-9) for i in range(n)],
                       dtype="float32")
        if lex.max() > 0:
            lex = lex / lex.max()
        sim = np.clip(0.72 * cos + 0.28 * lex, 0, 1)
        sim = np.clip(sim + a.card_bonus * is_card, 0, 1)
        now = time.time()
        age = np.maximum(0.0, (now - ts) / 86400.0)
        decay = 0.5 ** (age / max(a.half_life, 0.001))
        score = ask.W_SIM * sim + ask.W_TIME * decay
        order = list(np.argsort(-score)[: a.k])
        t3 = time.perf_counter()

        emb_ms.append((t2 - t1) * 1000)
        sc_ms.append((t3 - t2) * 1000)
        full_ms.append((t3 - t1) * 1000)
        comp_t1.append(float(score[order[0]]))
        sim_t1.append(float(sim[order[0]]))
        names = [name_of[i].lower() for i in order]
        tgt = c["card"].lower()
        if names and names[0] == tgt:
            hit1 += 1
        if tgt in names:
            hit3 += 1

    def med(x):
        return statistics.median(x)

    def p90(x):
        return sorted(x)[int(len(x) * 0.9) - 1] if len(x) > 1 else x[0]

    nc = len(cases)
    print()
    print("| cases | hit@1 | hit@%d | score (median) | score range | similarity | embed ms | score ms | total ms | p90 ms |"
          % a.k)
    print("|---|---|---|---|---|---|---|---|---|---|")
    print("| %d | %d/%d = %.0f%% | %d/%d = %.0f%% | %.3f | %.3f–%.3f | %.3f | %.0f | %.2f | **%.0f** | %.0f |" % (
        nc, hit1, nc, 100 * hit1 / nc, hit3, nc, 100 * hit3 / nc,
        med(comp_t1), min(comp_t1), max(comp_t1), statistics.mean(sim_t1),
        med(emb_ms), med(sc_ms), med(full_ms), p90(full_ms)))


if __name__ == "__main__":
    main()
