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

You want `274 passed`. If you see that, the engine is healthy.

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

Results land in `output\<product>\`. Open `summary.md` first.

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

One folder per product, e.g. `output\acme-builder-qa\`:

| File | Open this when you want… |
| --- | --- |
| `summary.md` | **Start here.** Screen count, elements per screen, file guide |
| `urls.txt` | Every screen captured, one URL per line |
| `elements.csv` | Every UI element found — open in Excel, filter, pivot |
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
The site is probably still rendering when we look. Add a settle window:

```yaml
adapters:
  - name: extra_wait
    options: { ms: 4000 }
```

This is required for apps that hold a websocket open (the Acme portal is
one), because they never reach "network idle". Compare `screens_count` and
`elements_count` in `summary.md` across two runs — if they move on an
unchanged site, you need this.

**It's hammering a shared environment.**
```powershell
python -m ui_discovery.crawl <url> --max-requests-per-minute 60 --max-concurrency 2
```

**Where did my last run go?**
`output\<product>\` — named from `target` / `outputs.run_label` in the config,
or from the URL if you didn't use one.
