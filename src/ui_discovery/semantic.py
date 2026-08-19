"""V5.1 — semantic element classification.

**Deterministic by default (zero tokens).** Every fingerprinted element is
labelled from the signals we already capture (role, accessible name, landmark,
category, safety class). An optional LLM provider can *refine* those labels —
but it is quarantined per architecture principle #13:

  * importing this module loads **no** AI library (providers import their SDK
    lazily, only when instantiated);
  * the deterministic path needs no provider, no key, no network;
  * an LLM provider is used only when explicitly selected (`--provider ...`) and
    the `[semantic]` extra is installed;
  * refinement layers *on top of* the analysis and writes a separate
    `semantics.json` — it never mutates the raw analysis/crawl.

    python -m ui_discovery.semantic output/<slug>/ [--provider none|mock|anthropic|openai]
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Protocol

from . import SCHEMA_VERSION, __version__
from .models import Analysis, ElementFingerprint, Semantics, SemanticLabel
from .safety import classify_label

LABELS = [
    "primary_action", "secondary_action", "navigation", "filter",
    "data_display", "destructive", "form_input", "informational",
]

_PRIMARY_HINTS = {
    "create", "add", "new", "save", "submit", "continue", "next", "confirm",
    "apply", "done", "finish", "start", "generate", "upload",
}
_FILTER_HINTS = {"search", "filter", "query", "find", "sort", "refine"}


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


def _has(name: str, words: set[str]) -> bool:
    return any(w in name.split() or w in name for w in words)


# --- deterministic classifier (the default; zero tokens) --------------------

def classify_fingerprint(fp: ElementFingerprint, page_url: str) -> SemanticLabel:
    cat = fp.category
    role = (fp.role or "").lower()
    name = _norm(fp.accessible_name)
    safety = classify_label(fp.accessible_name)  # SAFE / CAUTION / BLOCK

    label, confidence, why = "informational", "low", "default"

    if cat == "table" or role in ("table", "grid"):
        label, confidence, why = "data_display", "high", "table/grid"
    elif cat == "image":
        label, confidence, why = "informational", "high", "image"
    elif cat == "nav":
        label, confidence, why = "navigation", "high", "nav landmark"
    elif cat == "dialog":
        label, confidence, why = "informational", "medium", "dialog container"
    elif cat == "form":
        label, confidence, why = "form_input", "medium", "form container"
    elif cat in ("input", "select", "textarea"):
        if role in ("searchbox", "combobox") or _has(name, _FILTER_HINTS) \
                or (fp.landmark or "") == "search":
            label, confidence, why = "filter", "medium", "search/filter control"
        else:
            label, confidence, why = "form_input", "high", "form field"
    elif cat == "link":
        if (fp.landmark or "") == "navigation":
            label, confidence, why = "navigation", "high", "link in nav"
        else:
            label, confidence, why = "navigation", "medium", "content link"
    elif cat == "button":
        if safety == "BLOCK":
            label, confidence, why = "destructive", "high", "destructive label"
        elif _has(name, _PRIMARY_HINTS):
            label, confidence, why = "primary_action", "high", "primary verb"
        elif safety == "CAUTION":
            label, confidence, why = "secondary_action", "medium", "state-changing"
        else:
            label, confidence, why = "secondary_action", "medium", "button"

    return SemanticLabel(
        fingerprint=fp.fingerprint, label=label, source="deterministic",
        confidence=confidence, rationale=why,
        accessible_name=fp.accessible_name, role=fp.role,
        landmark=fp.landmark, category=cat, page_url=page_url,
    )


def _stats(labels: list[SemanticLabel]) -> dict[str, int]:
    stats: dict[str, int] = {"total": len(labels)}
    for lab in labels:
        stats[lab.label] = stats.get(lab.label, 0) + 1
    stats["llm_refined"] = sum(1 for lab in labels if lab.source == "llm")
    return stats


def classify_analysis(analysis: Analysis) -> Semantics:
    labels: list[SemanticLabel] = []
    for pa in analysis.pages:
        for fp in pa.fingerprints:
            labels.append(classify_fingerprint(fp, pa.url))
    return Semantics(
        schema_version=SCHEMA_VERSION, engine_version=__version__,
        generated_at=datetime.now(timezone.utc).isoformat(),
        source_crawl_id=analysis.source_crawl_id, start_url=analysis.start_url,
        provider="deterministic", stats=_stats(labels), labels=labels,
    )


# --- optional LLM refinement (quarantined; off by default) ------------------

class Provider(Protocol):
    name: str
    def refine(self, items: list[dict]) -> dict[str, dict]:
        """items → {fingerprint: {label, confidence, rationale}}."""
        ...


class MockProvider:
    """Deterministic, offline, zero-token stand-in — exercises the refinement
    plumbing in tests/demos without any AI. Not a real classifier."""

    name = "mock"

    def refine(self, items: list[dict]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for it in items:
            # A visible, deterministic transformation so refinement is testable:
            # promote a table-row "View"/"Open" link from navigation to a
            # secondary_action (a row action), and mark everything high-confidence.
            label = it["current_label"]
            name = _norm(it.get("accessible_name"))
            if it.get("category") == "link" and name in ("view", "open", "details"):
                label = "secondary_action"
            out[it["fingerprint"]] = {
                "label": label, "confidence": "high", "rationale": "mock-refined",
            }
        return out


def get_provider(name: str, model: Optional[str] = None) -> Optional[Provider]:
    name = (name or "none").lower()
    if name in ("none", ""):
        return None
    if name == "mock":
        return MockProvider()
    if name == "anthropic":
        return _AnthropicProvider(model or "claude-sonnet-4-5")
    if name == "openai":
        return _OpenAIProvider(model or "gpt-4o-mini")
    raise SystemExit(f"[ERROR] Unknown provider: {name}")


_INSTRUCTION = (
    "You label web UI elements by their semantic role. For each element choose "
    "exactly one label from: " + ", ".join(LABELS) + ". Consider its role, "
    "accessible name, landmark and category. Return ONLY a JSON array of "
    '{"fingerprint","label","confidence"(high|medium|low),"rationale"(short)}.'
)


class _AnthropicProvider:
    """Real provider. Requires `pip install ui-discovery[semantic]` and the
    provider's API key in the environment (read by the SDK itself, not by us).
    Off unless explicitly selected."""

    def __init__(self, model: str):
        try:
            import anthropic  # lazy — the ONLY place an AI SDK is imported
        except ImportError as exc:
            raise SystemExit(
                "The 'anthropic' package is not installed. Install the optional "
                "extra: pip install ui-discovery[semantic]"
            ) from exc
        self._anthropic = anthropic
        self._client = anthropic.Anthropic()  # key comes from the environment via the SDK
        self.model = model
        self.name = f"anthropic:{model}"

    def refine(self, items: list[dict]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for batch in (items[i:i + 40] for i in range(0, len(items), 40)):
            payload = [
                {k: it.get(k) for k in
                 ("fingerprint", "category", "role", "accessible_name",
                  "landmark", "current_label")}
                for it in batch
            ]
            try:
                msg = self._client.messages.create(
                    model=self.model, max_tokens=2000,
                    messages=[{"role": "user",
                               "content": _INSTRUCTION + "\n\n" + json.dumps(payload)}],
                )
                text = "".join(getattr(b, "text", "") for b in msg.content)
                for row in json.loads(text[text.find("["): text.rfind("]") + 1]):
                    if row.get("label") in LABELS:
                        out[row["fingerprint"]] = {
                            "label": row["label"],
                            "confidence": row.get("confidence", "medium"),
                            "rationale": row.get("rationale"),
                        }
            except Exception as exc:  # keep deterministic labels on any failure
                print(f"[WARN] LLM refine batch failed, keeping deterministic: {exc}",
                      file=sys.stderr)
        return out


class _OpenAIProvider:
    def __init__(self, model: str):
        try:
            import openai  # lazy
        except ImportError as exc:
            raise SystemExit(
                "The 'openai' package is not installed. Install the optional "
                "extra: pip install ui-discovery[semantic]"
            ) from exc
        self._client = openai.OpenAI()  # key comes from the environment via the SDK
        self.model = model
        self.name = f"openai:{model}"

    def refine(self, items: list[dict]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for batch in (items[i:i + 40] for i in range(0, len(items), 40)):
            payload = [
                {k: it.get(k) for k in
                 ("fingerprint", "category", "role", "accessible_name",
                  "landmark", "current_label")}
                for it in batch
            ]
            try:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user",
                               "content": _INSTRUCTION + "\n\n" + json.dumps(payload)}],
                )
                text = resp.choices[0].message.content or ""
                for row in json.loads(text[text.find("["): text.rfind("]") + 1]):
                    if row.get("label") in LABELS:
                        out[row["fingerprint"]] = {
                            "label": row["label"],
                            "confidence": row.get("confidence", "medium"),
                            "rationale": row.get("rationale"),
                        }
            except Exception as exc:
                print(f"[WARN] LLM refine batch failed, keeping deterministic: {exc}",
                      file=sys.stderr)
        return out


def refine_semantics(semantics: Semantics, provider: Provider) -> Semantics:
    items = [
        {"fingerprint": lab.fingerprint, "category": lab.category,
         "role": lab.role, "accessible_name": lab.accessible_name,
         "landmark": lab.landmark, "current_label": lab.label}
        for lab in semantics.labels
    ]
    result = provider.refine(items)
    new_labels: list[SemanticLabel] = []
    for lab in semantics.labels:
        r = result.get(lab.fingerprint)
        if r and r.get("label") in LABELS:
            new_labels.append(lab.model_copy(update={
                "label": r["label"], "source": "llm",
                "confidence": r.get("confidence", "medium"),
                "rationale": r.get("rationale"),
            }))
        else:
            new_labels.append(lab)
    semantics.labels = new_labels
    semantics.provider = provider.name
    semantics.stats = _stats(new_labels)
    return semantics


# --- CLI --------------------------------------------------------------------

def _resolve_analysis_json(target: str) -> Path:
    p = Path(target)
    if p.is_dir():
        p = p / "analysis.json"
    if not p.exists():
        raise FileNotFoundError(f"No analysis.json found at {target}")
    return p


def main(argv: list[str] | None = None) -> int:
    import argparse

    from .reports import write_semantics

    parser = argparse.ArgumentParser(
        prog="ui_discovery.semantic",
        description="V5.1 semantic classification (deterministic; optional LLM refine).",
    )
    parser.add_argument("target", help="analysis.json or output/<slug>/ directory.")
    parser.add_argument("--provider", default="none",
                        choices=["none", "mock", "anthropic", "openai"],
                        help="LLM refine provider (default: none = deterministic only).")
    parser.add_argument("--model", default=None, help="Model id for the provider.")
    args = parser.parse_args(argv)

    try:
        analysis_json = _resolve_analysis_json(args.target)
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    analysis = Analysis.model_validate(
        json.loads(analysis_json.read_text(encoding="utf-8"))
    )
    print(f"[INFO] Classifying {sum(len(p.fingerprints) for p in analysis.pages)} "
          f"elements (deterministic)")
    semantics = classify_analysis(analysis)

    provider = get_provider(args.provider, args.model)
    if provider is not None:
        print(f"[INFO] Refining with provider: {provider.name}")
        semantics = refine_semantics(semantics, provider)

    paths = write_semantics(semantics, str(analysis_json.parent))
    counts = {k: v for k, v in semantics.stats.items()
              if k not in ("total", "llm_refined")}
    print(f"[INFO] Provider: {semantics.provider} · "
          f"refined: {semantics.stats.get('llm_refined', 0)}")
    print(f"[INFO] Labels: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    print(f"[INFO] Wrote {paths['json']} / {paths['markdown']} / {paths['html']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
