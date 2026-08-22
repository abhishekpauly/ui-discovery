"""G6 — keep people out of the captured screenshots.

`G5` cleans the model and leaves the harder half untouched. A capture of an
authenticated portal is mostly *pictures* of that portal, and a picture of a
customer list is a customer list; `F6.5` component crops and `F6.6` revealed
states then multiply the copies. A capture whose `page.json` is clean and whose
`screenshots/` folder is not has not protected anyone.

Three properties shape the implementation.

**Masked by identity, not by pixels.** The boxes come from the elements `G5`
already redacted — `Redactor.mask_targets` — so the two passes cannot disagree
about what counts as a person. Nothing here reads an image or classifies
anything; if the model was clean, the picture is.

**Painted into the page, not onto the file.** The overlay is a DOM node added
before the shutter fires, so no unmasked image is ever written, and a crop of
the page contains the crop of the overlay for free. Compositing the mask
afterwards would mean an unmasked PNG existing first, and would need the
coordinate translation for every crop done by hand.

**Only the narrowest element is covered.** `Element.text` is `textContent`, so
an email in one table cell also appears in the text of its row, its table, its
`<main>` and its `<body>` — masking every element that matched would paint the
whole page black and destroy the capture. Any candidate containing another
candidate is dropped, which leaves exactly the cells that carry the value.

Known limits, stated rather than discovered: a `position: fixed` element is
masked where it sits in the layout, which is not where a full-page screenshot
renders it; and an element inside a cross-origin frame is never in the model in
the first place, so it is not masked either.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from .redact import MaskTarget

# Painted into the page, then removed. The id is deliberately unlikely so the
# layer cannot collide with a target's own markup — and so `clear` can find it
# again without keeping a handle across a navigation.
LAYER_ID = "__ui_discovery_mask__"

# The shadow-boundary marker `extract.js` writes into every `dom_path`.
SHADOW_SEP = " >>> "

MASK_JS = r"""
(args) => {
  const paths = args.paths;
  const layerId = args.layerId;
  const sep = args.sep;

  // Resolve one `dom_path`, walking into open shadow roots at each " >>> ".
  // `querySelector` cannot cross a shadow boundary, which is exactly why
  // extract.js marks them rather than emitting one flat selector.
  const resolve = (path) => {
    let root = document;
    let el = null;
    const chunks = path.split(sep);
    for (let i = 0; i < chunks.length; i++) {
      if (!root) return null;
      try { el = root.querySelector(chunks[i]); } catch (e) { return null; }
      if (!el) return null;
      root = el.shadowRoot;
    }
    return el;
  };

  const found = [];
  let unresolved = 0;
  for (let i = 0; i < paths.length; i++) {
    const el = resolve(paths[i]);
    if (el) { found.push(el); } else { unresolved++; }
  }

  // Keep only the narrowest: drop anything that contains another candidate.
  // Without this, the ancestor chain of every match is a candidate too and the
  // page ends up entirely black.
  const keep = found.filter(
    (el) => !found.some((other) => other !== el && el.contains(other)));

  const old = document.getElementById(layerId);
  if (old) old.remove();

  const layer = document.createElement("div");
  layer.id = layerId;
  layer.setAttribute("aria-hidden", "true");
  layer.style.cssText =
    "position:absolute;left:0;top:0;width:0;height:0;margin:0;padding:0;" +
    "border:0;z-index:2147483647;pointer-events:none;";
  document.documentElement.appendChild(layer);

  // Position children against the layer's own rect rather than against the
  // document origin. A positioned ancestor or a scrolled page would otherwise
  // shift every box, and the offset is invisible until someone reads a
  // screenshot of the wrong region.
  const origin = layer.getBoundingClientRect();

  const boxes = [];
  for (let i = 0; i < keep.length; i++) {
    const r = keep[i].getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    const box = document.createElement("div");
    box.style.cssText =
      "position:absolute;background:#000;pointer-events:none;" +
      "left:" + (r.left - origin.left) + "px;" +
      "top:" + (r.top - origin.top) + "px;" +
      "width:" + r.width + "px;height:" + r.height + "px;";
    layer.appendChild(box);
    boxes.push({
      x: Math.round(r.left + window.scrollX),
      y: Math.round(r.top + window.scrollY),
      width: Math.round(r.width),
      height: Math.round(r.height)
    });
  }
  return { boxes: boxes, candidates: paths.length, unresolved: unresolved };
}
"""

CLEAR_JS = r"""
(layerId) => {
  const layer = document.getElementById(layerId);
  if (layer) layer.remove();
  return true;
}
"""


def paths_by_frame(targets: Iterable[MaskTarget]) -> dict[Optional[str], list[str]]:
    """Group mask targets by the frame their `dom_path` is relative to.

    `Element.frame` is set only for elements found inside a same-origin iframe,
    and for those the path is relative to *that* frame — running it against the
    main document would silently resolve nothing. Grouping here keeps that
    knowledge in one place instead of at each call site.
    """
    grouped: dict[Optional[str], list[str]] = {}
    for target in targets:
        if not target.dom_path:
            continue
        bucket = grouped.setdefault(target.frame, [])
        if target.dom_path not in bucket:
            bucket.append(target.dom_path)
    return grouped


def _frames_for(page, frame_url: Optional[str]) -> list[Any]:
    """The frame(s) a group of paths belongs to.

    `None` means the main frame. A frame URL that no longer matches anything —
    the frame navigated or detached between extraction and the screenshot — is
    not an error: it yields no frames, the paths go unmasked, and the caller
    counts them.
    """
    if frame_url is None:
        return [page.main_frame]
    return [f for f in page.frames if f.url == frame_url]


def _summarize(results: list[dict]) -> dict:
    boxes: list[dict] = []
    candidates = unresolved = 0
    for result in results:
        boxes.extend(result.get("boxes") or [])
        candidates += int(result.get("candidates") or 0)
        unresolved += int(result.get("unresolved") or 0)
    return {"boxes": boxes, "masked": len(boxes),
            "candidates": candidates, "unresolved": unresolved}


def _payload(paths: list[str]) -> dict:
    return {"paths": paths, "layerId": LAYER_ID, "sep": SHADOW_SEP}


def apply_mask(page, targets: Iterable[MaskTarget]) -> dict:
    """Paint an opaque box over every redacted element. Sync (V0 / probe).

    Never raises. A mask that could not be painted must not lose the page, but
    it must also not pass silently — the returned `unresolved` count is what
    says the picture is less covered than the model.
    """
    results = []
    for frame_url, paths in paths_by_frame(targets).items():
        for frame in _frames_for(page, frame_url):
            try:
                results.append(frame.evaluate(MASK_JS, _payload(paths)))
            except Exception:
                results.append({"boxes": [], "candidates": len(paths),
                                "unresolved": len(paths)})
    return _summarize(results)


async def apply_mask_async(page, targets: Iterable[MaskTarget]) -> dict:
    """Async twin of `apply_mask` — the crawler's path."""
    results = []
    for frame_url, paths in paths_by_frame(targets).items():
        for frame in _frames_for(page, frame_url):
            try:
                results.append(await frame.evaluate(MASK_JS, _payload(paths)))
            except Exception:
                results.append({"boxes": [], "candidates": len(paths),
                                "unresolved": len(paths)})
    return _summarize(results)


def clear_mask(page) -> None:
    """Remove the overlay. Sync.

    Called after the shutter so a later extraction does not read the mask layer
    back as page content — the probe re-extracts after every interaction.
    """
    for frame in page.frames:
        try:
            frame.evaluate(CLEAR_JS, LAYER_ID)
        except Exception:
            continue


async def clear_mask_async(page) -> None:
    """Async twin of `clear_mask`."""
    for frame in page.frames:
        try:
            await frame.evaluate(CLEAR_JS, LAYER_ID)
        except Exception:
            continue
