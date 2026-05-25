"""Deterministic chunking.

The chunker is the contract between corpus loader and indexer. It must be
pure: same input -> same output.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from ..config import RETRIEVAL


@dataclass(frozen=True)
class RawChunk:
    text: str
    char_start: int
    char_end: int


_ZW = dict.fromkeys(
    (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF),
    None,
)


def normalise(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_ZW)
    # Collapse Windows newlines, retain blank-line structure.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def _windows(s: str, size: int, overlap: int):
    if size <= 0:
        return
    step = max(1, size - overlap)
    n = len(s)
    i = 0
    while i < n:
        yield i, min(n, i + size)
        if i + size >= n:
            break
        i += step


def chunk_text(text: str, *, size: int | None = None, overlap: int | None = None) -> list[RawChunk]:
    """Paragraph-respecting sliding chunker."""
    size = size or RETRIEVAL.chunk_size
    overlap = overlap or RETRIEVAL.chunk_overlap

    text = normalise(text)
    if not text.strip():
        return []

    paragraphs: list[tuple[int, int, str]] = []  # (start, end, content)
    cursor = 0
    for para in text.split("\n\n"):
        start = text.find(para, cursor)
        if start < 0:
            start = cursor
        end = start + len(para)
        paragraphs.append((start, end, para))
        cursor = end + 2  # length of "\n\n"

    out: list[RawChunk] = []
    buf_start: int | None = None
    buf_end: int = 0
    buf_text: str = ""

    def _flush() -> None:
        nonlocal buf_start, buf_end, buf_text
        if buf_text.strip():
            out.append(RawChunk(text=buf_text.strip(), char_start=buf_start or 0, char_end=buf_end))
        buf_start, buf_end, buf_text = None, 0, ""

    for start, end, p in paragraphs:
        if not p.strip():
            continue
        added_len = len(p) + (2 if buf_text else 0)
        if buf_text and len(buf_text) + added_len > size:
            _flush()
        if len(p) > size:
            _flush()
            for a, b in _windows(p, size, overlap):
                out.append(RawChunk(text=p[a:b].strip(), char_start=start + a, char_end=start + b))
            continue
        if not buf_text:
            buf_start = start
            buf_text = p
            buf_end = end
        else:
            buf_text = buf_text + "\n\n" + p
            buf_end = end

    _flush()
    return out
