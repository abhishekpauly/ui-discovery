// Deterministic in-page extraction pass.
//
// One evaluate() round-trip returns a rich, framework-agnostic snapshot of the
// page: headings + an inventory of interactive/structural elements, each with
// the identity signals the UI model needs. This runs entirely against browser
// / web standards — no assumption about React/Angular/Vue/etc.
() => {
  // Shadow boundaries are marked with " >>> " — Playwright's shadow-piercing
  // combinator, so the resulting path stays resolvable via query_selector
  // while still showing a reader exactly where the boundary is.
  const SHADOW_SEP = " >>> ";

  function segmentsWithin(el) {
    // Path of `el` within its own root (document or a shadow root).
    const path = [];
    const root = el.getRootNode();
    while (el && el.nodeType === Node.ELEMENT_NODE && path.length < 40) {
      let sel = el.nodeName.toLowerCase();
      // Only take the #id shortcut when the id is actually unique *within this
      // root* — duplicate ids (invalid but common, and routine across shadow
      // roots) would otherwise collapse distinct elements onto the same path.
      if (el.id && root.querySelectorAll("#" + CSS.escape(el.id)).length === 1) {
        path.unshift(sel + "#" + CSS.escape(el.id));
        break;
      }
      let nth = 1;
      let sib = el;
      while ((sib = sib.previousElementSibling)) {
        if (sib.nodeName === el.nodeName) nth++;
      }
      path.unshift(sel + ":nth-of-type(" + nth + ")");
      el = el.parentElement;
    }
    return path.join(" > ");
  }

  function cssPath(el) {
    if (!(el instanceof Element)) return "";
    // Walk out through any open shadow roots, prepending each host's path.
    const chunks = [];
    let cur = el;
    let guard = 0;
    while (cur && guard++ < 20) {
      chunks.unshift(segmentsWithin(cur));
      const root = cur.getRootNode();
      if (root instanceof ShadowRoot) {
        cur = root.host;
      } else {
        break;
      }
    }
    return chunks.join(SHADOW_SEP);
  }

  function shadowDepth(el) {
    let depth = 0;
    let root = el.getRootNode();
    let guard = 0;
    while (root instanceof ShadowRoot && guard++ < 20) {
      depth++;
      root = root.host.getRootNode();
    }
    return depth;
  }

  // Every root worth querying: the document plus every OPEN shadow root
  // reachable from it, depth-first. Closed roots are deliberately absent —
  // `element.shadowRoot` is null for them by web standards, so their contents
  // are genuinely unobservable rather than merely skipped.
  function collectRoots() {
    const roots = [document];
    for (let i = 0; i < roots.length && roots.length < 500; i++) {
      roots[i].querySelectorAll("*").forEach((el) => {
        if (el.shadowRoot) roots.push(el.shadowRoot);
      });
    }
    return roots;
  }

  function siblingOrdinal(el) {
    let n = 0;
    let sib = el;
    while ((sib = sib.previousElementSibling)) {
      if (sib.nodeName === el.nodeName) n++;
    }
    return n;
  }

  const LANDMARK_TAGS = {
    HEADER: "banner",
    NAV: "navigation",
    MAIN: "main",
    FOOTER: "contentinfo",
    ASIDE: "complementary",
    FORM: "form",
    DIALOG: "dialog",
  };
  const LANDMARK_ROLES = [
    "navigation", "main", "banner", "contentinfo", "complementary",
    "dialog", "alertdialog", "form", "search", "region",
  ];
  function landmarkOf(el) {
    let cur = el.parentElement;
    let guard = 0;
    while (cur || guard < 40) {
      if (!cur) {
        // Ran out of ancestors inside a shadow root — a landmark can wrap the
        // host from the light DOM, so continue the walk on the other side of
        // the boundary rather than reporting "no landmark".
        const root = el.getRootNode();
        if (!(root instanceof ShadowRoot)) return null;
        el = root.host;
        cur = el;
        continue;
      }
      guard++;
      const r = (cur.getAttribute && cur.getAttribute("role")) || "";
      if (r && LANDMARK_ROLES.includes(r.trim().split(/\s+/)[0])) {
        return r.trim().split(/\s+/)[0];
      }
      if (LANDMARK_TAGS[cur.nodeName]) return LANDMARK_TAGS[cur.nodeName];
      cur = cur.parentElement;
    }
    return null;
  }

  const IMPLICIT_ROLE = {
    A: "link", BUTTON: "button", NAV: "navigation", MAIN: "main",
    HEADER: "banner", FOOTER: "contentinfo", ASIDE: "complementary",
    FORM: "form", TABLE: "table", IMG: "img", SELECT: "combobox",
    TEXTAREA: "textbox", DIALOG: "dialog", UL: "list", OL: "list",
    LI: "listitem",
  };
  function roleOf(el) {
    const explicit = el.getAttribute("role");
    if (explicit) return explicit.trim().split(/\s+/)[0];
    const tag = el.nodeName;
    if (tag === "INPUT") {
      const t = (el.getAttribute("type") || "text").toLowerCase();
      const m = {
        button: "button", submit: "button", reset: "button",
        checkbox: "checkbox", radio: "radio", range: "slider",
        search: "searchbox", email: "textbox", tel: "textbox",
        url: "textbox", number: "spinbutton", text: "textbox",
        password: "textbox",
      };
      return m[t] || "textbox";
    }
    if (tag === "A") return el.hasAttribute("href") ? "link" : null;
    if (/^H[1-6]$/.test(tag)) return "heading";
    return IMPLICIT_ROLE[tag] || null;
  }

  function isVisible(el) {
    const st = getComputedStyle(el);
    if (st.display === "none") return false;
    if (st.visibility === "hidden" || st.visibility === "collapse") return false;
    if (parseFloat(st.opacity || "1") === 0) return false;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return false;
    return true;
  }

  function enabledOf(el) {
    if (el.disabled) return false;
    if ((el.getAttribute("aria-disabled") || "") === "true") return false;
    return true;
  }

  function rectOf(el) {
    const r = el.getBoundingClientRect();
    return {
      x: Math.round(r.x), y: Math.round(r.y),
      width: Math.round(r.width), height: Math.round(r.height),
    };
  }

  // Deterministic approximation of the accessible-name algorithm. Returns
  // [name, source] so a reader can see HOW the name was derived.
  function accName(el) {
    // IDREF lookups (aria-labelledby, label[for]) resolve within the element's
    // own root: ids do not cross a shadow boundary, so resolving them against
    // `document` would silently pick up an unrelated element of the same id.
    const root = el.getRootNode();

    const al = el.getAttribute("aria-label");
    if (al && al.trim()) return [al.trim(), "aria-label"];

    const lb = el.getAttribute("aria-labelledby");
    if (lb) {
      const txt = lb.split(/\s+/)
        .map((id) => {
          const t = root.getElementById ? root.getElementById(id) : null;
          return t ? t.textContent.replace(/\s+/g, " ").trim() : "";
        })
        .filter(Boolean)
        .join(" ");
      if (txt) return [txt, "aria-labelledby"];
    }

    const tag = el.nodeName;
    if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") {
      if (el.id) {
        const lab = root.querySelector('label[for="' + CSS.escape(el.id) + '"]');
        if (lab && lab.textContent.trim()) {
          return [lab.textContent.replace(/\s+/g, " ").trim(), "label[for]"];
        }
      }
      const wrap = el.closest("label");
      if (wrap && wrap.textContent.trim()) {
        return [wrap.textContent.replace(/\s+/g, " ").trim(), "wrapping-label"];
      }
      const t = (el.getAttribute("type") || "").toLowerCase();
      if (tag === "INPUT" && ["button", "submit", "reset"].includes(t) && el.value) {
        return [el.value, "value"];
      }
      const ph = el.getAttribute("placeholder");
      if (ph && ph.trim()) return [ph.trim(), "placeholder"];
    }

    if (tag === "IMG") {
      const alt = el.getAttribute("alt");
      if (alt !== null && alt.trim()) return [alt.trim(), "alt"];
    }

    const own = (el.textContent || "").replace(/\s+/g, " ").trim();
    const TEXT_NAMED = ["BUTTON", "A", "H1", "H2", "H3", "H4", "H5", "H6", "SUMMARY", "LABEL"];
    if (own && own.length <= 200 && TEXT_NAMED.includes(tag)) return [own, "text"];

    const title = el.getAttribute("title");
    if (title && title.trim()) return [title.trim(), "title"];

    if (own && own.length <= 120 && el.getAttribute("role")) return [own, "text"];
    return [null, null];
  }

  const STABLE_ATTRS = [
    "id", "name", "type", "href", "role", "placeholder", "alt", "value",
    "title", "for", "data-testid", "aria-label", "aria-labelledby",
    "aria-describedby", "aria-expanded", "aria-controls", "aria-hidden",
    "aria-disabled", "disabled",
    // interaction affordances (V3 safety classification):
    "aria-haspopup", "aria-selected", "aria-pressed", "open",
    // UI-type signals (taxonomy.py): state and behaviour that identify a
    // control when its role does not.
    "aria-roledescription", "aria-modal", "aria-sort", "aria-live",
    "aria-invalid", "aria-required", "required", "contenteditable",
    "draggable", "target", "download", "accesskey", "aria-keyshortcuts",
    "class",
  ];
  // `value` is in STABLE_ATTRS because it names a button ("Save" on an
  // <input type=submit>) and identifies a choice. On a text, email, search or
  // password field it is instead whatever a person typed — including their
  // password — and a snapshot must never carry that. Only the naming and
  // choice-shaped types keep it.
  const VALUE_ATTR_SAFE_TYPES = new Set([
    "button", "submit", "reset",
    "checkbox", "radio", "range", "number", "date", "datetime-local",
    "month", "week", "time", "color", "hidden",
  ]);

  function valueAttrAllowed(el) {
    const tag = el.nodeName.toLowerCase();
    if (tag === "textarea") return false;
    if (tag !== "input") return true;
    return VALUE_ATTR_SAFE_TYPES.has((el.getAttribute("type") || "text").toLowerCase());
  }

  function attrsOf(el) {
    const o = {};
    const keepValue = valueAttrAllowed(el);
    for (const a of STABLE_ATTRS) {
      if (a === "value" && !keepValue) continue;
      if (el.hasAttribute(a)) {
        let v = el.getAttribute(a);
        if (v !== null) {
          if (v.length > 300) v = v.slice(0, 300);
          if (a === "class") v = v.slice(0, 120);
          o[a] = v;
        }
      }
    }
    return o;
  }


  // --- relationship + state signals ----------------------------------------
  //
  // Everything below reads web standards only: DOM properties, ARIA idrefs,
  // native form association. None of it knows which framework built the page,
  // and none of it interacts.

  // Resolve a space-separated IDREF list within the element's OWN root. Ids do
  // not cross a shadow boundary, so resolving against `document` would happily
  // return an unrelated element that happens to share the id.
  function idrefs(el, attr) {
    const raw = el.getAttribute(attr);
    if (!raw) return [];
    const root = el.getRootNode();
    const out = [];
    for (const id of raw.split(/\s+/)) {
      if (!id) continue;
      const t = root.getElementById ? root.getElementById(id) : null;
      if (t) out.push(t);
    }
    return out;
  }

  function idrefText(el, attr) {
    const txt = idrefs(el, attr)
      .map((t) => (t.textContent || "").replace(/\s+/g, " ").trim())
      .filter(Boolean)
      .join(" ");
    return txt ? txt.slice(0, 300) : null;
  }

  // The label a person reads for one option-like element.
  function optionLabel(el) {
    const [name] = accName(el);
    if (name) return name;
    return (el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 120);
  }

  const MAX_OPTIONS = 50;

  // What choices does this control offer? A native <select> and the ARIA
  // composite widgets are the only things that can answer that from standard
  // markup; anything else legitimately has no options.
  const OPTION_OWNERS = [
    ["datalist", "option"],
    ["[role=listbox]", "[role=option]"],
    ["[role=radiogroup]", "[role=radio]"],
    ["[role=menu],[role=menubar]",
     "[role=menuitem],[role=menuitemcheckbox],[role=menuitemradio]"],
    ["[role=tablist]", "[role=tab]"],
  ];

  function optionsOf(el) {
    const tag = el.nodeName.toLowerCase();
    let found = null;

    if (tag === "select") {
      found = [...el.options].map((o) => ({
        label: (o.label || o.textContent || "").replace(/\s+/g, " ").trim().slice(0, 120),
        value: o.value === "" ? null : String(o.value).slice(0, 120),
        selected: !!o.selected,
        disabled: !!o.disabled,
      }));
    } else {
      for (const pair of OPTION_OWNERS) {
        if (!el.matches(pair[0])) continue;
        found = [...el.querySelectorAll(pair[1])].map((o) => ({
          label: optionLabel(o),
          value: o.getAttribute("value") || null,
          selected: o.getAttribute("aria-selected") === "true"
                 || o.getAttribute("aria-checked") === "true",
          disabled: o.getAttribute("aria-disabled") === "true" || !!o.disabled,
        }));
        break;
      }
    }

    if (!found || !found.length) return { options: [], option_count: 0 };
    // The stored list is capped; the count never is, so a reader can tell a
    // 12-option dropdown from a 4000-option one.
    return { options: found.slice(0, MAX_OPTIONS), option_count: found.length };
  }

  // Values are user data. Only the ones that describe a *choice* are recorded;
  // free text, email addresses and passwords are what a person typed and never
  // belong in a snapshot.
  const VALUE_SAFE_TYPES = new Set([
    "checkbox", "radio", "range", "number", "date", "datetime-local",
    "month", "week", "time", "color",
  ]);

  function valueOf(el, states) {
    const tag = el.nodeName.toLowerCase();
    if (tag === "select") {
      const sel = [...el.selectedOptions]
        .map((o) => (o.label || o.textContent || "").replace(/\s+/g, " ").trim());
      return sel.length ? sel.join(", ").slice(0, 200) : null;
    }
    if (tag === "input") {
      const t = (el.getAttribute("type") || "text").toLowerCase();
      if (VALUE_SAFE_TYPES.has(t)) {
        return el.value === "" ? null : String(el.value).slice(0, 120);
      }
      // The text is not recorded, but whether the field arrives pre-filled is
      // a fact about the UI rather than about the person.
      if (el.value) states.has_value = "true";
      return null;
    }
    if (tag === "textarea" && el.value) states.has_value = "true";
    return null;
  }

  // Interaction state, read from DOM properties first. Frameworks routinely
  // set `.checked` / `.required` without reflecting them to an attribute, so
  // an attribute-only read reports a checked box as unchecked.
  function statesOf(el) {
    const st = {};
    const tag = el.nodeName.toLowerCase();
    const attr = (name) => el.getAttribute(name);
    const put = (key, v) => {
      if (v !== null && v !== undefined && v !== "") st[key] = String(v);
    };

    if (tag === "input" && (el.type === "checkbox" || el.type === "radio")) {
      put("checked", el.checked);
    } else {
      put("checked", attr("aria-checked"));
    }
    if (tag === "option") put("selected", el.selected);
    else put("selected", attr("aria-selected"));

    put("expanded", attr("aria-expanded"));
    put("pressed", attr("aria-pressed"));
    put("current", attr("aria-current"));
    put("sort", attr("aria-sort"));
    put("invalid", attr("aria-invalid"));

    if ("required" in el) put("required", el.required || attr("aria-required") === "true");
    else put("required", attr("aria-required"));
    if ("readOnly" in el) put("readonly", el.readOnly || attr("aria-readonly") === "true");
    else put("readonly", attr("aria-readonly"));

    if (tag === "details") put("open", el.open);
    if (tag === "select" && el.multiple) put("multiple", true);

    // "false" is informative for a toggle, but a blanket `required: false` on
    // every field is noise. Drop the falsey ones that carry nothing.
    for (const k of ["checked", "required", "readonly", "multiple", "open"]) {
      if (st[k] === "false") delete st[k];
    }
    return st;
  }

  // The form this control belongs to. `el.form` is the native association and
  // handles `form="other-id"`, which a DOM walk would get wrong.
  function ownerForm(el) {
    const owner = ("form" in el && el.form) ? el.form : el.closest("form, [role=form]");
    return owner && owner !== el ? cssPath(owner) : null;
  }

  // The named set this control sits in: a fieldset legend, a labelled ARIA
  // group, or — for native radios, which have no container of their own — the
  // shared `name` that makes them one choice.
  function groupOf(el) {
    const aria = el.closest("[role=group],[role=radiogroup]");
    if (aria && aria !== el) {
      const [name] = accName(aria);
      if (name) return name.slice(0, 120);
    }
    const fs = el.closest("fieldset");
    if (fs) {
      const legend = fs.querySelector("legend");
      const txt = legend ? (legend.textContent || "").replace(/\s+/g, " ").trim() : "";
      if (txt) return txt.slice(0, 120);
    }
    const tag = el.nodeName.toLowerCase();
    if (tag === "input" && (el.type === "radio" || el.type === "checkbox")) {
      const n = el.getAttribute("name");
      if (n) return n.slice(0, 120);
    }
    return null;
  }

  // Columns and row count for a table/grid, so a report can say what the data
  // on a screen actually is instead of "1 table".
  function tableShape(el) {
    let cells = [...el.querySelectorAll("thead th, [role=columnheader]")];
    if (!cells.length) {
      const first = el.querySelector("tr");
      cells = first ? [...first.querySelectorAll("th")] : [];
    }
    const columns = cells
      .map((c) => (c.textContent || "").replace(/\s+/g, " ").trim())
      .filter(Boolean)
      .slice(0, 40);
    let rows = el.querySelectorAll("tbody tr").length;
    if (!rows) {
      rows = Math.max(0, el.querySelectorAll("tr").length - (columns.length ? 1 : 0));
    }
    return { columns: columns, row_count: rows };
  }

  function describe(el, category) {
    const [name, src] = accName(el);
    const states = statesOf(el);
    const opts = optionsOf(el);
    const shape = (category === "table") ? tableShape(el)
                                         : { columns: [], row_count: 0 };
    return {
      category,
      tag: el.nodeName.toLowerCase(),
      role: roleOf(el),
      accessible_name: name,
      accessible_name_source: src,
      text: (el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 200) || null,
      visible: isVisible(el),
      enabled: enabledOf(el),
      bounding_box: rectOf(el),
      attributes: attrsOf(el),
      dom_path: cssPath(el),
      sibling_ordinal: siblingOrdinal(el),
      landmark: landmarkOf(el),
      shadow_depth: shadowDepth(el),
      // What this control offers, what state it is in, and what else on the
      // page it is tied to — the facts a flat element list cannot express.
      options: opts.options,
      option_count: opts.option_count,
      states: states,
      value: valueOf(el, states),
      described_by: idrefText(el, "aria-describedby"),
      group: groupOf(el),
      owner_form: ownerForm(el),
      columns: shape.columns,
      row_count: shape.row_count,
      // Filled by the second pass below, once every captured element is known:
      // a parent is only worth recording if we captured it too.
      parent_path: "",
      controls: [],
      source: "runtime",
    };
  }

  // Category selectors, processed in order; an element is claimed by the first
  // category it matches (so <input type=submit> is a button, not an input).
  const GROUPS = [
    ["button", "button, input[type=button], input[type=submit], input[type=reset], [role=button]"],
    ["link", "a[href], [role=link]"],
    ["input", "input:not([type=button]):not([type=submit]):not([type=reset]):not([type=hidden])"],
    ["select", "select, [role=combobox], [role=listbox]"],
    ["textarea", "textarea"],
    ["form", "form, [role=form]"],
    ["image", "img, [role=img]"],
    ["table", "table, [role=table], [role=grid]"],
    ["dialog", "dialog, [role=dialog], [role=alertdialog]"],
    ["nav", "nav, [role=navigation]"],
    // Kinds the shape-based groups above miss entirely. Each is a control a
    // person would name, and each is detectable from standard markup.
    ["tab", "[role=tab], [role=tablist], [role=tabpanel]"],
    ["menu", "[role=menu], [role=menubar], [role=menuitem], [role=toolbar]"],
    ["tree", "[role=tree], [role=treeitem]"],
    ["disclosure", "details, summary, [aria-expanded]"],
    ["status", "[role=alert], [role=status], [aria-live], progress, meter, [role=progressbar]"],
    ["tooltip", "[role=tooltip]"],
    ["region", "aside, [role=complementary], [role=region][aria-label], [role=search]"],
    ["columnheader", "th, [role=columnheader], [aria-sort]"],
    ["editor", "[contenteditable=true], [contenteditable='']"],
    // Media that carries meaning. Decorative <svg> is deliberately excluded:
    // an icon with no accessible name is invisible to a screen reader too,
    // and counting hundreds of them would drown the inventory.
    ["media", "canvas, video, audio, iframe, svg[aria-label], svg[role=img], svg > title"],
  ];

  const roots = collectRoots();

  const seen = new Set();
  const elements = [];
  // Node -> its record, so the second pass can resolve a relationship to an
  // element we actually captured rather than re-deriving a path for a node
  // that is not in the inventory at all.
  const byNode = new Map();
  for (const [cat, sel] of GROUPS) {
    for (const root of roots) {
      root.querySelectorAll(sel).forEach((el) => {
        if (seen.has(el)) return;
        seen.add(el);
        const record = describe(el, cat);
        elements.push(record);
        byNode.set(el, record);
      });
    }
  }

  // Second pass: containment and ARIA control relationships.
  //
  // Both are deliberately expressed as `dom_path` references to OTHER captured
  // elements, so the whole graph survives serialization and can be rebuilt
  // later without a browser.
  for (const [node, record] of byNode) {
    // Nearest captured ancestor, crossing shadow boundaries the same way
    // landmarkOf does — a captured parent can sit on the far side of a host.
    let from = node;
    let cur = from.parentElement;
    let guard = 0;
    while (guard++ < 200) {
      if (!cur) {
        const root = from.getRootNode();
        if (!(root instanceof ShadowRoot)) break;
        from = root.host;
        cur = from;
        continue;
      }
      const hit = byNode.get(cur);
      if (hit) { record.parent_path = hit.dom_path; break; }
      cur = cur.parentElement;
    }

    // aria-controls / aria-owns: the tab that owns a panel, the button that
    // opens a dialog, the trigger that owns a menu.
    const targets = [...idrefs(node, "aria-controls"), ...idrefs(node, "aria-owns")];
    for (const t of targets) {
      const hit = byNode.get(t);
      record.controls.push(hit ? hit.dom_path : cssPath(t));
    }
  }

  const headings = [];
  for (const root of roots) {
    root.querySelectorAll("h1,h2,h3,h4,h5,h6,[role=heading]").forEach((el) => {
      let level;
      if (/^H[1-6]$/.test(el.nodeName)) level = parseInt(el.nodeName[1], 10);
      else level = parseInt(el.getAttribute("aria-level") || "2", 10);
      const text = (el.textContent || "").replace(/\s+/g, " ").trim();
      if (text) {
        headings.push({
          level, text: text.slice(0, 200), dom_path: cssPath(el),
          shadow_depth: shadowDepth(el),
        });
      }
    });
  }

  // Every iframe on the page, whether or not it is entered. Traversal is
  // decided host-side (same-origin only); this is the raw inventory so the
  // model can record what was seen but deliberately not entered.
  const frames = [];
  for (const root of roots) {
    root.querySelectorAll("iframe, frame").forEach((el) => {
      frames.push({
        src: el.getAttribute("src") || "",
        name: el.getAttribute("name") || el.getAttribute("id") || "",
        title: el.getAttribute("title") || "",
        dom_path: cssPath(el),
        visible: isVisible(el),
      });
    });
  }

  return {
    title: document.title,
    final_url: location.href,
    viewport: { width: window.innerWidth, height: window.innerHeight },
    headings,
    elements,
    frames,
    // How many roots were queried: 1 = no open shadow DOM on this page.
    roots_scanned: roots.length,
  };
}
