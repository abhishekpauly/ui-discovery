# Runbook — how to run the crawler

Step-by-step, from a fresh machine to a finished capture. Every command is
copy-pasteable into **PowerShell** on Windows.

If you only remember one thing: **`cd` into the folder, activate the venv,
run one command.**

---

## 0. One-time setup

You only ever do this once per machine.

```powershell
cd C:\Users\abhip\projects\ui-discovery\ui-discovery
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m playwright install chromium
```

If line 3 fails with a red *"running scripts is disabled"*, run this once and
retry that line:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Check it worked:

```powershell
pytest -q
```

You want `412 passed, 7 skipped`. If you see that, the engine is healthy.

---

## 1. Every session

Two lines, every time you open a new terminal:

```powershell
cd C:\Users\abhip\projects\ui-discovery\ui-discovery
.\.venv\Scripts\Activate.ps1
```

Your prompt should now start with `(.venv)`. If it doesn't, nothing below
will work.

---

## 2. Crawl a public site

Nothing to configure. A browser window opens and you can watch it work.

```powershell
python -m ui_discovery.crawl https://demo.playwright.dev/todomvc --max-depth 2
```

Results land in `Downloads\<product>\`. Open `summary.md` first.

---

## 3. Crawl a site behind a login

### 3a. Capture the session (once, by hand)

```powershell
python -m ui_discovery.login https://portal.example.com/platform/dashboard --output session.json
```

A browser opens. **Log in normally.** Then come back to the terminal and
press **Enter**. It prints `Saved session to session.json`.

> Do this in a terminal you can see. The prompt is a real keypress — it
> cannot be automated, and if it hits end-of-input it saves an *empty*
> session that silently captures login pages.

`session.json` is a credential. It is gitignored; don't paste it anywhere.

### 3b. Crawl as the logged-in user

```powershell
python -m ui_discovery.crawl https://portal.example.com/platform/dashboard `
    --auth-state session.json --max-depth 1
```

If the session has expired you get a loud error and the command to re-capture
it. You will not get a quiet folder full of login screens.

---

## 4. The recommended way: a scope config

Instead of remembering flags, write the run down once. It is also the record
of what you were allowed to crawl.

```powershell
python -m ui_discovery.intake
```

Answer the prompts (Enter accepts every default). It writes `scope.yaml`.
Then:

```powershell
python -m ui_discovery.crawl --config scope.yaml
```

Check a config before using it:

```powershell
python -m ui_discovery.intake --check scope.yaml
```

A ready-made config for the Acme portal is in
`examples/websocket-spa.scope.yaml` — copy it and change `start_url`.

---

## 5. Everything in one command

Crawl **and** analyze, label, document and generate test scenarios:

```powershell
python -m ui_discovery.pipeline --config scope.yaml
```

Add `--probe` to also click the safe controls and record the API calls behind
them. Skip stages you don't want: `--skip docgen --skip qagen`.

---

## 6. What you get

Captures go to your **Downloads** folder, one folder per product — they are
deliverables, not build output, and it keeps screenshots out of the repo.

```
Downloads\Acme-Portal\
  summary.md  urls.txt  elements.csv  endpoints.md     <- the whole capture
  crawl.json  report.html  screenshots\
  RAG\                                                 <- one folder per module
    summary.md  urls.txt  elements.csv  endpoints.md
    screenshots\
  Knowledge-Hub\
  App-Builder\
  general\                                             <- screens in no module
```

Each module folder is self-contained — the same files, scoped to that
module's screens, with its own screenshots. It is the thing you hand to the
team that owns that module. `crawl.json` is never split: it is the canonical
model, and a partial one would be a different artifact wearing the same name.

Define modules in the config:

```yaml
modules:
  - name: RAG
    start_url: https://portal.example.com/platform/rag
  - name: Knowledge Hub
    start_url: https://portal.example.com/platform/knowledge-store
```

Pages are assigned by URL path, longest match wins. With no modules
configured you get one flat capture, exactly as before. Use `--output <dir>`
to write somewhere else.

Files at either level:

| File | Open this when you want… |
| --- | --- |
| `summary.md` | **Start here.** Screen count, elements per screen, file guide |
| `urls.txt` | Every screen captured, one URL per line |
| `elements.csv` | Every UI element found, with its **UI type** (slider, tab, file-upload…) — open in Excel, filter, pivot |
| `endpoints.md` | The API surface behind the UI *(needs `--probe`)* |
| `screenshots/` | One full-page screenshot per screen |
| `report.html` | The readable crawl report |
| `crawl.json` | The canonical model everything else derives from |

With the pipeline you also get `analysis.*`, `semantics.*`, `documentation.*`,
`qa.*` and `generated_tests.py`.

---

## 7. Comparing two runs

To see what changed between releases, keep both runs and diff them:

```powershell
python -m ui_discovery.crawl --config scope.yaml --output output\2026-08-19
python -m ui_discovery.analyze output\2026-08-19\<product>

# ...after the next release...
python -m ui_discovery.crawl --config scope.yaml --output output\2026-08-26
python -m ui_discovery.analyze output\2026-08-26\<product>

python -m ui_discovery.diff output\2026-08-19\<product> output\2026-08-26\<product>
```

Or set `outputs.keep_history: true` in the config and it dates the folders
for you. Without that, **a re-run overwrites the previous capture** and there
is nothing left to compare.

---

## Troubleshooting

**I don't see a browser.**
You should — headed is the default. If you passed `--headless`, drop it. If
the window flashes past, the crawl was simply fast; add `--max-concurrency 1`
to watch one page at a time.

**"No module named ui_discovery"**
The venv isn't active. Your prompt must show `(.venv)`. Re-run step 1.

**Everything says "Session appears REJECTED"**
The saved session expired. Re-run step 3a.

**A capture looks thin — far fewer elements than the site has.**
Apps that hold a websocket or SSE stream open never reach "network idle", so
the crawler has only the DOM to go on. It **detects that automatically** and
waits considerably longer; `readiness.held_open_connection` in `crawl.json`
says whether it applied.

If a capture still looks thin, compare `elements_count` in `summary.md`
across two runs of an unchanged site. If it moves, force a fixed settle
window on top:

```yaml
adapters:
  - name: extra_wait
    options: { ms: 4000 }
```

**Some screens are missing from the capture.**

Check `summary.md` first — if it says *"This capture is incomplete"*, the page
budget ran out and the missing URLs are listed at the bottom. Raise
`--max-pages`.

Otherwise the screens were never *discovered*, and there are three reasons:

1. **They're deeper than you crawled.** `--max-depth 1` only follows one hop.
   A route linked from a section page is at depth 2. Try `--max-depth 3`.
2. **They're behind a collapsed menu.** The crawler expands navigation
   automatically before reading links, so this is usually handled.
3. **The nav isn't marked up as links at all.** Some apps build a sidebar
   from plain `<div>`s with click handlers — no anchor, no button, no ARIA
   role. Link-following cannot see those, and neither can a screen reader
   (it's an accessibility defect in the app). `summary.md` will say *"There
   may be more screens"* when it spots them. Add `--deep-nav`:

```powershell
python -m ui_discovery.crawl <url> --no-deep-nav   # to turn it OFF
```

   This is **on by default** — it clicks elements that only a pointer cursor
   identifies as clickable and records where they go. Labels are still
   safety-checked, so a "Delete workspace" item is refused. Pass
   `--no-deep-nav` for a faster, link-following-only capture.
4. **Nothing links to them.** Portals with a *contextual* sidebar only render
   the current section's links, so whole areas can be islands. `--deep-nav`
   usually reaches these too; if not, seed them:

```powershell
python -m ui_discovery.crawl <url> --seed https://portal.example.com/reports `
    --seed https://portal.example.com/settings
```

Better, record them in the config so the run is repeatable:

```yaml
modules:
  - name: knowledge-store
    start_url: https://portal.example.com/knowledge-store
  - name: datasets
    start_url: https://portal.example.com/datasets
```

**It's hammering a shared environment.**
```powershell
python -m ui_discovery.crawl <url> --max-requests-per-minute 60 --max-concurrency 2
```

**Where did my last run go?**
Your `Downloads\<Product>\` folder — named from `target` /
`outputs.run_label` in the config, or from the URL if you didn't use one.
Override with `--output <dir>`, or `outputs.dir` in the config.
