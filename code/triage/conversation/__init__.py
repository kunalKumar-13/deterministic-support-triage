"""Multi-turn conversation consistency analyzer.

Detects patterns where a ticket's conversation history is internally
inconsistent in ways that suggest manipulation:

  * cross-ticket reference claims ("row 48 above", "previous agent
    TK-4892 said X")
  * identity / account / card claims that change between turns
  * escalating pressure tactics over turns
  * social-engineering ramps ("step 1 / step 2 / step 3" pretending to
    be benign before asking for something sensitive)

The analyzer is deterministic, rule-based. It does not call the LLM.
"""
from .consistency import ConsistencySignal, analyze

__all__ = ["ConsistencySignal", "analyze"]
