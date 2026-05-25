"""Structured logging with PII-aware redaction.

We log at two levels:

  - structlog JSON to stderr (observability for debugging)
  - per-turn append to $HOME/mle_hiring/log.txt (required by AGENTS.md)

The turn log records: prompt summary, decision summary, file actions. Secrets
and PII are redacted before write.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import structlog
    _HAS_STRUCTLOG = True
except Exception:
    _HAS_STRUCTLOG = False


_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{20,}", re.IGNORECASE),
    re.compile(r"\b\d{13,19}\b"),  # card-like
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # SSN-like
)


def redact(s: str) -> str:
    out = s
    for pat in _SECRET_PATTERNS:
        out = pat.sub("[REDACTED]", out)
    return out


def configure(level: str = "INFO") -> Any:
    """Configure structlog + stdlib logging."""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=getattr(logging, level.upper(), logging.INFO),
    )
    if _HAS_STRUCTLOG:
        structlog.configure(
            processors=[
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.add_log_level,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(
                getattr(logging, level.upper(), logging.INFO)
            ),
            cache_logger_on_first_use=True,
        )
        return structlog.get_logger("triage")
    return logging.getLogger("triage")


log = configure(os.environ.get("TRIAGE_LOG_LEVEL", "INFO"))


# ---------------------------------------------------------------------------
# AGENTS.md per-turn log
# ---------------------------------------------------------------------------

def _agents_log_path() -> Path:
    home = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or ".")
    return home / "mle_hiring" / "log.txt"


def append_turn(
    title: str,
    user_prompt: str,
    response_summary: str,
    actions: list[str],
    *,
    agent: str = "claude-code",
    repo_root: str = "",
    branch: str = "main",
    worktree: str = "main",
    parent_agent: str = "none",
) -> None:
    """Append a §5.2 per-turn entry to the shared log file. Best-effort."""
    try:
        path = _agents_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        title = (title or "turn").strip()[:80]
        actions_block = "\n".join(f"* {redact(a)}" for a in actions) or "* (none)"
        body = (
            f"\n## [{ts}] {title}\n\n"
            f"User Prompt (verbatim, secrets redacted):\n{redact(user_prompt)}\n\n"
            f"Agent Response Summary:\n{redact(response_summary)}\n\n"
            f"Actions:\n{actions_block}\n\n"
            f"Context:\n"
            f"tool={agent}\n"
            f"branch={branch}\n"
            f"repo_root={repo_root}\n"
            f"worktree={worktree}\n"
            f"parent_agent={parent_agent}\n"
        )
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
    except Exception:
        # Never let logging break the pipeline.
        pass
