"""Guard: the runtime is AI-free and self-contained.

This is the *enforcement* of architecture principle #13. The engine must run
with no LLM, no API key, and no tokens. AI is permitted only in V5, and only
under the optional `[semantic]` extra — never in the core.

These tests fail the build if:
  * importing the core pulls in any AI/LLM library, or
  * an AI/LLM library appears in the core `dependencies` of pyproject.toml.

(Build-time AI assistance is fine; runtime dependence is not.)
"""

from __future__ import annotations

import importlib
import re
import sys
import tomllib
from pathlib import Path

# Top-level module names that indicate an LLM/AI runtime dependency.
AI_DENYLIST = {
    "openai", "anthropic", "cohere", "replicate", "litellm", "instructor",
    "langchain", "langchain_core", "langchain_community", "langgraph",
    "llama_index", "llama_cpp", "guidance", "dspy",
    "transformers", "sentence_transformers", "sentencepiece", "torch",
    "tensorflow", "tiktoken", "huggingface_hub", "vertexai",
    "google.generativeai", "generativeai",
}

# Every core module — importing all of them must NOT load an AI library.
CORE_MODULES = [
    "ui_discovery",
    "ui_discovery.extract", "ui_discovery.extraction", "ui_discovery.browser",
    "ui_discovery.crawl", "ui_discovery.crawler",
    "ui_discovery.analyze", "ui_discovery.diff",
    "ui_discovery.config", "ui_discovery.cliconfig", "ui_discovery.intake",
    "ui_discovery.adapters", "ui_discovery.adapters.builtin",
    "ui_discovery.analysis.engine", "ui_discovery.analysis.fingerprint",
    "ui_discovery.analysis.regions", "ui_discovery.analysis.components",
    "ui_discovery.analysis.navigation",
    "ui_discovery.probe", "ui_discovery.interactions",
    "ui_discovery.safety", "ui_discovery.network",
    "ui_discovery.reports", "ui_discovery.models", "ui_discovery.util",
    "ui_discovery.auth", "ui_discovery.login",
    # V5 modules must ALSO import AI-free — providers load their SDK lazily,
    # only when instantiated. Importing these modules never does.
    "ui_discovery.semantic", "ui_discovery.llm", "ui_discovery.docgen",
    "ui_discovery.qagen",
]

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _top(name: str) -> str:
    return name.split(".")[0]


def test_importing_core_loads_no_ai_library():
    for mod in CORE_MODULES:
        importlib.import_module(mod)
    loaded_tops = {_top(m) for m in sys.modules}
    offenders = {d for d in AI_DENYLIST if _top(d) in loaded_tops}
    assert not offenders, (
        f"Core import pulled in AI/LLM libraries at runtime: {sorted(offenders)}. "
        "AI is allowed only in V5 under the optional [semantic] extra."
    )


def test_core_dependencies_are_ai_free():
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    core_deps = data["project"]["dependencies"]
    names = {re.split(r"[<>=!~\[ ]", d.strip())[0].lower() for d in core_deps}
    offenders = names & {d.lower() for d in AI_DENYLIST}
    assert not offenders, (
        f"AI/LLM library found in CORE dependencies: {sorted(offenders)}. "
        "Move it to the optional [semantic] extra — the core must stay AI-free."
    )


def test_semantic_extra_is_the_only_place_ai_may_live():
    # The [semantic] extra is the designated (and only) home for V5's optional
    # AI deps. Its presence is the contract; it may be empty until V5 ships.
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    extras = data["project"].get("optional-dependencies", {})
    assert "semantic" in extras, (
        "Expected an optional [semantic] extra to exist as the quarantined home "
        "for V5's AI dependencies."
    )


def test_no_api_keys_read_by_core():
    # The core must not reach for provider credentials.
    src = Path(__file__).resolve().parents[1] / "src" / "ui_discovery"
    suspicious = re.compile(r"(OPENAI|ANTHROPIC|COHERE)_?API_?KEY", re.I)
    for py in src.rglob("*.py"):
        assert not suspicious.search(py.read_text(encoding="utf-8")), (
            f"{py.name} references a provider API key — the core must not."
        )
