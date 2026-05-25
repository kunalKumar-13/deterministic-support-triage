"""Retrieval engine: corpus indexing, lexical + sparse vector retrieval,
deterministic merge, lexical rerank, and document trust scoring.

Public surface is `Retriever`, the singleton accessor.
"""
from .engine import Retriever, get_retriever

__all__ = ["Retriever", "get_retriever"]
