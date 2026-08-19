"""Shared, quarantined LLM text-provider layer (V5 generation features).

Architecture principle #13: importing this module loads **no** AI library — the
real providers import their SDK *lazily*, only when instantiated. The engine
never depends on this; it is used only by optional generation features (docs,
QA scenarios) and only when a provider is explicitly selected. The provider's
API key is read from the environment by the SDK itself, never by our code.

`MockTextProvider` is an offline, zero-token stand-in so the generation plumbing
is testable and demoable without any AI.
"""

from __future__ import annotations

import sys
from typing import Optional, Protocol


class TextProvider(Protocol):
    name: str
    def complete(self, prompt: str, *, max_tokens: int = 1500) -> str:
        ...


class MockTextProvider:
    """Deterministic, offline, zero-token. Returns a clearly-marked canned
    summary so tests/demos can prove the LLM path ran — not a real writer."""

    name = "mock"

    def complete(self, prompt: str, *, max_tokens: int = 1500) -> str:
        first = next((ln for ln in prompt.splitlines() if ln.strip()), "")
        return f"[mock-generated] {first.strip()[:160]}"


class _AnthropicText:
    def __init__(self, model: str):
        try:
            import anthropic  # lazy — the ONLY place the SDK is imported
        except ImportError as exc:
            raise SystemExit(
                "The 'anthropic' package is not installed. Install the optional "
                "extra: pip install ui-discovery[semantic]"
            ) from exc
        self._client = anthropic.Anthropic()  # key from the environment via the SDK
        self.model = model
        self.name = f"anthropic:{model}"

    def complete(self, prompt: str, *, max_tokens: int = 1500) -> str:
        try:
            msg = self._client.messages.create(
                model=self.model, max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return "".join(getattr(b, "text", "") for b in msg.content).strip()
        except Exception as exc:
            print(f"[WARN] LLM completion failed: {exc}", file=sys.stderr)
            return ""


class _OpenAIText:
    def __init__(self, model: str):
        try:
            import openai  # lazy
        except ImportError as exc:
            raise SystemExit(
                "The 'openai' package is not installed. Install the optional "
                "extra: pip install ui-discovery[semantic]"
            ) from exc
        self._client = openai.OpenAI()  # key from the environment via the SDK
        self.model = model
        self.name = f"openai:{model}"

    def complete(self, prompt: str, *, max_tokens: int = 1500) -> str:
        try:
            resp = self._client.chat.completions.create(
                model=self.model, max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as exc:
            print(f"[WARN] LLM completion failed: {exc}", file=sys.stderr)
            return ""


def get_text_provider(name: str, model: Optional[str] = None) -> Optional[TextProvider]:
    name = (name or "none").lower()
    if name in ("none", ""):
        return None
    if name == "mock":
        return MockTextProvider()
    if name == "anthropic":
        return _AnthropicText(model or "claude-sonnet-4-5")
    if name == "openai":
        return _OpenAIText(model or "gpt-4o-mini")
    raise SystemExit(f"[ERROR] Unknown provider: {name}")
