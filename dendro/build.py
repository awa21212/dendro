# -*- coding: utf-8 -*-
"""本地 RAG 索引器：切块 -> ollama 向量 -> npy。

用法：
  python build.py                 # 增量重建 main 库
  python build.py --corpus demo            # 增量重建 demo 库
  python build.py --corpus demo --rebuild  # 全量重建
"""
import argparse, hashlib, json, os, re, sys, time, urllib.request
import corpora

BASE = os.path.dirname(os.path.abspath(__file__))
OLLAMA = "http://127.0.0.1:11434"
MODEL = "bge-m3"                 # 默认；nomic 用 --model nomic-embed-text-v1.5
DEFAULT_SUFFIX = "_bge"
MODEL_OVERRIDE = ""

SKIP_DIR = ("archive", "node_modules", ".git", "preview", "index", "release", "rag")
SKIP_NAME = ("快照", "定稿", "备份", "bak")
CHUNK, OVERLAP = 700, 120


def log(*a):
    print(*a, flush=True)


def clear_proxy():
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(k, None)
    os.environ["NO_PROXY"] = "*"


def collect_files(sources):
    out = []
    for root, names in sources:
        if not os.path.isdir(root):
            continue
        if not names:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIR and not any(k in d for k in SKIP_NAME)]
                for f in filenames:
                    if f.lower().endswith(".md") and not f.startswith("_"):
                        out.append(os.path.join(dirpath, f))
            continue
        exact = [n for n in names if n.lower().endswith(".md")]
        kws = [n for n in names if not n.lower().endswith(".md")]
        for n in exact:
            p = os.path.join(root, n)
            if os.path.isfile(p):
                out.append(p)
        if kws:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIR]
                for f in filenames:
                    if f.lower().endswith(".md") and any(k in f for k in kws):
                        out.append(os.path.join(dirpath, f))
    # 同一份内容被两个来源各收一次（例如"原始目录 + 桌面副本"）时提前告警，
    # 否则它会以两份独立文件进索引，检索时互相抢位（同一个文件两种扩展名踩过）。
    seen_hash, uniq = {}, []
    for p in sorted(set(out)):
        try:
            h = hashlib.sha1(open(p, "rb").read()).hexdigest()
        except OSError:
            uniq.append(p)
            continue
        if h in seen_hash:
            log(f"[warn] 内容重复，跳过：{p}")
            log(f"       与 {seen_hash[h]} 是同一份文件（复制品不该进索引）")
            continue
        seen_hash[h] = p
        uniq.append(p)
    return uniq


def chunk_file(path):
    raw = open(path, "r", encoding="utf-8", errors="ignore").read()
    if not raw.strip():
        return []
    # 方法卡是「一症状一结论」的原子单元，切开反而把症状和治疗分到两块
    whole = ("/cards/" in path.replace("\\", "/"))
    limit = 4000 if whole else CHUNK
    lines = raw.splitlines()
    blocks, cur, heading = [], [], ""
    for ln in lines:
        if ln.startswith("#"):
            if cur:
                blocks.append((heading, "\n".join(cur).strip()))
                cur = []
            heading = ln.lstrip("# ").strip()[:60]
            continue
        if ln.strip() == "":
            if cur:
                blocks.append((heading, "\n".join(cur).strip()))
                cur = []
            continue
        cur.append(ln)
    if cur:
        blocks.append((heading, "\n".join(cur).strip()))

    chunks, buf, cur_hd = [], "", None
    for hd, text in blocks:
        if not text:
            continue
        piece = (f"【{hd}】\n" if hd and hd != cur_hd else "") + text
        if len(buf) + len(piece) <= limit:
            buf = (buf + "\n\n" + piece).strip()
            continue
        if buf:
            chunks.append(buf)
        cur_hd = hd
        while len(piece) > limit:
            chunks.append(piece[:limit])
            piece = piece[limit - min(OVERLAP, limit // 5):]
        buf = piece
    if buf:
        chunks.append(buf)
    return chunks


DATE_RE = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")
MD_RE = re.compile(r"(?:^|[^\d])(\d{1,2})-(\d{1,2})(?:\s+|·)")


def chunk_ts(text, path):
    """块的时间：文件名里的日期 > 块内离文件 mtime 最近的日期 > mtime。

    以前取「块里第一个 20xx-xx-xx」，结果回测报告正文里的 2019-01-02（回测起点）把整块标成 2019 年，
    衰减算出来是 0.00。改成：把所有候选日期收集起来，挑离文件 mtime 最近的那个——文档写作时间
    通常贴近 mtime，而正文里的历史日期都离得远。
    """
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = time.time()

    cands = []

    for m in DATE_RE.finditer(os.path.basename(path)):
        cands.append(m.group(0))

    for m in DATE_RE.finditer(text[:400]):
        cands.append(m.group(0))

    if cands:
        best, best_d = None, None
        for s in cands:
            try:
                t = time.mktime(time.strptime(s, "%Y-%m-%d"))
            except ValueError:
                continue
            if t > mtime + 2 * 86400:      # 还没发生的日期不可信
                continue
            d = abs(mtime - t)
            if best_d is None or d < best_d:
                best, best_d = t, d
        if best is not None:
            return best

    m = MD_RE.search(text[:300])
    if m:
        now = time.localtime()
        mo, dy = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12 and 1 <= dy <= 31:
            for year in (now.tm_year, now.tm_year - 1):
                try:
                    cand = time.mktime(time.strptime("%d-%02d-%02d" % (year, mo, dy), "%Y-%m-%d"))
                except ValueError:
                    continue
                if cand <= time.time() + 30 * 86400:
                    return cand

    return mtime


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


def embed(texts):
    if (MODEL_OVERRIDE or MODEL) == "local":
        return embed_local(texts)
    body = json.dumps({"model": MODEL_OVERRIDE or MODEL, "input": texts}).encode("utf-8")
    req = urllib.request.Request(OLLAMA + "/api/embed", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read().decode("utf-8"))["embeddings"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="main")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--dedupe", type=float, default=0.0, help="近重复块丢弃阈值（默认关闭：实测删块会掉分）")
    ap.add_argument("--dedupe-global", action="store_true", help="跨文件去重（仅用于对照）")
    ap.add_argument("--model", default="", help="换嵌入模型，如 nomic-embed-text-v1.5 / local")
    ap.add_argument("--suffix", default=None,
                    help="索引目录后缀，默认 _bge；显式 --suffix \"\" 用不带后缀的库")
    args = ap.parse_args()
    clear_proxy()

    cfg = corpora.get(args.corpus)
    global MODEL_OVERRIDE
    MODEL_OVERRIDE = args.model or MODEL
    idx = cfg["index"] + (DEFAULT_SUFFIX if args.suffix is None else args.suffix)
    vec = os.path.join(idx, "vectors.npy")
    meta_p = os.path.join(idx, "meta.jsonl")
    state_p = os.path.join(idx, "state.json")
    os.makedirs(idx, exist_ok=True)

    old = {}
    if os.path.isfile(state_p) and not args.rebuild:
        old = json.load(open(state_p, encoding="utf-8"))

    rows, texts, newstate = [], [], {}
    files = collect_files(cfg["sources"])
    log(f"[{args.corpus}] {len(files)} 个 markdown 文件")
    for p in files:
        try:
            raw = open(p, "rb").read()
        except OSError:
            continue
        h = hashlib.sha1(raw).hexdigest()
        newstate[p] = h
        if old.get(p) == h:
            continue
        chs = chunk_file(p)
        if not chs:
            continue
        for ch in chs:
            rows.append({"path": p, "text": ch, "ts": chunk_ts(ch, p)})
            texts.append(ch)
        try:
            shown = os.path.relpath(p, BASE)
        except ValueError:
            shown = p
        log(f"  + {shown}  ({len(chs)} 块)")

    seen, urows, utexts = set(), [], []
    for r0, t0 in zip(rows, texts):
        k0 = hashlib.sha1(re.sub(r"\s+", "", t0).encode("utf-8")).hexdigest()
        if k0 in seen:
            continue
        seen.add(k0); urows.append(r0); utexts.append(t0)
    rows, texts = urows, utexts

    # 没有新内容时也要处理删除：state.json 里有、本次扫描里没有的文件，是已经
    # 被删/被移走的，必须从索引里剔除。否则增量建库会把旧向量永久留在库里，
    # 表现为「删了卡片，检索还能命中它」，只有 --rebuild 才能清掉。
    if not texts:
        stale = {p for p in old if p not in newstate}
        if stale and os.path.isfile(vec) and os.path.isfile(meta_p):
            import numpy as _np
            prev = _np.load(vec)
            prev_meta = [json.loads(l) for l in open(meta_p, encoding="utf-8")]
            keep = [i for i, m in enumerate(prev_meta) if m["path"] not in stale]
            if len(keep) != len(prev_meta):
                _np.save(vec, prev[keep])
                with open(meta_p, "w", encoding="utf-8") as f:
                    for i in keep:
                        f.write(json.dumps(prev_meta[i], ensure_ascii=False) + "\n")
                log(f"[prune] 剔除 {len(prev_meta) - len(keep)} 块（{len(stale)} 个文件已不存在）")
                log(f"[ok] {len(keep)} 块 -> {idx}")
        else:
            log("[done] 没有新增内容")
        json.dump(newstate, open(state_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return

    log(f"[embed] {len(texts)} 块 -> {MODEL_OVERRIDE or MODEL}")
    vecs, t0 = [], time.time()
    for i in range(0, len(texts), args.batch):
        vecs.extend(embed(texts[i:i + args.batch]))
        log(f"  {min(i+args.batch, len(texts))}/{len(texts)}  {time.time()-t0:.1f}s")

    import numpy as np
    arr = np.asarray(vecs, dtype="float32")
    arr /= np.linalg.norm(arr, axis=1, keepdims=True) + 1e-12

    if old and os.path.isfile(vec):
        prev = np.load(vec)
        prev_meta = [json.loads(l) for l in open(meta_p, encoding="utf-8")]
        keep = [i for i, m in enumerate(prev_meta) if old.get(m["path"]) == newstate.get(m["path"])]
        if keep:
            arr = np.vstack([prev[keep], arr])
            rows = [prev_meta[i] for i in keep] + rows

    dt = float(getattr(args, "dedupe", 0.0) or 0)
    if dt > 0 and arr.shape[0] > 1:
        order = sorted(range(len(rows)), key=lambda i: float(rows[i].get("ts") or 0), reverse=True)
        kept, dropped, bypath = [], 0, {}
        for i in order:
            v = arr[i]
            key = "__all__" if getattr(args, "dedupe_global", False) else rows[i]["path"]
            seen_v = bypath.get(key)
            if seen_v is not None and float((seen_v @ v).max()) >= dt:
                dropped += 1
                continue
            kept.append(i)
            bypath[key] = v.reshape(1, -1) if seen_v is None else np.vstack([seen_v, v])
        if dropped:
            kept.sort()
            arr = arr[kept]
            rows = [rows[i] for i in kept]
        log(f"[dedupe] 丢弃 {dropped} 块近重复（阈值 {dt}，保留时间较新的那份）")

    np.save(vec, arr)
    with open(meta_p, "w", encoding="utf-8") as f:
        for m in rows:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    json.dump(newstate, open(state_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log(f"[ok] {arr.shape[0]} 块 / {arr.shape[1]} 维 -> {idx}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
