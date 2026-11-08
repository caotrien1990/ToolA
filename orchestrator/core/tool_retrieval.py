"""Local BM25 ranking of callable tools. Each row can carry curated trigger_examples."""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import unicodedata
from collections import Counter, defaultdict
from functools import lru_cache


_CORPUS_PATH = Path(__file__).with_name("tool_corpus.json")


@lru_cache(maxsize=2)
def _read_corpus(path, mtime_ns, size):
    # mtime/size invalidate the bounded cache; samples are evaluation data only.
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    result = {}
    for name, row in raw.items():
        if name == "_meta":
            continue
        if not isinstance(row, dict) or row.get("name") != name:
            raise ValueError(f"invalid tool corpus entry: {name}")
        triggers = row.get("triggers")
        if not isinstance(triggers, list) or not all(
                isinstance(value, str) and value.strip() for value in triggers):
            raise ValueError(f"invalid corpus triggers: {name}")
        result[name] = tuple(triggers)
    return result


def corpus_triggers():
    info = _CORPUS_PATH.stat()
    return _read_corpus(str(_CORPUS_PATH), info.st_mtime_ns, info.st_size)

def tokens(value):
    folded = str(value).casefold().replace("đ", "d")
    folded = "".join(c for c in unicodedata.normalize("NFKD", folded)
                     if not unicodedata.combining(c))
    words = re.findall(r"[a-z0-9]+(?:_[a-z0-9]+)*", folded)
    return [part for word in words for part in
            ([word, *word.split("_")] if "_" in word else [word])]


@lru_cache(maxsize=2)
def _index(documents):
    postings = defaultdict(list)
    lengths = {}
    for name, purpose, description, examples in documents:
        terms = tokens(name) * 4 + tokens(purpose) * 2 + tokens(description)
        for phrase in examples:
            terms.extend(tokens(phrase) * 2)
        lengths[name] = len(terms)
        for term, frequency in Counter(terms).items():
            postings[term].append((name, frequency))
    return postings, lengths, sum(lengths.values()) / max(len(lengths), 1)


def rank_tools(query, schemas, purposes, excluded=(), boosts=(), eligible=None, limit=8):
    """Rank eligible non-deprecated tools by BM25 with explicit rule evidence."""
    curated = corpus_triggers()
    documents = []
    for entry in schemas:
        fn = entry["function"]
        name = fn["name"]
        if name in excluded or fn.get("deprecated") or (eligible is not None and name not in eligible):
            continue
        examples = fn.get("trigger_examples", ())
        if not isinstance(examples, (tuple, list)):
            raise ValueError(f"trigger_examples must be a list for {name}")
        examples = (*examples, *curated.get(name, ()))
        documents.append((name, purposes.get(name, ""), fn.get("description", ""), examples))
    postings, lengths, average = _index(tuple(documents))
    scores = Counter()
    matched = defaultdict(list)
    for term in sorted(set(tokens(query))):
        rows = postings.get(term, ())
        idf = math.log1p((len(lengths) - len(rows) + 0.5) / (len(rows) + 0.5))
        for name, frequency in rows:
            norm = 1.2 * (0.25 + 0.75 * lengths[name] / max(average, 1))
            scores[name] += idf * frequency * 2.2 / (frequency + norm)
            matched[name].append(term)
    # Preserve verified rule routing until an independent dataset authorizes promotion.
    # First rule = primary, then sibling rules in existing precedence order.
    ceiling = max(scores.values(), default=0)
    for order, name in enumerate(dict.fromkeys(boosts)):
        if name in lengths:
            scores[name] += (ceiling + 1) / (order + 1)
            matched[name].insert(0, "routing rule")
    return [{"tool": name, "score": round(scores[name], 4),
             "why": ", ".join(matched[name][:8])}
            for name in sorted(scores, key=lambda n: (-scores[n], n))[:limit]]
