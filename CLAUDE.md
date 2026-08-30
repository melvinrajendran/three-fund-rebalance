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
pytest -k "target_date and not persistence"   # by expression
pytest -m network                        # opt-in: hits the live VT sources
pytest --cov=three_fund_rebalance --cov-report=term-missing

ruff check three_fund_rebalance tests    # lint (CI runs exactly this)
ruff check --fix <path>                  # autofix
```

Re-run `pip install -e ".[dev]"` only when packaging metadata changes (a new/bumped
dependency, a renamed console script, a new top-level package) -- new modules inside
`three_fund_rebalance/` take effect immediately under the editable install.

### Running the CLI without side effects

The interactive flow reads and writes `~/.three_fund_rebalance/config.json` and hits
the network. To exercise it safely:

```bash
python -m three_fund_rebalance.cli --offline --vt-us-pct 62 --fresh --no-save --config /tmp/scratch.json
```

Always invoke it as a module, never through the `three-fund-rebalance` console
script: that runs the working copy regardless of what else is on PATH, so a uv
or pip install of the published package can never be what you actually tested.

To see rendered output non-interactively, drive `run()` with a scripted prompter
rather than piping stdin -- see "Testing conventions" below. Piping to stdin is
brittle because the prompt sequence branches on answers.

## Architecture

One linear pipeline, orchestrated end-to-end by `cli.run()`:

```
load_config → prompt_stock_bond_allocation → resolve_vt_allocation
            → prompt_rebalance_band → prompt_accounts
            → ⟳ compute_target_allocation → compute_trades → format_report → _revise
            → _offer_summary_file → save_config
```

The one loop in it is `⟳`: a typo is noticed in the report and nowhere
earlier, so `_revise` re-asks exactly one answer and the loop recomputes.
See `docs/cli-flow.md` for that and for the three numbered steps the user
walks.

Modules map to stages: `persistence` ↔ config file, `prompts` ↔ all input,
`vt_allocation` ↔ the one network call, `allocation` ↔ percentages→dollars,
`rebalance` ↔ the solver, `report`/`formatting` ↔ output. `config.py` holds
constants only; `models.py` holds the dataclasses everything else passes around.

The imports form a DAG with `models` at the bottom and `cli` at the top, and it is
worth keeping that way. In particular **`report` does not import `rebalance`**: it
renders a `RebalanceResult`, which lives in `models` alongside `Trade` for exactly
that reason, so the reporting layer depends on the data and not on the scipy-backed
solver that produced it. (`report.py` must not import `prompts.py` either -- shared
presentation constants live in `formatting.py`.)

## Invariants

Each of these is stated in full, with the failure that motivated it, in the
`docs/` file named beside it. They are listed here because they are true in every
session and because a change that breaks one usually looks reasonable up close.

1. **Money is `Decimal`, never `float`.** The sole exception is inside the LP;
   `rebalance._to_decimal` and `models.to_cents` convert back at the boundary.
   (`docs/invariants.md`)
2. **An asset class has one key, defined in one place** --
   `allocation.ASSET_CLASS_KEYS`, where bonds are `"bond"` and *not*
   `FundType.US_BOND.value`. Import the mapping; never re-type it.
   (`docs/invariants.md`)
3. **A rebalance never moves money between accounts.** Each account's total value
   is an equality constraint. (`docs/invariants.md`)
4. **There are two shelters, not one** -- `TAX_DEFERRED` and `TAX_FREE`. Write
   every test against `TAXABLE`, never against one shelter kind.
   (`docs/invariants.md`)
5. **What to hold is decided before where to hold it, and never by it.** The band
   is a trigger, not a destination; handing it to the location phases is a bug
   that shipped once. (`docs/solver.md`)
6. **`FundType.CASH` has an implicit target of zero**, and cash is not an asset
   class for drift purposes. (`docs/invariants.md`)
7. **An account holds a target-date fund *or* individual funds, never both** --
   and one holding individual funds declares all three. (`docs/invariants.md`)
8. **A declared holding is capacity, whatever it is worth.** A zero one renders
   as `--`, not `$0.00`. (`docs/invariants.md`)
9. **A target-date fund is one position holding a fixed internal ratio**, stated
   once as `Holding.fraction_of`. (`docs/invariants.md`)
10. **The LP must never over-determine the portfolio total** -- the third
    asset-class equality is implied, and stating it anyway makes large portfolios
    infeasible. Do not "complete" that loop. (`docs/solver.md`)
11. **A target the accounts cannot reach is approximated and disclosed, never
    refused.** (`docs/solver.md`)
12. **Nothing is called a "recommendation" and no order is phrased as an
    instruction**; "order" and "trade" are not synonyms; the report always ends
    on the disclaimer. These are compliance-driven, not stylistic.
    (`docs/output-wording.md`)
13. **Every date and time the program prints or saves is the user's own local
    one.** `cli._now_local` is the only clock. (`docs/output-wording.md`)
14. **Every way a config file can fail to load raises `PersistenceError`**, which
    `cli.run()` catches to warn and start blank. (`docs/persistence.md`)

## Where the rest of this lives

The design rationale is in `docs/`, one file per area. Each has a matching stub
in `.claude/rules/` carrying a `paths:` glob, so the right document loads by
itself when a file it governs is read. **Read the document before changing the
code it covers** -- most of it records a behavior that shipped wrong and was
caught in real use, so it is normative rather than background.

| document | covers | loads when reading |
|---|---|---|
| `docs/invariants.md` | the rules above, in full | `models.py`, `allocation.py`, `rebalance.py` |
| `docs/solver.md` | the two allocation LPs and the six location phases | `rebalance.py`, its tests |
| `docs/rebalancing-band.md` | the 5/25 rule and the two band settings | `allocation.py`, `config.py`, its tests |
| `docs/vt-allocation.md` | the source chain and its failure modes | `vt_allocation.py`, its tests |
| `docs/output-structure.md` | widths, hierarchy, alignment, indentation | `report.py`, `formatting.py`, `prompts.py`, their tests |
| `docs/output-wording.md` | what the program is allowed to say | same as above |
| `docs/cli-flow.md` | the revise loop, the update menu, the summary file | `cli.py`, `prompts.py`, `tests/test_cli.py` |
| `docs/persistence.md` | the config file and its schema upgrades | `persistence.py`, `config.py`, its tests |
| `docs/readme-spec.md` | what the README has to be | `README.md` |
| `docs/testing.md` | scripted-prompter conventions and answer shapes | `tests/**/*.py` |

Two procedures are skills rather than prose, so they cost nothing until run:
`/release` cuts a release, and `/readme-example` regenerates the README's Example
block from real output.

**Where a new note goes.** A design decision about one area goes to that area's
`docs/` file. A fact that is true in every session goes here, as *one line* --
never a paragraph. `CLAUDE.md` grew from 146 lines to 1,538 in eleven days by
being the only place anything could go; keeping it under 200 lines is what keeps
it read.

## Commits and PRs

No commit message carries a `Claude-Session:` trailer and no PR body carries a
`claude.ai/code/session_...` link. A session URL resolves only for the account
that made it, so to everyone else reading `git log` or a PR on GitHub it is a
dead link. This is enforced by `attribution` in `.claude/settings.json` rather
than left to prose. The trailer was stripped retroactively from the seven
commits that had it and from the four PR bodies, so a session link showing up
again is a regression and not history.

## Testing conventions

Test files mirror modules 1:1; tests are grouped in `Test*` classes by the
function under test, and named as full sentences describing the behavior.

`Prompter` takes injected `input_func`/`print_func`, so the entire interactive
flow is driven by a scripted list of canned answers -- **no monkeypatching of
builtins**. Every network call is monkeypatched and the suite must stay runnable
offline; `tests/test_network_sources.py` is the one deliberate exception,
deselected by default and run before a release.

`docs/testing.md` has the rest: the helpers to reuse, the exact shapes of scripted
answers, and why `compute_trades`'s band arguments default the way they do.

`tests/test_architecture.py` checks the rules that can be checked -- the import
DAG and the README's mechanics -- so they survive a refactor.

## Gotchas

`pyproject.toml` pins the ruff rule set explicitly: `[tool.ruff.lint] select = ["E",
"F", "I", "UP", "B", "SIM"]`. It used to select nothing, which left the active set as
whatever the resolved ruff version enabled by default -- so isort (`I001`) and
flake8-simplify (`SIM117`) were being enforced without anyone choosing them, and a
`ruff>=0.6` floor meant an upgrade could change what CI accepts in either direction.
Naming them makes the lint reproducible.

`E` is the half that matters day to day: `E501` is *not* in ruff's default set, so
`line-length = 100` was advisory and about thirty lines had quietly passed it. It is
enforced now. `B905` is the other one worth knowing -- every `zip()` over two lists
that are the same length by construction says `strict=True`, so a future change that
breaks that assumption raises instead of silently truncating.

Version lives in `three_fund_rebalance/__init__.py`; `pyproject.toml` derives it via
`dynamic = ["version"]`. Bump it in one place.

`license-files` in `pyproject.toml` is PEP 639 and requires `setuptools>=77`, which is
why the build-system floor is set there.
