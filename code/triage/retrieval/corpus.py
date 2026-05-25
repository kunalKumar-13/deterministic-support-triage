"""Corpus loader.

Walks `data/<domain>/**` deterministically. Loads text + markdown files only
(binary files are skipped with a warning). Produces a list of `ChunkRef`
plus the raw chunk texts (for indexing).
"""

from __future__ import annotations

import os
from pathlib import Path

from ..config import CORPUS_DIRS, REPO_ROOT
from ..models import ChunkRef
from .chunking import chunk_text


_TEXT_EXTS = {".md", ".markdown", ".txt", ".rst", ".html", ".htm"}


def _domain_of(p: Path) -> str:
    parts = p.relative_to(REPO_ROOT).parts
    if len(parts) >= 2 and parts[0] == "data":
        return parts[1]
    return "unknown"


def _title_of(text: str, path: Path) -> str:
    for line in text.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:120]
    return path.stem.replace("_", " ").replace("-", " ")


def _is_specific(path: Path, text: str) -> bool:
    """A doc is 'specific' if it has a deep path or precise filename."""
    rel = path.relative_to(REPO_ROOT)
    if len(rel.parts) >= 4:
        return True
    name = path.stem.lower()
    return not any(
        tag in name for tag in ("overview", "intro", "getting-started", "general", "about")
    )


def _recency_score(path: Path) -> float:
    """Naive proxy: file mtime mapped onto [0,1]. Stable across runs of the
    same checkout. Returns 0.5 if mtime is unavailable."""
    try:
        mt = path.stat().st_mtime
    except OSError:
        return 0.5
    # Map to a 5-year window ending now.
    import time
    now = time.time()
    age_s = max(0.0, now - mt)
    five_years = 5 * 365 * 24 * 3600
    s = 1.0 - min(1.0, age_s / five_years)
    return round(s, 3)


def _has_injection_marker(text: str) -> bool:
    low = text.lower()
    flags = (
        "ignore previous instructions",
        "developer mode",
        "system prompt",
        "act as if you were",
        "begin system instructions",
    )
    return any(f in low for f in flags)


def list_corpus_files() -> list[Path]:
    files: list[Path] = []
    for base in CORPUS_DIRS:
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file() and p.suffix.lower() in _TEXT_EXTS:
                files.append(p)
    return files


def load_corpus() -> list[ChunkRef]:
    """Load + chunk corpus into a deterministic list of ChunkRef."""
    chunks: list[ChunkRef] = []
    files = list_corpus_files()
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not text.strip():
            continue
        title = _title_of(text, path)
        domain = _domain_of(path)
        is_specific = _is_specific(path, text)
        recency = _recency_score(path)
        rel = str(path.relative_to(REPO_ROOT)).replace(os.sep, "/")
        for i, rc in enumerate(chunk_text(text)):
            chunks.append(
                ChunkRef(
                    doc_path=rel,
                    chunk_id=i,
                    title=title,
                    domain=domain,
                    text=rc.text,
                    char_start=rc.char_start,
                    char_end=rc.char_end,
                    has_injection_marker=_has_injection_marker(rc.text),
                    is_specific_doc=is_specific,
                    recency_score=recency,
                )
            )
    return chunks
