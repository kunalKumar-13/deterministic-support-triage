"""Retrieval orchestrator.

The `Retriever` is the only object the pipeline uses. It is built once at
startup, holds the index, and exposes `query(...)`.

`query(q, domain_hint=None)` returns a `RetrievalResult` ready for the
decision engine.
"""

from __future__ import annotations

from typing import Optional

import math

from ..config import DOMAIN_BRAND_TERMS, DOMAINS, RETRIEVAL
from ..models import ChunkRef, RetrievalResult
from .corpus import load_corpus
from .index import CorpusIndex, tokenize


def _normalise_scores(xs: list[float]) -> list[float]:
    if not xs:
        return []
    lo = min(xs)
    hi = max(xs)
    rng = hi - lo
    if rng <= 0:
        return [0.0 for _ in xs]
    return [(x - lo) / rng for x in xs]


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _rrf_merge(rankings: list[list[int]], k: int = 60) -> dict[int, float]:
    """Reciprocal-rank fusion. Returns idx -> fused score."""
    fused: dict[int, float] = {}
    for ranking in rankings:
        for r, idx in enumerate(ranking):
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (k + r + 1)
    return fused


class Retriever:
    def __init__(self, chunks: Optional[list[ChunkRef]] = None):
        if chunks is None:
            chunks = load_corpus()
        self.chunks = chunks
        self.index = CorpusIndex(chunks)
        self._title_token_cache: dict[int, set[str]] = {}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def n_chunks(self) -> int:
        return len(self.chunks)

    def _title_tokens(self, i: int) -> set[str]:
        if i not in self._title_token_cache:
            self._title_token_cache[i] = set(tokenize(self.chunks[i].title))
        return self._title_token_cache[i]

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def query(
        self,
        query_text: str,
        *,
        domain_hint: Optional[str] = None,
        candidate_pool: int = RETRIEVAL.candidate_pool,
        top_k: int = RETRIEVAL.top_k,
    ) -> RetrievalResult:
        if not self.chunks or not query_text.strip():
            return RetrievalResult(no_grounding=True, weak_match=True)

        bm25_raw = self.index.bm25_scores(query_text)
        tfidf_raw = self.index.tfidf_scores(query_text)

        # Build deterministic rankings: sort by (-score, doc_path, chunk_id).
        def _ranking(scores: list[float]) -> list[int]:
            decorated = [
                (-scores[i], self.chunks[i].doc_path, self.chunks[i].chunk_id, i)
                for i in range(len(scores))
            ]
            decorated.sort()
            return [d[3] for d in decorated[:candidate_pool]]

        bm25_rank = _ranking(bm25_raw)
        tfidf_rank = _ranking(tfidf_raw)

        # Reciprocal-rank fusion for the merge step.
        fused = _rrf_merge([bm25_rank, tfidf_rank])
        # Restrict to the candidate pool members.
        candidate_ids = sorted(fused.keys(), key=lambda i: (-fused[i], self.chunks[i].doc_path, self.chunks[i].chunk_id))
        candidate_ids = candidate_ids[:candidate_pool]

        # Rerank with the lexical+semantic blend.
        bm25_n = _normalise_scores([bm25_raw[i] for i in candidate_ids])
        tfidf_n = _normalise_scores([tfidf_raw[i] for i in candidate_ids])
        q_tokens = set(tokenize(query_text))

        rerank: list[tuple[float, int]] = []
        for j, idx in enumerate(candidate_ids):
            ch = self.chunks[idx]
            title_overlap = _jaccard(q_tokens, self._title_tokens(idx))
            base = (
                RETRIEVAL.weight_tfidf * tfidf_n[j]
                + RETRIEVAL.weight_bm25 * bm25_n[j]
                + RETRIEVAL.weight_title * title_overlap
            )
            # Trust + relevance multipliers
            mult = 1.0
            if ch.has_injection_marker:
                mult *= 0.5
            if ch.is_specific_doc:
                mult *= 1.10
            # Recency: light boost for the freshest end of the range.
            mult *= 0.9 + 0.2 * ch.recency_score
            # Domain match
            if domain_hint and ch.domain == domain_hint:
                mult *= 1.15
            elif domain_hint and ch.domain in DOMAINS and ch.domain != domain_hint:
                mult *= 0.85
            score = base * mult
            rerank.append((score, idx))

        # Sort by (-score, doc_path, chunk_id) for deterministic ties.
        rerank.sort(
            key=lambda t: (
                -t[0],
                self.chunks[t[1]].doc_path,
                self.chunks[t[1]].chunk_id,
            )
        )

        chosen = rerank[:top_k]
        chunks_out = [self.chunks[idx] for _, idx in chosen]
        scores_out = [s for s, _ in chosen]

        top1 = scores_out[0] if scores_out else 0.0
        # Agreement = jaccard of top1.text tokens with top2.text tokens.
        if len(chosen) >= 2:
            t1 = set(tokenize(chunks_out[0].text))
            t2 = set(tokenize(chunks_out[1].text))
            agreement = _jaccard(t1, t2)
        else:
            agreement = 0.0

        weak = top1 < RETRIEVAL.weak_match_threshold
        no_grounding = top1 < RETRIEVAL.weak_match_threshold and (
            len(chosen) == 0 or scores_out[0] < 0.2
        )

        return RetrievalResult(
            chunks=chunks_out,
            scores=scores_out,
            top1_score=float(top1),
            agreement=float(agreement),
            weak_match=weak,
            no_grounding=no_grounding,
        )

    # ------------------------------------------------------------------
    # Domain routing helper
    # ------------------------------------------------------------------

    def infer_domain(self, text: str, hint: Optional[str]) -> tuple[Optional[str], bool]:
        """Use brand-term gazetteer to infer the most-likely domain.

        Returns (domain, hint_was_trustworthy)."""
        low = text.lower()
        counts: dict[str, int] = {d: 0 for d in DOMAINS}
        for d, terms in DOMAIN_BRAND_TERMS.items():
            for t in terms:
                if t in low:
                    counts[d] += 1
        # Highest count wins; ties broken alphabetically (stable).
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        best, best_n = ranked[0]
        if best_n == 0:
            # No signal -> trust hint if given.
            if hint and hint.lower() in DOMAINS:
                return hint.lower(), True
            return None, False
        if hint and hint.lower() in DOMAINS:
            if best == hint.lower():
                return best, True
            # Content evidence outweighs a misleading hint.
            return best, False
        return best, False


_singleton: Retriever | None = None


def get_retriever() -> Retriever:
    global _singleton
    if _singleton is None:
        _singleton = Retriever()
    return _singleton
