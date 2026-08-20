# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# One-time setup (python3 on macOS may still be the system 3.9; use an explicit
# interpreter such as python3.12 if so)
python3 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"

pytest                                   # full suite (fast: ~0.4s, no network)
pytest tests/test_rebalance.py           # one file
pytest tests/test_cli.py::TestArgParsing::test_version_flag_prints_version_and_exits_cleanly
pytest -k "tdf and not persistence"      # by expression
pytest --cov=three_fund_rebalance --cov-report=term-missing

ruff check three_fund_rebalance tests    # lint (CI runs exactly this)
ruff check --fix <path>                  # autofix
```

Re-run `pip install -e ".[dev]"` only when packaging metadata changes (a new/bumped
dependency, a renamed console script, a new top-level package) — new modules inside
`three_fund_rebalance/` take effect immediately under the editable install.

### Running the CLI without side effects

The interactive flow reads and writes `~/.three_fund_rebalance/config.json` and hits
the network. To exercise it safely:

```bash
three-fund-rebalance --offline --vt-us-pct 62 --fresh --no-save --config /tmp/scratch.json
```

To see rendered output non-interactively, drive `run()` with a scripted prompter
rather than piping stdin — see "Testing conventions" below. Piping to stdin is
brittle because the prompt sequence branches on answers.

## Architecture

One linear pipeline, orchestrated end-to-end by `cli.run()`:

```
load_config → prompt_stock_bond_target → resolve_vt_split → compute_target_allocation
            → prompt_accounts → compute_trades → format_report → save_config
```

Modules map to stages: `persistence` ↔ config file, `prompts` ↔ all input,
`vt_allocation` ↔ the one network call, `allocation` ↔ percentages→dollars,
`rebalance` ↔ the solver, `report`/`formatting` ↔ output. `config.py` holds
constants only; `models.py` holds the dataclasses everything else passes around.

### Invariants that span files

**Money is `Decimal`, never `float`.** The sole exception is inside the LP, which
necessarily works in floats; `rebalance._to_decimal` and `models.to_cents` convert
back at the boundary. Introducing a float into a dollar amount elsewhere is a bug.

**A rebalance never moves money between accounts.** Each account's total value is an
equality constraint. Trades only reallocate *within* an account, including investing
that account's uninvested cash.

**`FundType.CASH` has an implicit target of zero** — cash is always fully invested.
It is excluded from the tradeable slots and from `_TARGET_FUND_TYPES`.

**A TDF is one position holding a fixed internal ratio,** not three positions.
`_fund_type_coefficient` is what lets a single slot contribute fractionally to all
three targets, so a TDF can coexist with individual funds in the same account.

### The solver (`rebalance.py`)

One decision variable per existing (account, holding) slot, solved as three
**lexicographic** phases — each phase's optimum is carried forward as a `<=` bound so
later phases refine but never undo earlier ones:

1. Minimize bonds left in taxable accounts (fill tax-advantaged room first).
2. Minimize trade volume *within taxable accounts* — the proxy for avoiding capital
   gains. **No cost-basis data is collected**, so this is an approximation, not a
   gains calculation. Do not describe it as one in user-facing text.
3. Tie-break by minimizing total trade volume everywhere, so the recommendation
   disturbs the fewest positions.

`_OBJECTIVE_SLACK` exists because HiGHS is not bit-exact — a hard `<=` against a raw
optimum can spuriously reject the next phase's true optimum. Don't tighten it to zero.

`_check_capacity_feasible` deliberately fails early with an actionable message rather
than letting scipy surface a generic "infeasible". Trades below `MIN_TRADE_DOLLARS`
are dropped as impractical.

### VT weighting (`vt_allocation.py`)

Source chain, tried in freshness order: monthly JSON endpoint → quarterly fact-sheet
PDF (separate host, outside the interactive site's bot protection) → last cached
value → manual entry. **It never guesses silently.** `FALLBACK_VT_US_PCT` is only
ever a *suggested default* in the manual prompt.

The fund page's HTML is deliberately not scraped — client-side rendered behind bot
protection. Don't "improve" this by adding a scraper.

### Output structure (`formatting.py`)

Hierarchy uses two devices only: a rule under a heading, and indentation.
`=` banners the three steps, `-` underlines divisions within a step, and below that
nesting is position alone — an account is a plain label, indented, with its contents
one level deeper. Resist adding a third rule style; that was tried and reverted.

`Prompter.indented()` carries depth for interactive output, so indentation is a
property of where you are in the flow rather than something spelled into each string.
`_at_depth` intentionally leaves leading blank lines flush — several messages open
with `\n` as a separator, and padding it would emit trailing whitespace.

**`report.py` must not import `prompts.py`.** Shared presentation constants
(`INDENT_UNIT`) live in `formatting.py`, which both import.

### Persistence

`~/.three_fund_rebalance/config.json`, versioned by `SCHEMA_VERSION`, written
atomically (temp file + `os.replace`). Saved balances are re-offered as *editable
defaults*, never silently trusted. A corrupt file raises `PersistenceError`, which
`cli.run()` catches to warn and continue blank rather than crash.

## Testing conventions

Test files mirror modules 1:1; tests are grouped in `Test*` classes by the function
under test. Names are full sentences describing the behavior.

`Prompter` takes injected `input_func`/`print_func`, so the entire interactive flow is
driven by a scripted list of canned answers — **no monkeypatching of builtins**.
`tests/test_cli.py::ScriptedPrompter` and `new_account_responses()` are the helpers;
reuse them rather than writing new stdin plumbing.

Every network call is monkeypatched, including failure paths. The suite must stay
runnable offline — CI depends on it.

## Gotchas

`pyproject.toml` configures no ruff `select`, so the active rule set is whatever the
resolved ruff version enables by default, and `dev` pins only `ruff>=0.6`. With 0.16.3
that default includes isort (`I001`) and flake8-simplify (`SIM117`) — import order
*is* enforced, and a newer or older ruff can change what CI flags.

Version lives in `three_fund_rebalance/__init__.py`; `pyproject.toml` derives it via
`dynamic = ["version"]`. Bump it in one place.

`license-files` in `pyproject.toml` is PEP 639 and requires `setuptools>=77`, which is
why the build-system floor is set there.
