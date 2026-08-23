# Getting Started (Windows)

This is the **UI Discovery Engine** — a small Python command-line program. You
run it by typing commands in a **terminal**. It is *not* a browser extension and
*not* something you run inside a chat window; it drives its own invisible
Chromium browser for you.

**Mental model:** open this folder → open a terminal → type a command → look at
the files it writes into `output\`.

You can use **VS Code** (its built-in terminal is the friendliest option) or just
**PowerShell**. VS Code is only an editor with a terminal attached — it doesn't
run anything by itself.

---

## 1. Install the prerequisites (once)

- **Python 3.10+** — https://www.python.org/downloads/
  During install, **tick "Add python.exe to PATH."**
- **VS Code** (optional but recommended) — https://code.visualstudio.com/

Check Python is available — open PowerShell and run:

```powershell
python --version
```

If that errors, close and reopen the terminal, or reinstall Python with the
PATH box ticked.

---

## 2. Open the project

- **VS Code:** File → Open Folder → select `C:\Users\abhip\projects\ui-discovery`.
  Then Terminal → New Terminal (or press `` Ctrl+` ``). A prompt appears at the
  bottom — that's where you type.
- **PowerShell only:** `cd C:\Users\abhip\projects\ui-discovery`

---

## 3. One-time setup

Run these four lines in the terminal, from inside the project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m playwright install chromium
```

- Line 1 creates an isolated environment (a `.venv` folder).
- Line 2 activates it — your prompt should now start with `(.venv)`.
- Line 3 installs the engine and its dependencies.
- Line 4 downloads the browser the engine drives (~150 MB, one time).

---

## 4. Confirm it works

```powershell
pytest -q
```

You should see something like `84 passed`. This runs everything against local
test pages — **no internet required.** If this passes, the engine is healthy.

---

## 5. Run it on a real site

Each command writes into `output\<site>\`. Open the `.html` files it creates.

```powershell
# Extract ONE page  ->  output\<site>\page.json + screenshot.png
python -m ui_discovery.extract https://demo.playwright.dev/todomvc

# CRAWL a whole site  ->  output\<site>\report.html + crawl.json + screenshots\
python -m ui_discovery.crawl https://demo.playwright.dev/todomvc --max-depth 2

# ANALYZE the crawl  ->  output\<site>\analysis.html (components, regions, fingerprints)
python -m ui_discovery.analyze output\demo.playwright.dev_todomvc

# PROBE one page's behavior safely  ->  output\<site>\probe.html
python -m ui_discovery.probe https://demo.playwright.dev/todomvc
```

Useful flags: `--max-pages 25`, `--max-depth 3`, `--output .\myresults`.

---

## 6. A portal that needs a login

Log in once; the engine reuses that session. Your password never touches the
tool.

```powershell
# Opens a VISIBLE browser. Log in by hand, then come back and press Enter.
python -m ui_discovery.login https://portal.example.com/login --output session.json

# Then add --auth-state to any command:
python -m ui_discovery.crawl https://portal.example.com/ --auth-state session.json
```

`session.json` is like a password — it's already git-ignored; don't share it,
and re-run `login` when it stops working. Only crawl portals you're authorized
to test.

---

## 7. Your first run against your own product

The steps above point at a demo site. Pointing at a product you actually own is
a different exercise: the failures are quieter, and two of them will make a
capture *look* complete while missing a third of the product. Do it in this
order.

### a. Write a scope config, don't use flags

```powershell
python -m ui_discovery.intake
```

It asks questions and writes a `scope.yaml`. Flags are fine for a demo; a real
target wants a file you can re-run, diff and check into a ticket.

### b. Set the subdomain policy before anything else

If your product spans more than one host — `app.` and `admin.`, or a separate
reports host — say so:

```yaml
scope:
  subdomains: registrable-domain
```

**This is the one that fails silently.** The default compares hostnames exactly,
so without it the crawl stops at the first subdomain boundary and the report
looks like a complete picture of a smaller product. Nothing in the output will
tell you.

### c. See what the crawl would do, before it does it

```powershell
python -m ui_discovery.crawl <url> --config scope.yaml --dry-run
```

A second, no browser. It writes `map.json` and `urls.txt` and tells you how
many screens are in scope, which rule excluded the rest, and — by name — any
module your budget cannot reach. Fix the config here rather than after a
forty-minute run.

### d. Then a cheap real pass

```powershell
python -m ui_discovery.crawl <url> --config scope.yaml --profile fast --headless
```

`fast` skips clicking, screenshots and deep-nav. You are not documenting the
product yet — you are finding out how big it is and whether your scope is right,
and doing that with a full run costs you the whole run.

### e. Read three things before running it again

| Where | What it tells you |
| --- | --- |
| `summary.md` → **Not captured** | Whether the budget was too small, links are broken, or your `exclude` rules are eating things. Only `budget` is fixed by raising `--max-pages`. |
| `summary.md` → **Leaves the product** | Whether the crawl is stopping where you meant it to. |
| `run.json` → `metrics.probe_share_of_crawl_pct` | How much of the wall clock the clicking costs. This is the number to optimise against, rather than a guess. |
| `report.md` → **Reachable, but not from anywhere** | Screens that work by URL and that nothing links to — dead routes and features shipped without an entry point. |

If `Not captured` is full of `budget`, raise `--max-pages`. If it is full of
`out-of-scope`, your `include`/`exclude` rules are wrong. If it is full of
`error`, the product has broken links — which is a finding, not a problem with
the tool.

### f. Trim the furniture, then do the real run

Real portals have a cookie banner, a chat widget and a support bubble on every
screen. They inflate the element count and invent components that span every
page:

```yaml
capture:
  exclude_selectors: ["#cookie-banner", ".chat-widget"]
```

Then the full pass:

```powershell
python -m ui_discovery.pipeline <url> --config scope.yaml --auth-state session.json
```

### g. If the capture contains customer data

Anything behind a login almost certainly does — in the model *and* in the
screenshots:

```yaml
privacy:
  redact_content: true        # names, emails, cards, IBANs out of the text
  redact_screenshots: true    # and covered in the pictures (follows the above)
outputs:
  retention_days: 30          # captures should not live forever
```

Detection finds *shapes*, not meaning, so a person's name in a sentence is not
found unless you list it under `privacy.person_names`.

---

## Everyday reminder

Each **new** terminal needs the environment activated again before you run
anything:

```powershell
.\.venv\Scripts\Activate.ps1
```

(You'll know it's active when the prompt starts with `(.venv)`.)

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `python : The term 'python' is not recognized` | Python isn't on PATH. Reinstall from python.org with **"Add python.exe to PATH"** ticked, then reopen the terminal. Try `py` instead of `python`. |
| `.\.venv\Scripts\Activate.ps1 ... running scripts is disabled on this system` | Run once, then retry: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` |
| Prompt doesn't show `(.venv)` | The venv isn't active. Run `.\.venv\Scripts\Activate.ps1` again from the project folder. |
| `pip install` fails / SSL / proxy errors | You may be on a restricted network. Try from a normal network, or `pip install -e ".[dev]" --trusted-host pypi.org --trusted-host files.pythonhosted.org` |
| `playwright` / browser errors, or "Executable doesn't exist" | Run `python -m playwright install chromium` again. |
| `net::ERR_...` or a page won't load | The site is unreachable from this machine (offline, VPN, or firewall). Confirm you can open the URL in a normal browser first. |
| Crawl returns 0 pages on a login portal | You need a session — run the `login` step and pass `--auth-state session.json`. |
| A one-time `tldextract` / `publicsuffix.org` error scrolls by, but the crawl still finishes | Harmless. On a restricted network it can't refresh a domain list and falls back to a bundled copy. Ignore it. |
| Command "hangs" | Crawls open a real browser and can take a few seconds per page. Give it time; add `--max-pages 5` to keep it short while testing. |

If something else breaks, copy the red error text from the terminal and send it
over — that's usually enough to pinpoint the fix.

---

See `README.md` for full command reference and `QA_REPORT.md` for what's been
tested. Runs on Python 3.11, Playwright 1.56, Crawlee 1.9, Pydantic 2.13.
