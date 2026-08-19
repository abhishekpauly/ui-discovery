"""Pydantic models — the canonical, versioned representation of a page.

Design notes for future phases:
  * `schema_version` is stamped on every write so snapshots stay comparable.
  * Every Element carries a *generous* identity signal set (role, accessible
    name, dom_path, sibling_ordinal, landmark, attributes, geometry) so that
    stable fingerprints can be COMPUTED later (V2) and change-analysis run
    (V5) without re-crawling. We deliberately do not compute a fingerprint in
    V0 — we only make sure the raw signal is captured and not lost.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class Geometry(BaseModel):
    x: float
    y: float
    width: float
    height: float


class Element(BaseModel):
    # What kind of thing this is, from a browser/standards point of view.
    category: str  # button|link|input|select|textarea|form|image|table|dialog|nav
    tag: str

    # Accessibility / semantic signal (deterministic approximation of the
    # browser's accessible role + name; the full ARIA tree is stored on Page).
    role: Optional[str] = None
    accessible_name: Optional[str] = None
    accessible_name_source: Optional[str] = None

    text: Optional[str] = None
    visible: bool = True
    enabled: bool = True

    # Identity signals (see module docstring).
    bounding_box: Optional[Geometry] = None
    attributes: dict[str, str] = Field(default_factory=dict)
    dom_path: str = ""
    sibling_ordinal: int = 0
    landmark: Optional[str] = None

    source: str = "runtime"


class Heading(BaseModel):
    level: int
    text: str
    dom_path: str = ""


class Page(BaseModel):
    schema_version: str
    engine_version: str
    extracted_at: str  # ISO-8601 UTC

    requested_url: str
    final_url: str
    title: str
    viewport: dict[str, int] = Field(default_factory=dict)

    # Which readiness signals fired, and how long they took — so a reader can
    # judge whether the snapshot captured a settled page.
    readiness: dict[str, Any] = Field(default_factory=dict)

    counts: dict[str, int] = Field(default_factory=dict)
    headings: list[Heading] = Field(default_factory=list)
    elements: list[Element] = Field(default_factory=list)

    # The browser's own ARIA snapshot (YAML) — ground-truth-ish a11y tree,
    # kept alongside the deterministic per-element pass rather than instead of.
    accessibility_tree: Optional[str] = None

    screenshot_path: Optional[str] = None


# --- V1: crawl-level models -------------------------------------------------


class CrawlConfig(BaseModel):
    start_url: str
    max_pages: int
    max_depth: int
    strategy: str  # e.g. "same-domain"
    dedupe_queries: bool = False  # H1: noise query params collapsed?
    hash_routes: bool = False  # H1: `#/route` fragments treated as pages?


class CrawlStats(BaseModel):
    pages_crawled: int
    pages_failed: int
    unique_urls: int
    links_discovered: int
    runtime_seconds: float


class PageNode(BaseModel):
    """A crawled page plus its position in the crawl (depth + outgoing links).

    The single-page `Page` model is embedded unchanged — V1 adds crawl context
    around it rather than modifying it.
    """

    url: str
    depth: Optional[int] = None
    out_links: list[str] = Field(default_factory=list)
    page: Page


class Crawl(BaseModel):
    schema_version: str
    engine_version: str
    crawl_id: str
    started_at: str
    finished_at: str
    config: CrawlConfig
    stats: CrawlStats
    navigation: list[dict[str, str]] = Field(default_factory=list)  # {"from","to"}
    pages: list[PageNode] = Field(default_factory=list)


# --- V2: analysis models ----------------------------------------------------


class ElementFingerprint(BaseModel):
    """A stable identity for one element on one page.

    `fingerprint` identifies *this element on this page* and is designed to
    survive CSS refactors and generated-id churn — so V5 can diff the same page
    across two crawls. `component_signature` is a coarser *shape* hash (text /
    instance indices removed) used to group repeated/shared components.
    """

    fingerprint: str
    component_signature: str
    strategy: str  # data-testid | id | structural
    category: str
    role: Optional[str] = None
    accessible_name: Optional[str] = None
    landmark: Optional[str] = None
    dom_path: str = ""


class Region(BaseModel):
    """A UI region on a page, inferred from accessibility landmarks — never
    assumed to exist."""

    type: str  # banner|navigation|main|contentinfo|complementary|form|dialog|unlabeled
    element_count: int
    categories: dict[str, int] = Field(default_factory=dict)


class Component(BaseModel):
    """A recurring UI structure.

    kind="shared": the same control recurs across multiple pages (global chrome
      such as the primary nav / header / footer).
    kind="repeated": the same shape repeats within pages (e.g. table-row
      actions, list items).
    """

    component_id: str
    kind: str
    signature: str
    role: Optional[str] = None
    label: Optional[str] = None
    landmark: Optional[str] = None
    category: str = ""
    page_count: int = 0
    instance_count: int = 0
    example_pages: list[str] = Field(default_factory=list)


class NavigationMenu(BaseModel):
    label: Optional[str] = None  # aria-label of the nav
    is_breadcrumb: bool = False
    items: list[str] = Field(default_factory=list)
    page_count: int = 1
    example_pages: list[str] = Field(default_factory=list)


class PageAnalysis(BaseModel):
    url: str
    title: str = ""
    depth: Optional[int] = None
    regions: list[Region] = Field(default_factory=list)
    fingerprints: list[ElementFingerprint] = Field(default_factory=list)


class Analysis(BaseModel):
    schema_version: str
    engine_version: str
    analyzed_at: str
    source_crawl_id: str
    start_url: str
    stats: dict[str, int] = Field(default_factory=dict)
    pages: list[PageAnalysis] = Field(default_factory=list)
    components: list[Component] = Field(default_factory=list)
    navigations: list[NavigationMenu] = Field(default_factory=list)


# --- V3: interaction + network models ---------------------------------------


class StateSignature(BaseModel):
    """A cheap, deterministic fingerprint of page state, used to detect what an
    interaction changed."""

    url: str
    visible_interactive: int
    visible_dialogs: int
    expanded: int
    text_len: int = 0
    content_hash: str = ""


class Interaction(BaseModel):
    target: Optional[str] = None
    role: Optional[str] = None
    category: str = ""
    interaction_type: str = "other"
    dom_path: str = ""

    # Safety decision (deterministic; see safety.py). `executed` is only ever
    # True when the type is on the allow-list AND the label classifies SAFE.
    safety_label: str = "SAFE"
    executed: bool = False
    skipped_reason: Optional[str] = None

    before: Optional[StateSignature] = None
    after: Optional[StateSignature] = None
    route_changed: Optional[bool] = None
    dom_changed: Optional[bool] = None
    dialog_opened: Optional[bool] = None
    expanded_changed: Optional[bool] = None
    reverted: Optional[bool] = None
    error: Optional[str] = None


class NetworkRequest(BaseModel):
    method: str
    url: str  # query values for sensitive keys are redacted
    resource_type: str
    status: Optional[int] = None
    is_api: bool = False
    is_graphql: bool = False
    endpoint_pattern: str = ""
    duration_ms: Optional[float] = None


class InteractionProbe(BaseModel):
    schema_version: str
    engine_version: str
    probed_at: str
    url: str
    final_url: str
    title: str = ""
    config: dict[str, Any] = Field(default_factory=dict)
    stats: dict[str, int] = Field(default_factory=dict)
    interactions: list[Interaction] = Field(default_factory=list)
    network: list[NetworkRequest] = Field(default_factory=list)


# --- V5: semantic layer (optional; deterministic by default) ----------------


class SemanticLabel(BaseModel):
    """A semantic role for one element, keyed by its fingerprint.

    `source` is "deterministic" (rule-based, zero tokens — the default) or "llm"
    (optional refinement, only when a provider is explicitly configured). This
    is a layer *on top of* the analysis; it never mutates the raw model.
    """

    fingerprint: str
    label: str  # primary_action|secondary_action|navigation|filter|data_display|destructive|form_input|informational
    source: str = "deterministic"
    confidence: str = "medium"  # high|medium|low
    rationale: Optional[str] = None
    # echoed for readability
    accessible_name: Optional[str] = None
    role: Optional[str] = None
    landmark: Optional[str] = None
    category: str = ""
    page_url: Optional[str] = None


class Semantics(BaseModel):
    schema_version: str
    engine_version: str
    generated_at: str
    source_crawl_id: Optional[str] = None
    start_url: Optional[str] = None
    provider: str = "deterministic"  # deterministic | mock | anthropic:<model> | openai:<model>
    stats: dict[str, int] = Field(default_factory=dict)
    labels: list[SemanticLabel] = Field(default_factory=list)


# --- V5.2: documentation generation -----------------------------------------


class DocPage(BaseModel):
    url: str
    title: str = ""
    depth: Optional[int] = None
    purpose: str = ""
    purpose_source: str = "deterministic"  # deterministic | llm
    regions: list[str] = Field(default_factory=list)
    controls: dict[str, list[str]] = Field(default_factory=dict)  # label -> names
    links: list[str] = Field(default_factory=list)
    screenshot: Optional[str] = None


class Documentation(BaseModel):
    schema_version: str
    engine_version: str
    generated_at: str
    source_crawl_id: Optional[str] = None
    start_url: Optional[str] = None
    provider: str = "deterministic"
    overview: str = ""
    overview_source: str = "deterministic"
    inventory: dict[str, int] = Field(default_factory=dict)
    global_nav: list[str] = Field(default_factory=list)
    shared_components: list[str] = Field(default_factory=list)
    pages: list[DocPage] = Field(default_factory=list)


# --- V5.3: QA / test-scenario generation ------------------------------------


class TestStep(BaseModel):
    action: str  # navigate|assert_title|assert_visible|click|assert_url|fill|guard_skip
    target: Optional[str] = None  # accessible name or URL
    role: Optional[str] = None
    value: Optional[str] = None
    note: Optional[str] = None


class TestScenario(BaseModel):
    id: str
    title: str
    page_url: str
    type: str  # smoke|navigation|form|interaction|destructive_guard
    priority: str = "P2"
    automatable: bool = True
    source: str = "deterministic"
    steps: list[TestStep] = Field(default_factory=list)
    expected: str = ""
    notes: Optional[str] = None


class QAPlan(BaseModel):
    schema_version: str
    engine_version: str
    generated_at: str
    source_crawl_id: Optional[str] = None
    start_url: Optional[str] = None
    provider: str = "deterministic"
    strategy: str = ""
    strategy_source: str = "deterministic"
    language: str = "py"  # generated skeleton language: py | ts
    stats: dict[str, int] = Field(default_factory=dict)
    scenarios: list[TestScenario] = Field(default_factory=list)
