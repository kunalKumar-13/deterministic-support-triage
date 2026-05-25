"""Smoke tests for retrieval. Skipped if corpus is empty."""

import pytest

from triage.retrieval import get_retriever


def test_retriever_builds():
    r = get_retriever()
    assert r is not None


def test_retriever_returns_ranked_results():
    r = get_retriever()
    if r.n_chunks == 0:
        pytest.skip("corpus is empty")
    res = r.query("how do I reset my password", domain_hint="devplatform")
    assert res.chunks
    # Top result should mention password/reset.
    top_text = res.chunks[0].text.lower()
    assert any(k in top_text for k in ("password", "reset", "sign"))


def test_retriever_is_deterministic():
    r = get_retriever()
    if r.n_chunks == 0:
        pytest.skip("corpus is empty")
    q = "I want to dispute a transaction"
    a = r.query(q)
    b = r.query(q)
    assert [c.doc_path for c in a.chunks] == [c.doc_path for c in b.chunks]
    assert a.scores == b.scores
