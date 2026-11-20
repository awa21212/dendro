# -*- coding: utf-8 -*-
"""本地 RAG 检索：向量相似度 × 时间新鲜度（综合分），并报告最新时间戳。

综合分 = 0.75 × 相似度 + 0.25 × 衰减权重，衰减权重 = 0.5 ^ (块龄 / 半衰期天数)。

用法：
  python ask.py "why does the docker build ignore the cache on CI"
  python ask.py --corpus demo "ssh says permission too open"    # 指定库
  python ask.py --hours 48 "what changed this week"                     # 只看最近 48 小时（硬窗）
  python ask.py --since 2026-11-01 "..."                       # 只看这天之后
  python ask.py --half-life 7 "..."                            # 半衰期 7 天（默认 30）
  python ask.py --no-decay "..."                               # 关闭时间项，纯相似度
  python ask.py --newest "..."                                 # 只按时间倒序看最新几条
"""
import argparse, json, os, pickle, re, shutil, sys, time, urllib.request
import numpy as np
import corpora

BASE = os.path.dirname(os.path.abspath(__file__))
OLLAMA = "http://127.0.0.1:11434"
EMBED_MODEL = "bge-m3"          # 中文检索比 nomic 准（方法卡命中 3/4 vs 2/4），速度一样
DEFAULT_SUFFIX = "_bge"          # 默认读 bge-m3 建的那套索引；换模型时显式传 --model/--suffix
EMBED_OVERRIDE = ""
CHAT_MODEL = "qwen2.5:7b-instruct"

W_SIM, W_TIME = 0.75, 0.25
CARD_BONUS = 0.03   # 方法卡优先；留出集上 15→17/19，且 0.03 与 0.06 同分（宽平台，不是刀尖）


def clear_proxy():
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(k, None)
    os.environ["NO_PROXY"] = "*"


LOCAL_DIM = 4096


def _local_vec(text, dim=LOCAL_DIM):
    """字符 n-gram 哈希向量（2/3-gram，L2 归一化）。不依赖任何模型，纯本地。"""
    import hashlib
    t = re.sub(r"\s+", "", text.lower())
    v = [0.0] * dim
    for n in (2, 3):
        for i in range(len(t) - n + 1):
            g = t[i:i + n]
            h = int(hashlib.md5(g.encode("utf-8")).hexdigest()[:8], 16)
            sign = 1.0 if (h >> 31) & 1 else -1.0
            v[h % dim] += sign
    norm = sum(x * x for x in v) ** 0.5
    return [x / norm for x in v] if norm else v


def embed_local(texts):
    return [_local_vec(t) for t in texts]


def embed_one(text):
    if (EMBED_OVERRIDE or EMBED_MODEL) == "local":
        import numpy as _np
        v = _np.asarray(_local_vec(text), dtype="float32")
        return v / (_np.linalg.norm(v) + 1e-12)
    body = json.dumps({"model": EMBED_OVERRIDE or EMBED_MODEL, "input": [text]}).encode("utf-8")
    req = urllib.request.Request(OLLAMA + "/api/embed", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        v = np.asarray(json.loads(r.read().decode("utf-8"))["embeddings"][0], dtype="float32")
    return v / (np.linalg.norm(v) + 1e-12)


def chat(prompt):
    body = json.dumps({"model": CHAT_MODEL, "prompt": prompt, "stream": False,
                       "options": {"temperature": 0.2}}).encode("utf-8")
    req = urllib.request.Request(OLLAMA + "/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read().decode("utf-8"))["response"].strip()


def bigrams(t):
    t = re.sub(r"\s+", "", t)
    return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) > 1 else {t}


def load_corpus(name, suffix=None):
    cfg = corpora.get(name)
    suffix = DEFAULT_SUFFIX if suffix is None else suffix   # 显式传空串 = 用不带后缀的旧库
    idx = cfg["index"] + suffix
    vec, meta_p = os.path.join(idx, "vectors.npy"), os.path.join(idx, "meta.jsonl")
    if not (os.path.isfile(vec) and os.path.isfile(meta_p)):
        idx = cfg["index"]                      # 默认后缀的库不存在就退回旧库
        vec, meta_p = os.path.join(idx, "vectors.npy"), os.path.join(idx, "meta.jsonl")
    if not (os.path.isfile(vec) and os.path.isfile(meta_p)):
        raise SystemExit("[%s] 还没有索引：先跑 python build.py --corpus %s" % (name, name))
    mat = np.load(vec)
    meta = [json.loads(l) for l in open(meta_p, encoding="utf-8")]
    return mat, meta


def snapshot_st():
    root = corpora.ST_VECTORS
    dst = os.path.join(BASE, "st_snapshot")
    os.makedirs(dst, exist_ok=True)
    n = 0
    for dirpath, _, filenames in os.walk(root):
        for f in filenames:
            if f != "index.json":
                continue
            chat_name = os.path.basename(os.path.dirname(dirpath))
            safe = re.sub(r"[^\w\u4e00-\u9fa5.-]+", "_", chat_name)[:60]
            sub = os.path.join(dst, safe)
            os.makedirs(sub, exist_ok=True)
            shutil.copy2(os.path.join(dirpath, f), os.path.join(sub, "index.json"))
            n += 1
    print("[snapshot] %d 个会话 -> %s" % (n, dst))
    return dst


def load_st(root=None):
    root = root or corpora.ST_VECTORS
    if not os.path.isdir(root):
        raise SystemExit("找不到 ST 向量库：%s" % root)
    texts, vecs, tags, times = [], [], [], []
    for dirpath, _, filenames in os.walk(root):
        for f in filenames:
            if f != "index.json":
                continue
            fp = os.path.join(dirpath, f)
            try:
                d = json.load(open(fp, encoding="utf-8"))
            except Exception:
                continue
            chat_name = os.path.basename(os.path.dirname(dirpath))
            for it in d.get("items", []):
                md = it.get("metadata", {})
                t = (md.get("text") or "").strip()
                v = it.get("vector")
                if not t or not v:
                    continue
                texts.append(t); vecs.append(v); tags.append(chat_name); times.append(os.path.getmtime(fp))
    if not texts:
        raise SystemExit("ST 向量库里没有可读条目")
    mat = np.asarray(vecs, dtype="float32")
    mat /= np.linalg.norm(mat, axis=1, keepdims=True) + 1e-12
    meta = [{"path": tags[i], "text": texts[i], "ts": times[i]} for i in range(len(texts))]
    return mat, meta


def ts_of(m):
    t = m.get("ts")
    if t:
        return float(t)
    try:
        return os.path.getmtime(m["path"])
    except OSError:
        return 0.0


def fmt_ts(t):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else "?"


def _bigram_sets(corpus, suffix, meta):
    """每块的 bigram 集合，落盘缓存。

    每次查询都对全部块重算 bigram 是 80–190 ms 的纯 Python 开销（715 块实测），
    而 matmul 只要 0.07 ms —— 缓存后只做集合交（0.1 ms）。
    缓存按「块数 + 首尾路径」校验，重建索引后自动失效。
    """
    fp = os.path.join(BASE, "bigram_cache_%s%s.pkl" % (corpus, suffix or ""))
    stamp = [len(meta), meta[0]["path"] if meta else "", meta[-1]["path"] if meta else ""]
    try:
        with open(fp, "rb") as f:
            d = pickle.load(f)
        if d.get("stamp") == stamp and len(d.get("sets") or []) == len(meta):
            return d["sets"]
    except Exception:
        pass
    sets = [bigrams(os.path.basename(m["path"]) + "\n" + m["text"]) for m in meta]
    try:
        with open(fp, "wb") as f:
            pickle.dump({"stamp": stamp, "sets": sets}, f, protocol=4)
    except Exception as e:
        print("[warn] bigram 缓存写不进去：%s" % e, file=sys.stderr)
    return sets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("q")
    ap.add_argument("--corpus", default=None,
                    help="库名，取自 corpus.config.json；省略则用 default_scope 的第一个")
    ap.add_argument("--st", action="store_true", help="同时查外部向量库（可选，只读；corpora.ST_VECTORS 为空时禁用）")
    ap.add_argument("--snapshot", action="store_true", help="先拷静态快照再查")
    ap.add_argument("-k", type=int, default=8)
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--hours", type=float, default=0.0, help="硬窗：只看最近 N 小时")
    ap.add_argument("--since", default="", help="硬窗：只看 YYYY-MM-DD 之后")
    ap.add_argument("--half-life", type=float, default=30.0, help="衰减半衰期（天），默认 30")
    ap.add_argument("--no-decay", action="store_true", help="关闭时间项，只按相似度")
    ap.add_argument("--newest", action="store_true", help="只按时间倒序（找最新的）")
    ap.add_argument("--current-only", action="store_true", help="同一文件只留分数最高的一块")
    ap.add_argument("--min-sim", type=float, default=0.0,
                    help="相似度绝对下限（默认 0=关）。注意：bge-m3 的底噪本来就有 0.5 左右，"
                         "0.10 这种小阈值在这里等于没设，别照搬别人的数")
    ap.add_argument("--min-ratio", type=float, default=0.70,
                    help="相对下限：只留相似度 ≥ 比值×最高分 的结果（默认 0.70，砍掉长尾垃圾"
                         "而不动第 1 名；传 0 关掉）")
    ap.add_argument("--model", default="", help="嵌入模型，必须和建库时一致")
    ap.add_argument("--suffix", default=None,
                    help="索引后缀；默认 _bge，传 \"\" 用旧的无后缀库")
    args = ap.parse_args()
    global EMBED_OVERRIDE
    EMBED_OVERRIDE = args.model
    clear_proxy()

    if args.st:
        root = snapshot_st() if args.snapshot else None
        mat, meta = load_st(root)
    else:
        name = args.corpus or corpora.DEFAULT_SCOPE[0]
        if not args.corpus:
            print("（默认查 %s；可选：%s）" % (name, " / ".join(corpora.names())))
        mat, meta = load_corpus(name, args.suffix)

    q = embed_one(args.q)
    cos = mat @ q
    qb = bigrams(args.q)
    # 文件名也参与字面匹配：问「因子库总览」这类题时，正文里不一定重复标题（问术语表这类题时很管用）
    _bsets = _bigram_sets(args.corpus, args.suffix, meta)
    lex = np.array([len(qb & _bsets[i]) / (len(qb) + 1e-9) for i in range(len(meta))],
                   dtype="float32")
    if lex.max() > 0:
        lex = lex / lex.max()
    sim = np.clip(0.72 * cos + 0.28 * lex, 0, 1)
    # 方法卡是人工整理过的结论，长文是原始叙述；分数接近时优先卡（结构性先验，不是调参）
    is_card = np.array([("/cards/" in m["path"].replace("\\", "/")) for m in meta], dtype="float32")
    sim = np.clip(sim + CARD_BONUS * is_card, 0, 1)

    now = time.time()
    ts = np.array([ts_of(m) for m in meta], dtype="float64")
    age = np.maximum(0.0, (now - ts) / 86400.0)
    decay = 0.5 ** (age / max(args.half_life, 0.001))

    keep = np.ones(len(meta), dtype=bool)
    if args.hours:
        keep &= ts >= now - args.hours * 3600
    if args.since:
        try:
            lo = time.mktime(time.strptime(args.since, "%Y-%m-%d"))
        except ValueError:
            raise SystemExit("--since 要 YYYY-MM-DD")
        keep &= ts >= lo
    if args.min_sim > 0 and not args.newest:
        keep &= sim >= args.min_sim

    score = sim if args.no_decay else W_SIM * sim + W_TIME * decay

    if args.newest:
        order = [i for i in np.argsort(-ts) if keep[i]][: args.k]
    elif not args.current_only:
        order = [i for i in np.argsort(-score) if keep[i]][: args.k]
    else:
        best = {}
        for i, m in enumerate(meta):
            if not keep[i]:
                continue
            key = os.path.basename(m["path"]).lower()
            if key not in best or score[i] > score[best[key]]:
                best[key] = i
        order = sorted(best.values(), key=lambda i: -score[i])[: args.k]

    if not order:
        raise SystemExit("没有命中（相似度低于 --min-sim 或时间过滤后为空；试试 --min-sim 0 放宽）")

    # 相对下限：砍掉「跟第 1 名差太远」的长尾，第 1 名永远保留
    if args.min_ratio > 0 and len(order) > 1:
        _best = max(sim[i] for i in order)
        order = [order[0]] + [i for i in order[1:] if sim[i] >= args.min_ratio * _best]

    tag = "ST" if args.st else args.corpus
    print("\n== [%s] %s ==" % (tag, args.q))
    print("   【当前时间】%s 周%s（数据最新 %s）"
          % (time.strftime("%Y-%m-%d %H:%M", time.localtime(now)),
             "一二三四五六日"[time.localtime(now).tm_wday], fmt_ts(ts.max())))
    print("   命中 %d 块 · 结果里最新：%s"
          % (len(order), fmt_ts(max(ts[i] for i in order))))

    for rank, i in enumerate(order, 1):
        m = meta[i]
        txt = m["text"] if args.full else m["text"][:300].replace("\n", " ")
        if args.newest:
            head = "[%d] %s  %s" % (rank, os.path.basename(m["path"]), fmt_ts(ts[i]))
        else:
            head = "[%d] %s  综合 %.3f（相似 %.3f × 时间 %.2f）  %s" % (
                rank, os.path.basename(m["path"]), score[i], sim[i], decay[i], fmt_ts(ts[i]))
        print("\n" + head)
        print("    " + txt.strip())

    try:
        import hitlog
        _top = os.path.basename(meta[order[0]]["path"])
        _hits = [os.path.basename(meta[i]["path"]) for i in order]
        _rid = hitlog.append(args.q, _top, "st" if args.st else args.corpus,
                             age=float(age[order[0]]), hits=_hits)
        if _rid:
            print("   （已记录 #%s；答对了敲 python hitlog.py ok %s）" % (_rid, _rid))
    except Exception:
        pass
    if args.llm:
        ctx = "\n\n".join("（片段%d｜%s｜%s）\n%s" % (r, os.path.basename(meta[i]["path"]), fmt_ts(ts[i]), meta[i]["text"])
                          for r, i in enumerate(order, 1))
        prompt = ("你是我的本地研究助手。只根据下面片段回答，不要编造。\n"
                  "时间更早的片段如果和新片段冲突，以新的为准，并说明你用了哪一条。\n"
                  "如果片段里没有答案，就说「资料里没有」。中文，简洁，末尾列引用片段编号。\n\n"
                  + ctx + "\n\n问题：" + args.q + "\n答案：")
        print("\n== 模型作答 ==")
        print(chat(prompt))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
