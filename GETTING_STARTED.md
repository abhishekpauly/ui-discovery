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
