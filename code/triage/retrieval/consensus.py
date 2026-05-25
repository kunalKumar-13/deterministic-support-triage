"""Retrieval consensus validation.

Given a list of retrieved chunks plus their scores, returns a
`ConsensusSignal` describing how much the top chunks agree with each
other on the substantive content of the answer.

Signals computed:
  * pairwise topical similarity (jaccard of content tokens)
  * numeric disagreement detection (different dollar amounts, durations,
    counts mentioned in chunks that purport to answer the same question)
  * imperative disagreement detection (one chunk says "always" / "never"
    where another contradicts)
  * single-source dependency (only one chunk supports the would-be
    response)

The signal is deterministic and additive.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import ChunkRef


_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")

# Numeric facts are scoped to clearly *policy-bearing* numbers, so we don't
# fire on incidental measurements. We require the unit to be a money or
# percent unit, since those are the most likely contradiction surface.
_POLICY_NUMBER_RE = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*"
    r"(%|percent|\$|usd|eur|gbp|inr|dollar|dollars)\b",
    re.I,
)
_IMPERATIVE_NEGATION_RE = re.compile(
    r"\b(?:never|always|must not|cannot|may not|will not|won't)\b",
    re.I,
)
# A chunk only counts as a "policy claim" chunk if it contains BOTH policy
# nouns and policy verbs / amounts.
_POLICY_KEYWORDS_RE = re.compile(
    r"\b(?:refund|reimburse|charge|fee|amount|limit|cap|liability|"
    r"policy|chargeback|dispute|premium|discount|coverage|"
    r"deductible)\b",
    re.I,
)


def _tokens(s: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(s)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _numeric_facts(text: str) -> set[str]:
    out: set[str] = set()
    for m in _POLICY_NUMBER_RE.finditer(text):
        out.add(m.group(0).lower().replace(" ", ""))
    return out


def _is_policy_chunk(text: str) -> bool:
    return bool(_POLICY_KEYWORDS_RE.search(text)) and bool(_POLICY_NUMBER_RE.search(text))


@dataclass(frozen=True)
class ConsensusSignal:
    n_chunks: int
    pairwise_topical_agreement: float = 0.0
    numeric_disagreement: bool = False
    imperative_disagreement: bool = False
    single_source_only: bool = False
    tags: tuple[str, ...] = field(default_factory=tuple)
    score: float = 1.0  # 1.0 = perfect agreement, 0.0 = total conflict

    @property
    def critical(self) -> bool:
        """Returns True if the signal is strong enough to force escalation."""
        return self.numeric_disagreement or self.imperative_disagreement


def analyze(chunks: list[ChunkRef]) -> ConsensusSignal:
    if not chunks:
        return ConsensusSignal(n_chunks=0, score=0.0, tags=("no_chunks",))

    if len(chunks) == 1:
        return ConsensusSignal(
            n_chunks=1,
            pairwise_topical_agreement=0.0,
            single_source_only=True,
            tags=("single_source_only",),
            score=0.55,  # one source is OK but not strongly corroborated
        )

    tags: list[str] = []
    n = len(chunks)
    tokens = [_tokens(c.text) for c in chunks]
    numerics = [_numeric_facts(c.text) for c in chunks]
    negations = [
        len(_IMPERATIVE_NEGATION_RE.findall(c.text)) for c in chunks
    ]

    # Pairwise jaccard on the top 3 chunks.
    top = min(3, n)
    jacc_pairs: list[float] = []
    for i in range(top):
        for j in range(i + 1, top):
            jacc_pairs.append(_jaccard(tokens[i], tokens[j]))
    avg_jacc = sum(jacc_pairs) / max(1, len(jacc_pairs))

    # Numeric disagreement: top-3 chunks where BOTH are *policy* chunks
    # (contain both policy keywords and policy numbers), they share a
    # strong topic (jaccard > 0.35), and their numeric fact sets are
    # disjoint with at least two numbers between them.
    policy_chunks = [_is_policy_chunk(c.text) for c in chunks]
    numeric_dis = False
    for i in range(top):
        for j in range(i + 1, top):
            if not policy_chunks[i] or not policy_chunks[j]:
                continue
            if not numerics[i] or not numerics[j]:
                continue
            shared_topic = _jaccard(tokens[i], tokens[j]) > 0.35
            disjoint_numbers = not (numerics[i] & numerics[j])
            if shared_topic and disjoint_numbers and len(numerics[i] | numerics[j]) >= 2:
                numeric_dis = True
                tags.append(f"numeric_disagreement:{i},{j}")
                break
        if numeric_dis:
            break

    # Imperative disagreement: one chunk has many "never/always" assertions
    # while another in the same topical area has many regular qualifiers.
    # Require BOTH chunks to be policy chunks and a high topical overlap
    # so we don't fire on incidental wording differences.
    imperative_dis = False
    if top >= 2 and avg_jacc > 0.40:
        # Only consider chunks that are both policy-bearing.
        pol = [_is_policy_chunk(c.text) for c in chunks[:top]]
        if sum(pol) >= 2 and max(negations[:top]) >= 3 and min(negations[:top]) == 0:
            imperative_dis = True
            tags.append("imperative_disagreement")

    score = avg_jacc  # baseline = topical overlap
    if numeric_dis:
        score *= 0.4
    if imperative_dis:
        score *= 0.6
    score = max(0.0, min(1.0, score))

    return ConsensusSignal(
        n_chunks=n,
        pairwise_topical_agreement=round(avg_jacc, 3),
        numeric_disagreement=numeric_dis,
        imperative_disagreement=imperative_dis,
        single_source_only=False,
        tags=tuple(tags),
        score=round(score, 3),
    )
