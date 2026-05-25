"""LLM client wrappers.

We support Anthropic + OpenAI via their official SDKs, both optional.
The client is configured to be deterministic (temperature=0). If no SDK or
API key is available, `LLMClient.call` returns None and the engine falls
back to its heuristic path.

We never raise out of the client — failure returns None.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from ..config import LLM
from ..logging_setup import log


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str


def _try_anthropic(prompt: str, system: str) -> Optional[LLMResult]:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        import anthropic  # type: ignore
    except Exception:
        return None
    try:
        client = anthropic.Anthropic(api_key=api_key, timeout=LLM.timeout_s)
        resp = client.messages.create(
            model=LLM.anthropic_model,
            max_tokens=LLM.max_tokens,
            temperature=LLM.temperature,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        parts = []
        for block in resp.content:
            t = getattr(block, "text", None)
            if t:
                parts.append(t)
        return LLMResult(text="".join(parts), provider="anthropic", model=LLM.anthropic_model)
    except Exception as e:
        log.warning("anthropic_call_failed", error=str(e)[:200]) if hasattr(log, "warning") else None
        return None


def _try_openai(prompt: str, system: str) -> Optional[LLMResult]:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        import openai  # type: ignore
    except Exception:
        return None
    try:
        client = openai.OpenAI(api_key=api_key, timeout=LLM.timeout_s)
        # Prefer the responses API if available, else fall back to chat.
        try:
            resp = client.responses.create(
                model=LLM.openai_model,
                input=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=LLM.temperature,
                max_output_tokens=LLM.max_tokens,
            )
            text = getattr(resp, "output_text", "") or ""
            return LLMResult(text=text, provider="openai", model=LLM.openai_model)
        except Exception:
            resp = client.chat.completions.create(
                model=LLM.openai_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=LLM.temperature,
                max_tokens=LLM.max_tokens,
                seed=LLM.seed,
            )
            return LLMResult(
                text=resp.choices[0].message.content or "",
                provider="openai",
                model=LLM.openai_model,
            )
    except Exception as e:
        log.warning("openai_call_failed", error=str(e)[:200]) if hasattr(log, "warning") else None
        return None


class LLMClient:
    def __init__(self) -> None:
        self.provider_pref = LLM.provider

    def call(self, system: str, prompt: str) -> Optional[LLMResult]:
        if self.provider_pref == "off":
            return None

        order: list[str]
        if self.provider_pref == "auto":
            order = ["anthropic", "openai"]
        else:
            order = [self.provider_pref]

        for p in order:
            if p == "anthropic":
                r = _try_anthropic(prompt, system)
            elif p == "openai":
                r = _try_openai(prompt, system)
            else:
                r = None
            if r is not None and r.text.strip():
                return r
        return None
