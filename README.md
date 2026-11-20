# Dendro

**Retrieval that remembers when it was written.**

Your notes are a tree. Every ring is a time you learned something. Dendro reads
the rings: a note written last week outranks a note written last year, even when
the older one is a slightly better keyword match.

No vector database. No embedding API. No GPU. One folder, one process, about 700
lines per file and one `pip install`.

Real output on the bundled demo corpus (`examples/demo`, six failure notes):

```
$ python dendro/ask.py --corpus demo "why does the docker build ignore the cache on CI" -k 3
[1] docker-build-cache.md    0.791   (similarity 0.721 x recency 1.00)
[2] nginx-502-upstream.md    0.632
[3] git-line-endings.md      0.630
```

(The CLI speaks Chinese — that is where it grew up. The numbers are what matter.)

## Quickstart

```bash
git clone https://github.com/awa21212/dendro && cd dendro
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
ollama pull bge-m3                                 # the only external piece

python dendro/build.py --corpus demo               # 6 files -> 11 chunks, ~4 s
python dendro/ask.py   --corpus demo "ssh says permission too open" -k 3
python dendro/measure.py                           # hit@1 / score / latency
```

Then point it at your own notes: copy `corpus.config.example.json` to
`corpus.config.json`, list the folders you want indexed, and rebuild.

## Why another retriever

Most local RAG stacks answer "what is similar". That is the wrong question for a
personal knowledge base, where six months of notes contradict each other and the
newest one is usually right.

Dendro scores three things instead:

| term | weight | what it captures |
|---|---|---|
| cosine | 0.72 | meaning (bge-m3, 1024-d, via Ollama) |
| lexical | 0.28 | exact words, **including the file name** |
| recency | 0.25 | `0.5 ** (age_days / 30)`, per chunk, not per file |

`score = 0.75 * (0.72*cos + 0.28*lex + card_bonus) + 0.25 * decay`

Three details that are load-bearing and easy to get wrong:

- **Time is per chunk.** A single growing file (`diary.md`) has one mtime; its
  paragraphs are years apart. Dendro stamps every chunk.
- **The file name joins the lexical match.** Ask for "factor catalogue" and the
  hit is `因子库总览.md`, whose body never repeats its own title.
- **Cards get a small prior.** Notes you maintain deliberately (`cards/*.md`)
  outrank raw logs at equal similarity — 0.03, a plateau, not a knife edge.

## Benchmarks

Two holdouts, same corpus, identical parameters. Not a leaderboard — a
regression guard:

| scorer | 19 short notes | 20 long documents |
|---|---|---|
| BM25 (lexical only) | 63% | 55% |
| dense only | 74% | 10% |
| dense + RRF | 74% | 35% |
| **Dendro** | **89%** | **40%** + 65% in top 3 |

Score fusion (RRF) buys nothing here; the recency and card terms are what move
the top line. Long documents stay hard: at 700 characters per chunk, a question
about a six-page write-up has to out-rank its own neighbours.

Latency on 715 blocks, one query, warm cache: **41–57 ms** total
(56 ms embedding round trip + ~1.5 ms scoring + numpy). cold start 17 ms to load
the index.

## Install

```bash
pip install -r requirements.txt          # numpy
ollama pull bge-m3                       # or any embedding model
```

## Use

```bash
# 1. build an index (demo corpus needs no configuration at all)
python build.py --corpus demo

# 2. ask
python ask.py --corpus demo "ssh key permission too open"

# 3. optional: retrieval check on your own cases
python measure.py --cases examples/cases.json
```

Point it at your own notes:

```bash
cp corpus.config.example.json corpus.config.json   # edit paths
python build.py --corpus notes
python ask.py --corpus notes "my question"
```

Or leave the file out and set `DENDRO_CORPUS_CONFIG=/path/to/config.json`.

### Commands

| script | what it does |
|---|---|
| `build.py` | chunk → embed → `vectors.npy` + `meta.jsonl`, incremental by sha1 |
| `ask.py` | hybrid retrieval, `--hours` time window, `--min-ratio` floor, `--llm` to summarise |
| `preflight.py` | multi-corpus "what do I already know about X", for agents |
| `measure.py` | hit@1 / hit@k / score / latency on a case file |
| `hitlog.py` | log which hit you actually used, then `--report` for rank and age |
| `audit_cards.py` | "code changed, note did not" — kept as a habit, not a demo |

## For agents

`preflight.py` prints a compact block that a coding agent can read before it
starts:

```
$ python preflight.py "docker build cache" -k 2
== 开工前先读 ==
[1·notes] docker-build-cache.md  综合 0.79
```

That block is the whole point: retrieval that runs *before* the model starts
guessing, with the age of each hit visible so it knows whether it is reading a
habit or a fossil.

## What Dendro is not

- Not a semantic search engine. With one near-duplicate per document, top-1
  collapses from 100% to 62% — it is a needle retriever, and near-miss decoys
  are its weak point.
- Not a chunker for PDFs or code ASTs. It reads text; bring your own extraction.
- `--st` searches an external vector store too (optional, off by default).

## Layout

```
dendro/
  build.py       chunk + embed + index
  ask.py         retrieve
  preflight.py   multi-corpus preflight block
  measure.py     health check
  hitlog.py      feedback loop
  audit_cards.py habit checker
  corpora.py     corpus registry (config driven)
examples/
  demo/*.md      the corpus used by every command above
  cases.json     12 questions pointing at those files
```

## License

Business Source License 1.1 — see [LICENSE](LICENSE). Free for personal,
research and evaluation use. Production use needs a commercial license; after
four years the license converts to Apache-2.0. [COMMERCIAL.md](COMMERCIAL.md)
for terms.
