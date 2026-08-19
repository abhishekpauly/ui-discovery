// Deterministic in-page extraction pass.
//
// One evaluate() round-trip returns a rich, framework-agnostic snapshot of the
// page: headings + an inventory of interactive/structural elements, each with
// the identity signals the UI model needs. This runs entirely against browser
// / web standards — no assumption about React/Angular/Vue/etc.
() => {
  function cssPath(el) {
    if (!(el instanceof Element)) return "";
    const path = [];
    while (el && el.nodeType === Node.ELEMENT_NODE && path.length < 40) {
      let sel = el.nodeName.toLowerCase();
      // Only take the #id shortcut when the id is actually unique on the page —
      // duplicate ids (invalid but common) would otherwise collapse distinct
      // elements onto the same path.
      if (el.id && document.querySelectorAll("#" + CSS.escape(el.id)).length === 1) {
        sel += "#" + CSS.escape(el.id);
        path.unshift(sel);
        break;
      }
      let nth = 1;
      let sib = el;
      while ((sib = sib.previousElementSibling)) {
        if (sib.nodeName === el.nodeName) nth++;
      }
      sel += ":nth-of-type(" + nth + ")";
      path.unshift(sel);
      el = el.parentElement;
    }
    return path.join(" > ");
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
    while (cur) {
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
    const al = el.getAttribute("aria-label");
    if (al && al.trim()) return [al.trim(), "aria-label"];

    const lb = el.getAttribute("aria-labelledby");
    if (lb) {
      const txt = lb.split(/\s+/)
        .map((id) => {
          const t = document.getElementById(id);
          return t ? t.textContent.replace(/\s+/g, " ").trim() : "";
        })
        .filter(Boolean)
        .join(" ");
      if (txt) return [txt, "aria-labelledby"];
    }

    const tag = el.nodeName;
    if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") {
      if (el.id) {
        const lab = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
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
  ];
  function attrsOf(el) {
    const o = {};
    for (const a of STABLE_ATTRS) {
      if (el.hasAttribute(a)) {
        let v = el.getAttribute(a);
        if (v !== null) {
          if (v.length > 300) v = v.slice(0, 300);
          o[a] = v;
        }
      }
    }
    return o;
  }

  function describe(el, category) {
    const [name, src] = accName(el);
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
  ];

  const seen = new Set();
  const elements = [];
  for (const [cat, sel] of GROUPS) {
    document.querySelectorAll(sel).forEach((el) => {
      if (seen.has(el)) return;
      seen.add(el);
      elements.push(describe(el, cat));
    });
  }

  const headings = [];
  document.querySelectorAll("h1,h2,h3,h4,h5,h6,[role=heading]").forEach((el) => {
    let level;
    if (/^H[1-6]$/.test(el.nodeName)) level = parseInt(el.nodeName[1], 10);
    else level = parseInt(el.getAttribute("aria-level") || "2", 10);
    const text = (el.textContent || "").replace(/\s+/g, " ").trim();
    if (text) headings.push({ level, text: text.slice(0, 200), dom_path: cssPath(el) });
  });

  return {
    title: document.title,
    final_url: location.href,
    viewport: { width: window.innerWidth, height: window.innerHeight },
    headings,
    elements,
  };
}
