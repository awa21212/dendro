"""Corpus registry: which folders feed which index.

Nothing about any particular machine is baked in. Dendro reads
``corpus.config.json`` next to this file (or the path in
``$DENDRO_CORPUS_CONFIG``)::

    {
      "default_scope": ["notes"],
      "corpora": {
        "notes": {
          "desc": "my markdown notes",
          "sources": [["~/notes", null], ["~/papers", ["*.md"]]]
        }
      }
    }

Each source is ``[path, include]``:

* ``path``  - directory or file; ``~`` and relative paths are allowed
              (relative to the config file).
* ``include`` - ``null`` to take every ``*.md`` under ``path``, or a list of
              filename fragments; only files whose name contains one of them
              are indexed. Handy to pick one or two files out of a big folder.

``index`` is optional. When omitted, the index lives in ``index_<name>``
next to this file. Give an absolute path if you want the index elsewhere.

A demo corpus ships in ``examples/demo`` so the very first build runs without
any configuration at all.
"""

import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("DENDRO_CORPUS_CONFIG") or os.path.join(
    BASE, "corpus.config.json"
)

# Optional: path to an external vector store (any format load_st() understands)
# to search alongside the corpora. Leave empty to disable `ask.py --st`.
ST_VECTORS = ""

# Friendly names for corpora that were renamed at some point.
RETIRED = {}


def _resolve(path, root):
    path = os.path.expanduser(str(path))
    if not os.path.isabs(path):
        path = os.path.join(root, path)
    return os.path.normpath(path)


def _norm(entry, root):
    """[path, include] -> (abs path, include or None)."""
    if isinstance(entry, str):
        entry = [entry, None]
    path, include = entry[0], entry[1] if len(entry) > 1 else None
    if include is not None:
        include = [str(x) for x in include]
    return (_resolve(path, root), include)


def _load():
    demo = _resolve("examples/demo", os.path.dirname(BASE))
    cfg = {
        "default_scope": ["demo"],
        "corpora": {
            "demo": {
                "desc": "bundled demo notes (no configuration needed)",
                "sources": [[demo, None]],
            }
        },
    }
    if os.path.isfile(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            user = json.load(f)
        root = os.path.dirname(os.path.abspath(CONFIG_PATH))
        corpora = {}
        for name, spec in (user.get("corpora") or {}).items():
            spec = dict(spec or {})
            spec["sources"] = [_norm(s, root) for s in spec.get("sources") or []]
            spec.setdefault("desc", name)
            if spec.get("index"):
                spec["index"] = _resolve(spec["index"], root)
            corpora[name] = spec
        if corpora:
            cfg["corpora"] = corpora
            cfg["default_scope"] = user.get("default_scope") or sorted(corpora)
        cfg["retired"] = user.get("retired") or {}
    return cfg


_CFG = _load()
RETIRED.update(_CFG.get("retired") or {})
DEFAULT_SCOPE = tuple(_CFG["default_scope"])


def _finalize():
    out = {}
    for name, spec in _CFG["corpora"].items():
        out[name] = {
            "index": spec.get("index") or os.path.join(BASE, "index_" + name),
            "desc": spec.get("desc") or name,
            "sources": [tuple(s) for s in spec.get("sources") or []],
        }
    return out


CORPUS = _finalize()


def names():
    return list(CORPUS)


def get(name):
    if name in CORPUS:
        return CORPUS[name]
    if name in RETIRED:
        raise SystemExit(
            "corpus %r was renamed to %r - use that name instead"
            % (name, RETIRED[name])
        )
    raise SystemExit(
        "unknown corpus %r. Available: %s" % (name, ", ".join(sorted(CORPUS)))
    )
