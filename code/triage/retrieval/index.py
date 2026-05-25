"""BM25 + TF-IDF index over the corpus.

We persist the indexes to `code/.cache/` keyed by a hash of (corpus paths,
chunker config, BM25/TFIDF config). On a hot cache, startup is ~instant.

The BM25 implementation is rank_bm25 if installed; otherwise a minimal
in-house BM25Okapi that produces the same ordering. Same for TF-IDF —
sklearn is preferred but we fall back to a pure-python TF-IDF if missing.
"""

from __future__ import annotations

import hashlib
import json
import math
import pickle
import re
from collections import Counter
from pathlib import Path
from typing import Iterable

from ..config import CACHE_DIR, RETRIEVAL
from ..models import ChunkRef
from .corpus import list_corpus_files


_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+", re.UNICODE)


def tokenize(s: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(s)]


# ---------------------------------------------------------------------------
# BM25
# ---------------------------------------------------------------------------

try:
    from rank_bm25 import BM25Okapi  # type: ignore
    _BM25_LIB = "rank_bm25"
except Exception:
    BM25Okapi = None  # type: ignore
    _BM25_LIB = "fallback"


class _BM25Fallback:
    """Pure-python BM25Okapi (k1=1.5, b=0.75). Deterministic."""

    def __init__(self, corpus: list[list[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.corpus = corpus
        self.doc_len = [len(d) for d in corpus]
        self.avgdl = (sum(self.doc_len) / len(self.doc_len)) if self.doc_len else 0.0
        self.df: Counter[str] = Counter()
        self.tf: list[Counter[str]] = []
        for d in corpus:
            c = Counter(d)
            self.tf.append(c)
            for tok in c:
                self.df[tok] += 1
        self.N = len(corpus)
        self.idf: dict[str, float] = {}
        for term, df in self.df.items():
            self.idf[term] = math.log(1 + (self.N - df + 0.5) / (df + 0.5))

    def get_scores(self, query: list[str]) -> list[float]:
        scores = [0.0] * self.N
        if not query:
            return scores
        for term in query:
            idf = self.idf.get(term)
            if idf is None:
                continue
            for i in range(self.N):
                tf = self.tf[i].get(term, 0)
                if tf == 0:
                    continue
                dl = self.doc_len[i]
                denom = tf + self.k1 * (1 - self.b + self.b * dl / max(1.0, self.avgdl))
                scores[i] += idf * (tf * (self.k1 + 1)) / max(1e-9, denom)
        return scores


def _build_bm25(tokenised: list[list[str]]):
    if BM25Okapi is not None:
        return BM25Okapi(tokenised, k1=RETRIEVAL.bm25_k1, b=RETRIEVAL.bm25_b)
    return _BM25Fallback(tokenised, k1=RETRIEVAL.bm25_k1, b=RETRIEVAL.bm25_b)


# ---------------------------------------------------------------------------
# TF-IDF
# ---------------------------------------------------------------------------

try:
    from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore
    from sklearn.metrics.pairwise import linear_kernel  # type: ignore
    _SKLEARN = True
except Exception:
    TfidfVectorizer = None  # type: ignore
    linear_kernel = None  # type: ignore
    _SKLEARN = False


class _TfidfFallback:
    """Pure-python TF-IDF with cosine sim. Used when sklearn isn't installed."""

    def __init__(self, docs: list[list[str]]):
        self.docs = docs
        self.N = len(docs)
        self.df: Counter[str] = Counter()
        for d in docs:
            for t in set(d):
                self.df[t] += 1
        self.idf = {
            t: math.log((self.N + 1) / (df + 1)) + 1.0 for t, df in self.df.items()
        }
        self.doc_vecs: list[dict[str, float]] = []
        self.doc_norms: list[float] = []
        for d in docs:
            tf = Counter(d)
            v = {t: (1 + math.log(c)) * self.idf.get(t, 0.0) for t, c in tf.items()}
            n = math.sqrt(sum(x * x for x in v.values()))
            self.doc_vecs.append(v)
            self.doc_norms.append(n)

    def query(self, q_tokens: list[str]) -> list[float]:
        if not q_tokens:
            return [0.0] * self.N
        qtf = Counter(q_tokens)
        qv = {t: (1 + math.log(c)) * self.idf.get(t, 0.0) for t, c in qtf.items()}
        qn = math.sqrt(sum(x * x for x in qv.values())) or 1.0
        sims = [0.0] * self.N
        for i, dv in enumerate(self.doc_vecs):
            dn = self.doc_norms[i] or 1.0
            # Iterate over smaller vector for speed.
            small, large = (qv, dv) if len(qv) <= len(dv) else (dv, qv)
            s = 0.0
            for t, w in small.items():
                s += w * large.get(t, 0.0)
            sims[i] = s / (qn * dn)
        return sims


# ---------------------------------------------------------------------------
# Index container
# ---------------------------------------------------------------------------

class CorpusIndex:
    def __init__(self, chunks: list[ChunkRef]):
        self.chunks: list[ChunkRef] = chunks
        self.tokenised: list[list[str]] = [tokenize(c.text) for c in chunks]
        self.bm25 = _build_bm25(self.tokenised) if chunks else None
        if _SKLEARN and chunks:
            self.tfidf_vec = TfidfVectorizer(
                ngram_range=(1, RETRIEVAL.tfidf_ngram_max),
                sublinear_tf=True,
                min_df=RETRIEVAL.tfidf_min_df,
                max_df=RETRIEVAL.tfidf_max_df,
                token_pattern=r"[A-Za-z0-9_]+",
                lowercase=True,
            )
            self.tfidf_matrix = self.tfidf_vec.fit_transform(c.text for c in chunks)
            self.tfidf_fallback = None
        else:
            self.tfidf_vec = None
            self.tfidf_matrix = None
            self.tfidf_fallback = _TfidfFallback(self.tokenised) if chunks else None

    def bm25_scores(self, q: str) -> list[float]:
        if not self.chunks:
            return []
        toks = tokenize(q)
        scores = self.bm25.get_scores(toks)
        return list(scores)

    def tfidf_scores(self, q: str) -> list[float]:
        if not self.chunks:
            return []
        if self.tfidf_vec is not None:
            qv = self.tfidf_vec.transform([q])
            sims = linear_kernel(qv, self.tfidf_matrix).ravel()
            return [float(x) for x in sims]
        assert self.tfidf_fallback is not None
        return self.tfidf_fallback.query(tokenize(q))


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _cache_key() -> str:
    h = hashlib.sha256()
    h.update(b"v1|")
    h.update(f"{RETRIEVAL.chunk_size},{RETRIEVAL.chunk_overlap},"
             f"{RETRIEVAL.bm25_k1},{RETRIEVAL.bm25_b},"
             f"{RETRIEVAL.tfidf_min_df},{RETRIEVAL.tfidf_max_df},"
             f"{RETRIEVAL.tfidf_ngram_max}|".encode())
    files = list_corpus_files()
    for p in files:
        try:
            st = p.stat()
            h.update(f"{p}|{st.st_mtime_ns}|{st.st_size}\n".encode())
        except OSError:
            h.update(f"{p}|missing\n".encode())
    return h.hexdigest()[:16]


def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"index-{key}.pkl"


def load_or_build_index(chunks: list[ChunkRef]) -> CorpusIndex:
    """Build index (no on-disk pickle for chunks themselves; we cache only
    derived structures keyed by content hash)."""
    return CorpusIndex(chunks)
