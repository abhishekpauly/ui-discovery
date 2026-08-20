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

    # H3 provenance — where in the document tree this element actually lives.
    #
    # `shadow_depth` counts open shadow boundaries crossed (0 = light DOM);
    # `dom_path` marks each boundary with " >>> ", which Playwright resolves.
    #
    # `frame` is set only for elements found inside a same-origin iframe. For
    # those, `dom_path` is relative to **that frame**, not the page — selectors
    # do not cross frame boundaries — so acting on one means entering the frame
    # first via `frame_path` (the host-page selector for the iframe element).
    shadow_depth: int = 0
    frame: Optional[str] = None
    frame_path: Optional[str] = None

    source: str = "runtime"


class Heading(BaseModel):
    level: int
    text: str
    dom_path: str = ""
    shadow_depth: int = 0
    frame: Optional[str] = None


class AuthCheck(BaseModel):
    """Whether a page looks like a logged-out / login page.

    This is a statement about the *page*, not about the session: a login page
    is the expected result of crawling without credentials. It only becomes an
    error when a session was supplied and we landed here anyway — see
    `CrawlStats.auth_expired`.
    """

    looks_logged_out: bool = False
    # A settled page with no headings and no interactive elements at all. Not
    # a login page — an app that rendered nothing, which is what some SPAs do
    # when their token is rejected. Tracked separately because the evidence is
    # different, but it fails a capture just as thoroughly, and more quietly.
    looks_empty: bool = False
    signal: Optional[str] = None    # which rule fired
    evidence: Optional[str] = None  # the matching text/url, for the report


class FrameInfo(BaseModel):
    """One iframe seen on the page, and whether we entered it.

    Cross-origin frames are recorded but **not** traversed. That is a scoping
    decision, not a technical limit — Playwright can read them — because
    third-party embedded content is outside the product under test.
    """

    key: str  # name/id if present, else the frame URL
    url: str = ""
    dom_path: str = ""  # host-page selector for the <iframe> element
    title: Optional[str] = None
    same_origin: bool = False
    traversed: bool = False
    reason: Optional[str] = None  # why it was not traversed
    element_count: int = 0


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

    # H3: every iframe seen, and whether its contents were merged above.
    frames: list[FrameInfo] = Field(default_factory=list)

    # H4: does this page look like a login / logged-out page?
    auth: Optional[AuthCheck] = None

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
    probe: bool = False  # H2: safe interaction/network probe per page?
    auth_used: bool = False  # H4: was a saved session supplied?
    # H5/R2/S1: the scope that produced this crawl, so a snapshot records what
    # was in and out of scope rather than leaving it to the operator's memory.
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    config_file: Optional[str] = None
    deep_nav: bool = False
    # Clickable-but-unmarked elements seen across the crawl. Non-zero
    # means routes may exist that link-following cannot reach.
    unmarked_clickables: int = 0
    capabilities: dict[str, bool] = Field(default_factory=dict)


class CrawlStats(BaseModel):
    pages_crawled: int
    pages_failed: int
    unique_urls: int
    links_discovered: int
    runtime_seconds: float
    # H4: pages that looked logged-out or rendered nothing, and whether that
    # means the supplied session was rejected (only meaningful when a session
    # was actually used).
    pages_logged_out: int = 0
    pages_empty: int = 0
    auth_expired: bool = False


class PageNode(BaseModel):
    """A crawled page plus its position in the crawl (depth + outgoing links).

    The single-page `Page` model is embedded unchanged — V1 adds crawl context
    around it rather than modifying it.
    """

    url: str
    depth: Optional[int] = None
    out_links: list[str] = Field(default_factory=list)
    page: Page
    # H2: per-page interaction/network probe, present only when the crawl ran
    # with --probe. Additive — a crawl without it is unchanged.
    probe: Optional["InteractionProbe"] = None


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


# --- C1: change diff between two snapshots ----------------------------------


class ElementChange(BaseModel):
    """One element-level change on a page.

    `kind="renamed"` is the payoff of fingerprinting: the *same* control (same
    structural identity) carrying a different accessible name. It is reported
    separately from an add + a remove because it is a different fact about the
    product — a label changed, not a control appeared.
    """

    page_url: str
    kind: str  # added | removed | renamed
    fingerprint: str
    category: str = ""
    role: Optional[str] = None
    accessible_name: Optional[str] = None
    previous_name: Optional[str] = None  # renamed only
    landmark: Optional[str] = None
    match: Optional[str] = None  # how a rename was matched: fingerprint | structural


class PageChange(BaseModel):
    url: str
    kind: str  # added | removed | changed
    title: str = ""
    previous_title: Optional[str] = None
    elements_added: int = 0
    elements_removed: int = 0
    elements_renamed: int = 0


class ComponentChange(BaseModel):
    kind: str  # added | removed
    signature: str
    component_id: str = ""
    component_kind: str = ""  # shared | repeated
    label: Optional[str] = None
    category: str = ""
    role: Optional[str] = None
    page_count: int = 0


class DiffSide(BaseModel):
    """Provenance of one side of the comparison."""

    source_crawl_id: str = ""
    analyzed_at: str = ""
    start_url: str = ""
    page_count: int = 0
    element_count: int = 0


class Diff(BaseModel):
    schema_version: str
    engine_version: str
    generated_at: str
    old: DiffSide
    new: DiffSide
    stats: dict[str, int] = Field(default_factory=dict)
    pages: list[PageChange] = Field(default_factory=list)
    elements: list[ElementChange] = Field(default_factory=list)
    components: list[ComponentChange] = Field(default_factory=list)
    # V5.4: a readable summary of the above. Deterministic by default; an
    # optional LLM rewrites the prose only. The structured fields above
    # remain the source of truth and are never derived from the narrative.
    narrative: str = ""
    narrative_source: str = "deterministic"


# --- V4: source correlation --------------------------------------------------


class SourceRef(BaseModel):
    """A location in the repo. Every claim V4 makes points at one of these, so
    a reader can go and check it."""

    path: str  # repo-relative, forward slashes
    line: int = 0
    snippet: str = ""


class SourceComponent(BaseModel):
    name: str
    kind: str = "component"  # component | page | layout
    ref: SourceRef
    # Literal strings found in the component: labels, aria-labels, test ids.
    labels: list[str] = Field(default_factory=list)
    test_ids: list[str] = Field(default_factory=list)


class SourceRoute(BaseModel):
    path: str  # the route pattern as written, e.g. /orders/:id
    component: Optional[str] = None
    ref: Optional[SourceRef] = None


class SourceEndpoint(BaseModel):
    method: str = "GET"
    url: str  # as written in source; may contain template placeholders
    pattern: str = ""  # normalized, comparable to observed traffic
    ref: Optional[SourceRef] = None


class SourceIndex(BaseModel):
    schema_version: str
    engine_version: str
    indexed_at: str
    repo_path: str
    stats: dict[str, int] = Field(default_factory=dict)
    components: list[SourceComponent] = Field(default_factory=list)
    routes: list[SourceRoute] = Field(default_factory=list)
    endpoints: list[SourceEndpoint] = Field(default_factory=list)


class Correlation(BaseModel):
    """One link from something observed at runtime to something in source.

    `confidence` is never omitted and never inflated: confirmed > high >
    medium > low > unknown, each with the evidence that produced it. An
    inference presented as a fact would make the whole report untrustworthy.
    """

    kind: str  # element | route | endpoint
    runtime: str  # what was observed (accessible name / url / endpoint)
    runtime_page: Optional[str] = None
    source_name: Optional[str] = None
    ref: Optional[SourceRef] = None
    confidence: str = "unknown"  # confirmed|high|medium|low|unknown
    evidence: str = ""
    alternatives: list[str] = Field(default_factory=list)


class CorrelationReport(BaseModel):
    schema_version: str
    engine_version: str
    generated_at: str
    repo_path: str
    source_crawl_id: Optional[str] = None
    stats: dict[str, int] = Field(default_factory=dict)
    correlations: list[Correlation] = Field(default_factory=list)
    unmatched_runtime: list[str] = Field(default_factory=list)
    unmatched_source: list[str] = Field(default_factory=list)


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


# `PageNode.probe` forward-references `InteractionProbe`, which is defined
# further down this module. Resolve it explicitly so the reference is bound at
# import time rather than lazily on first validation.
PageNode.model_rebuild()
