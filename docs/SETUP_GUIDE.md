# Setup Guide for Vaughn: tamperproof in PyCharm

Follow this top to bottom once. It takes about 30–45 minutes. Shortcuts are written *Windows/Linux / macOS*.

---

## 0. Prerequisites

| # | What | Why | Check it worked |
|---|---|---|---|
| 1 | **Git** ≥ 2.30 ([git-scm.com](https://git-scm.com/downloads)). On macOS, `xcode-select --install` also works. | tamperproof calls `git` directly, and so do the tests | `git --version` |
| 2 | **Python 3.12** ([python.org](https://www.python.org/downloads/)). 3.11.4+ is the minimum. **Windows:** tick *Add python.exe to PATH* in the installer. | Runs the project | `python --version` (Windows) / `python3 --version` (macOS) |
| 3 | **PyCharm** ([jetbrains.com/pycharm/download](https://www.jetbrains.com/pycharm/download/)). See the note below. | The IDE | Opens to the Welcome screen |
| 4 | **GitHub account** (you have `VaughnAntonio`) | Hosting the repo, CI | You can log in on github.com |
| 5 | **Git identity**, set once: `git config --global user.name "Vaughn Antonio"` and `git config --global user.email "<your GitHub email>"` | Commits need an author | `git config --global user.name` |
| 6 | **Claude Pro or higher** | Claude Code and Claude Projects both need a paid plan | You see *Projects* in the claude.ai sidebar |
| 7 | **Claude Code CLI** (see section 9) | AI pair-programmer that edits files and runs tests | `claude --version` |

> **About "PyCharm CE".** Since 2025.1, JetBrains merged PyCharm Community and Professional into **one PyCharm**. You get a 30-day Pro trial, then it falls back to the free core features, which cover everything this project needs: editor, debugger, pytest runner, Git, terminal and run configurations. When the trial ends, nothing in this guide stops working. If you still have an old CE install, it works too, but the unified one is the supported path.

Optional but useful: the **GitHub CLI** (`gh`) from [cli.github.com](https://cli.github.com/). It is not required.

---

## 1. Get the project onto your machine

### Option A: from GitHub (once the repo exists)

1. Open PyCharm. On the Welcome screen click **Clone Repository**. Inside an open project, use **Git | Clone…**.
2. URL: `https://github.com/VaughnAntonio/tamperproof.git`
3. Directory: for example `~/PycharmProjects/tamperproof`. Click **Clone**.
4. When asked, click **Trust Project**.
5. If GitHub asks you to sign in, choose **Log In via GitHub…**. A browser opens. Approve it.

### Option B: from the zip (also how you do the **first push**)

1. Unzip `tamperproof.zip` to `~/PycharmProjects/tamperproof`.
2. In PyCharm: **File | Open…** → select the `tamperproof` folder (the one containing `pyproject.toml`) → **Trust Project**.

The zip already contains the git history. If the GitHub repo is still empty, push it once:

1. **Git | Manage Remotes…** → **+** → Name `origin`, URL `https://github.com/VaughnAntonio/tamperproof.git` → OK.
2. **Git | Push…** (`Ctrl+Shift+K` / `Cmd+Shift+K`) → check that `main → origin : main` is listed → **Push**. Log in via the browser if asked.
3. Refresh the repo on github.com. Files appear, and the **Actions** tab starts the first CI run.

Terminal equivalent:
```bash
git remote add origin https://github.com/VaughnAntonio/tamperproof.git
git push -u origin main
```

---

## 2. Create the virtual environment (interpreter)

A virtual environment keeps this project's packages separate from everything else on your computer.

1. Open **Settings**: **File | Settings** on Windows/Linux, **PyCharm | Settings** on macOS (`Ctrl+Alt+S` / `Cmd+,`).
2. Go to **Project: tamperproof | Python Interpreter**.
3. Click **Add Interpreter → Add Local Interpreter…**.
4. Choose **Virtualenv**, **Generate new**:
   - Location: `<project>/.venv`
   - Base interpreter: your Python 3.12
5. Click **OK**. The bottom-right status bar should now say `Python 3.12 (tamperproof)`.

If PyCharm offers to create an environment from `pyproject.toml` when you open the project, accepting it does the same thing.

---

## 3. Install the project

1. Open the terminal: **View | Tool Windows | Terminal** (`Alt+F12` / `Option+F12`). The prompt should start with `(.venv)`. If it doesn't, close and reopen the terminal tab.
2. Run:
   ```bash
   pip install -e ".[dev]"
   ```
   `-e` means **editable**. Your code changes take effect immediately, without reinstalling. `[dev]` also installs `ruff`, the linter.
3. Check:
   ```bash
   tamperproof --help
   pytest -q
   ruff check .
   ```
   Expected results: a usage message, `9 passed`, and `All checks passed!`.

---

## 4. Use the shared run configurations

The repo ships four run configurations in `.run/`. PyCharm loads them automatically. Open the dropdown at the top-right of the window, next to the green ▶ button.

| Configuration | What it does |
|---|---|
| **1 Build demo repo** | Creates `demo_repo/` with `main`, `agent/honest`, `agent/cheat` |
| **2 Audit honest branch** | Should print `Verdict: PASS` (exit code 0) |
| **3 Audit cheat branch** | Should print `Verdict: BLOCK` (exit code 2) and write `cheat.sarif` and `cheat.md` |
| **All tests** | Runs the pytest suite in the test runner UI |

Run them in order: 1 → 2 → 3, then **All tests**. Select one and press **Run** (`Shift+F10` / `Ctrl+R`).

Notes:

- **3** ends with *Process finished with exit code 2*. That's correct. Exit code 2 means BLOCK, which is how CI would fail a cheating pull request.
- Run **1** again only after deleting `demo_repo/`, because `git init` won't recreate an existing repo cleanly. Delete it by right-clicking it in the Project view → **Delete**.
- To reopen `cheat.md` nicely, right-click it → **Open In | Markdown preview**, or just open it. PyCharm has a split preview.

**If the configurations don't appear:** use **Run | Edit Configurations… → + → Python**, and set:
- *Module name*: `tamperproof` (switch the dropdown from *Script path* to *Module name*)
- *Parameters*: `demo_repo --base main --head agent/cheat`
- *Working directory*: the project folder
- *Python interpreter*: the `.venv` one

For tests: **+ → Python tests → pytest**, *Script path* = the `tests` folder.

---

## 5. Run and read the tests in PyCharm

1. Open `tests/test_tamperproof.py`.
2. Click the green ▶ in the gutter next to any `def test_…` to run just that test. **Run 'All tests'** runs everything.
3. The **Run** tool window shows a tree of tests. Green means passed, red means failed. Click a red one to see the assertion diff.
4. Re-run only failed tests with the ↻✗ button in the test tool window.

To see coverage: *Run with Coverage* may be Pro-only after your trial. The free fallback is:
```bash
pip install pytest-cov
pytest --cov=tamperproof --cov-report=term-missing
```

---

## 6. Debug the proof engine (do this once, it's how you'll really understand the project)

1. Build the demo repo first (config **1**).
2. Open `tamperproof/proof.py`. In `oracle_restoration`, click the gutter next to the line **after** `restored = run_pytest(tree)` to set a red breakpoint (`Ctrl+F8` / `Cmd+F8`).
3. Set a second breakpoint in `branch_ablation` on the line after `flipped = [...]`.
4. Select config **3 Audit cheat branch** and click **Debug** 🐞 (`Shift+F9` / `Ctrl+D`).
5. When it stops, look at the **Debug** tool window → **Variables**:
   - `restored`: results of the **original** tests run against the agent's code. You'll see `test_tier_boundary_50: failed`. That is the proof.
   - `baseline`: the agent's own test results (all passed or skipped).
6. Press **Resume** (`F9` / `Cmd+Option+R`). At the second breakpoint, inspect `flipped`. It should contain `test_tier_boundary_100`, because removing `if qty == 100` made it fail.
7. Try **Evaluate Expression** (`Alt+F8` / `Option+F8`) and type `{k: v for k, v in restored.items() if v != "passed"}`.

**Why breakpoints in `demo_repo/test_pricing.py` won't trigger:** the proof engine runs pytest in a separate **subprocess**. To step into it, enable **Settings | Build, Execution, Deployment | Python Debugger | Attach to subprocess automatically while debugging**. Inspecting the results dicts as above is usually enough.

Debug controls: Step Over `F8`, Step Into `F7`, Step Out `Shift+F8`, Resume `F9` / `Cmd+Option+R`, Stop `Ctrl+F2` / `Cmd+F2`.

---

## 7. Navigating the code quickly

| Do this | Shortcut |
|---|---|
| Find any file, class or action | `Shift` `Shift` |
| Go to the definition of what's under the cursor | `Ctrl+B` / `Cmd+B` (or Ctrl/Cmd+click) |
| Find everywhere something is used | `Alt+F7` / `Option+F7` |
| Rename safely across the project | `Shift+F6` |
| Show the structure of the current file | `Ctrl+F12` / `Cmd+F12` |
| Recent files | `Ctrl+E` / `Cmd+E` |
| Quick fix / suggestions | `Alt+Enter` / `Option+Enter` |
| Reformat file | `Ctrl+Alt+L` / `Cmd+Option+L` |

A suggested reading order: `cli.py` → `audit.py` → `static_rules.py` (`diff_tests`, then `diff_source`) → `proof.py` → `report.py`.

**Ruff inside the editor (optional):** **Settings | Plugins | Marketplace** → search *Ruff* → Install → restart. Otherwise run `ruff check . --fix` in the terminal before committing.

---

## 8. Git workflow in PyCharm

Once the repo is cloned from GitHub:

1. Make a branch: click the branch name at the top-left (or bottom-right) → **New Branch…** → for example `feature/per-test-oracle`.
2. Edit code. Changed files turn blue in the Project view.
3. Commit: **Git | Commit…** (`Ctrl+K` / `Cmd+K`). Tick the files, write a message, and leave *Analyze code* ticked. Click **Commit**.
4. Push: **Git | Push…** (`Ctrl+Shift+K` / `Cmd+Shift+K`).
5. On github.com, open a Pull Request. The **CI** workflow runs lint, tests on Python 3.11–3.13, and the honest/cheat demo gate. A green tick means it all passed. The job summary shows the tamperproof Markdown report.
6. Pull others' changes or CI fixes: **Git | Update Project…** (`Ctrl+T` / `Cmd+T`).
7. History: **View | Tool Windows | Git** → **Log** tab.

Always run **All tests** before you push. CI runs the same tests anyway.

---

## 9. Set up Claude Code in PyCharm (main AI tool for coding)

Claude Code reads and edits the real files, runs `pytest` and `ruff` itself, and shows its changes in PyCharm's diff viewer. Your Pro plan includes it. Usage counts against the same limit as claude.ai chat.

### 9.1 Install the CLI

Follow the official quickstart: <https://code.claude.com/docs/en/quickstart>. It gives one install command per OS. Then, in a **new** terminal:

```bash
claude --version
```

### 9.2 Install the PyCharm plugin

1. **Settings | Plugins | Marketplace** → search **Claude Code [Beta]** → **Install** → restart PyCharm.
2. If PyCharm later says *"Cannot launch Claude Code"*, set the full path to `claude` in **Settings | Tools | Claude Code [Beta] → Claude command**.
3. So that `Esc` can interrupt Claude: **Settings | Tools | Terminal** → untick *Move focus to the editor with Escape*.

### 9.3 First run

1. Open the `tamperproof` project, then the terminal (`Alt+F12` / `Option+F12`). Make sure you're in the project root.
2. Run `claude`. The first time, log in with your claude.ai account (no API key needed).
3. Claude Code loads `CLAUDE.md` automatically, which imports `docs/CONTEXT.md`. Together they are the "memory" of the planning conversation: what the project is, why, the decisions made, and the rules. Check with:
   ```
   /memory
   ```
   You should see `CLAUDE.md` listed.
4. Ask a test question: *"Summarise this project and its two rules that must not change."* It should mention proof-only BLOCK and never weakening tests.

### 9.4 Your personal memory (all projects)

Copy `docs/user-CLAUDE.md.example` to `~/.claude/CLAUDE.md` (Windows: `C:\Users\<you>\.claude\CLAUDE.md`). It tells Claude Code who you are and how you like answers, in every project. Edit it whenever you like. Claude Code also keeps its own *auto memory* as it learns your corrections.

### 9.5 Daily use

| Do this | How |
|---|---|
| Open Claude Code from the editor | `Ctrl+Esc` / `Cmd+Esc`, or the Claude button |
| Point Claude at code | Select lines in the editor; the selection is shared automatically |
| Insert a file reference like `@tamperproof/proof.py#L40-60` | `Alt+Ctrl+K` / `Cmd+Option+K` |
| Review Claude's edit | It opens in PyCharm's diff viewer; accept or reject |
| Plan before changing code | `claude --permission-mode plan`, or press `Shift+Tab` to cycle modes |
| Resume yesterday's conversation | `claude --continue` (last) or `claude --resume` (pick one) |
| Free up context in a long session | `/compact` |

**Safety:** keep edits in the default Manual mode (Claude asks before each edit). JetBrains' docs warn that auto-accepting edits inside the IDE can also change IDE config files. `.claude/settings.json` in the repo pre-approves only safe commands (pytest, ruff, the demo, read-only git) and blocks reading `.env` files. Claude still asks before `git push`.

A good first task: *"Create a branch feature/per-test-oracle and implement roadmap item 1 from CLAUDE.md. Plan first, then implement, then run ruff and pytest."*

### 9.6 Optional: continue the original cloud session locally

The planning conversation ran as a Claude Code **cloud** session. Teleport can pull such a session, with its full history, into your terminal, but only if the session's branch is on GitHub and you run it from a clean clone of the same repo:

```bash
cd ~/PycharmProjects/tamperproof
claude --teleport            # pick the session from the list
```

That session started before the repo existed, so teleport may not offer it. If it doesn't, nothing is lost: `CLAUDE.md` and `docs/CONTEXT.md` already carry the context.

---

## 10. Set up the Claude Project

1. On claude.ai, click **Projects → Create project**. Name it `tamperproof` and give it a description like "Python tool that proves when AI agents game test suites. JetBrains portfolio project."
2. **Set project instructions**: open `docs/CLAUDE_PROJECT_INSTRUCTIONS.md`, copy everything **below the horizontal line**, and paste it.
3. **Add project knowledge**: upload `README.md`, `pyproject.toml`, every file in `tamperproof/`, `demo/build_demo_repo.py`, `tests/test_tamperproof.py`, and this guide. If your plan shows a **GitHub** option under *Add content*, you can connect the repo instead, and it stays in sync.
4. Test it with a message like *"Walk me through adding a TT07 rule for `pytest.raises` being removed, with the test and the PyCharm steps to run it."* The answer should include file paths, a unit test, and PyCharm menu paths with shortcuts.
5. After big changes, re-upload the changed files (or re-sync GitHub) so Claude isn't working from stale code.

---

## 11. Final checklist

- [ ] `git --version` and `python --version` work in a fresh terminal
- [ ] PyCharm status bar shows the `.venv` interpreter
- [ ] `pytest -q` → `9 passed`
- [ ] `ruff check .` → `All checks passed!`
- [ ] Run configs 1 → 2 → 3 give PASS, then BLOCK (exit code 2)
- [ ] You stopped at a breakpoint in `proof.py` and saw `restored`
- [ ] The repo is on GitHub, and the Actions tab shows a green CI run
- [ ] `claude` runs in PyCharm's terminal and `/memory` lists `CLAUDE.md`
- [ ] `~/.claude/CLAUDE.md` exists with your personal preferences
- [ ] The Claude Project has the instructions and knowledge files

---

## 12. Troubleshooting

| Symptom | Fix |
|---|---|
| `git: command not found` / tests fail with `FileNotFoundError: 'git'` | Install Git, then **restart PyCharm** so it sees the new PATH |
| `ModuleNotFoundError: tamperproof` | You skipped `pip install -e ".[dev]"`, or ran it outside `.venv`. Check the terminal prompt shows `(.venv)` |
| Terminal not using `.venv` | **Settings | Tools | Terminal** → tick *Activate virtualenv*, then open a new terminal tab |
| `TypeError: extractall() got an unexpected keyword argument 'filter'` | Python is older than 3.11.4. Install 3.12 and recreate `.venv` |
| Run config shows *"Please select a valid Python interpreter"* | Edit the configuration and choose the `.venv` interpreter |
| `fatal: destination path 'demo_repo' already exists` / odd demo results | Delete the `demo_repo/` folder and re-run config **1** |
| *"Process finished with exit code 2"* on config 3 | Not an error. BLOCK = exit code 2 by design |
| Windows: PowerShell refuses to activate the venv | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or switch the terminal shell to *Command Prompt* in Settings |
| `claude: command not found` in PyCharm | Restart PyCharm after installing; or set the full path in **Settings | Tools | Claude Code [Beta]** |
| Plugin features (diffs in IDE, selection) missing | Run `claude` from PyCharm's own terminal in the project root; or run `/ide` in Claude Code |
| Push rejected / auth prompt loops | **Settings | Version Control | GitHub** → remove the account and log in again via browser |

The tool has been tested on Linux (Python 3.12). It uses only cross-platform standard library calls, so Windows and macOS should work. If something OS-specific breaks, note the full error and ask in the Claude Project.
