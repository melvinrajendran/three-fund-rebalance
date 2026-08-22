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
python -m three_fund_rebalance.cli --offline --vt-us-pct 62 --fresh --no-save --config /tmp/scratch.json
```

Always invoke it as a module, never through the `three-fund-rebalance` console
script: that runs the working copy regardless of what else is on PATH, so a pipx
or uv install of the same name can never be what you actually tested.

To see rendered output non-interactively, drive `run()` with a scripted prompter
rather than piping stdin — see "Testing conventions" below. Piping to stdin is
brittle because the prompt sequence branches on answers.

## Architecture

One linear pipeline, orchestrated end-to-end by `cli.run()`:

```
load_config → prompt_stock_bond_allocation → resolve_vt_allocation → compute_target_allocation
            → prompt_rebalance_band → prompt_accounts → compute_trades
            → format_report → save_config
```

Modules map to stages: `persistence` ↔ config file, `prompts` ↔ all input,
`vt_allocation` ↔ the one network call, `allocation` ↔ percentages→dollars,
`rebalance` ↔ the solver, `report`/`formatting` ↔ output. `config.py` holds
constants only; `models.py` holds the dataclasses everything else passes around.

The user walks **three** numbered steps — target allocation, rebalancing band,
account holdings. The report is not a fourth: it is what those produce, so it gets
`format_result_header` (same `=` rule, no "STEP x OF y") rather than a step banner.
`cli._INPUT_STEPS` is the count, in one place.

### Invariants that span files

**Money is `Decimal`, never `float`.** The sole exception is inside the LP, which
necessarily works in floats; `rebalance._to_decimal` and `models.to_cents` convert
back at the boundary. Introducing a float into a dollar amount elsewhere is a bug.

**A rebalance never moves money between accounts.** Each account's total value is an
equality constraint. Trades only reallocate *within* an account, including investing
that account's available cash.

**There are two shelters, not one.** `TaxTreatment` is three-valued: `TAXABLE`,
`TAX_DEFERRED` (traditional 401(k)/IRA, 403(b), 457(b), SEP, SIMPLE) and `TAX_FREE`
(Roth IRA, Roth 401(k), HSA). Both shelters are exempt today; they differ in what a
dollar of growth inside them is worth, which is what decides that bonds belong in
tax-deferred space and stocks in tax-free. Anything not `TAXABLE` is a shelter —
`Account.is_tax_advantaged()` — and every taxable-vs-sheltered test in the solver is
written against `TAXABLE` so that adding a third shelter kind would not silently
change phases 1, 2 or 5.

**What to hold is decided before where to hold it, and never by it.**
`_resolve_allocation` settles the three asset-class totals first, honoring the
rebalancing band; the solver then hits those as hard equalities. Every location
objective is phrased as "minimize this asset class in that kind of account", which
means *relocate* only while the class total is fixed — see the solver section, where
letting the band reach those phases turned out to be a real bug.

**`FundType.CASH` has an implicit target of zero** — cash is always fully invested.
It is excluded from the tradeable slots and from `_TARGET_FUND_TYPES`.

**A target-date fund is one position holding a fixed internal ratio,** not three
positions. `_fund_type_coefficient` is what lets a single slot contribute
fractionally to all three targets.

**An account holds a target-date fund *or* individual funds, never both** (cash may
sit alongside either). `Account.__post_init__` enforces it, `prompts` asks which kind
up front instead of offering a fourth yes/no, and `INDIVIDUAL_FUND_TYPES` is the set
that clashes with `TARGET_DATE`.

The consequence worth holding onto: **a target-date account has exactly one slot, so
the per-account budget equality pins it outright.** No objective can reach inside it.
That is what stops the solver from liquidating a taxable target-date fund to relocate
the bond sleeve within it — which it used to do even for a portfolio already sitting
on its target. It also means such an account sets a *floor* under every asset class,
not just a ceiling, which is why `_check_capacity_feasible` checks both.

### The solver (`rebalance.py`)

Two stages. **`_resolve_allocation` decides what each asset class should be worth**,
then six phases decide where to hold it.

The first stage is its own tiny LP over three variables, also lexicographic: (1) move
as little as possible from where the portfolio already sits — that is what a band
*means*, stay put inside it and move only to the nearest edge outside it; (2) among
the ties, sit as close to target as possible, which is what steers available cash at
whichever class is furthest below target. It returns `dict(current)` outright when the
first objective comes back at zero, so "leave it alone" is answered with the numbers
the portfolio already holds rather than with the solver's slack spent drifting a
fraction of a cent.

**Skipping that stage and handing the band to the six phases below is a bug that was
shipped once and caught in real use.** Given a portfolio inside its band but with
international parked in a Roth and a taxable account too full to take any, phase 5
could not relocate — so it satisfied itself by *selling* international and buying U.S.
stock up to the band ceiling, while phase 4 liquidated a bond fund the portfolio was
already 3.8 points underweight. Both objectives are stated as "minimize this asset
class in that kind of account", which only means "relocate it" while the class total
is fixed. `TestAllocationIsSettledBeforeLocation` pins the whole shape.

The six location phases then run over one shared variable layout, against the resolved
totals as hard equalities — each phase's optimum carried forward as a `<=` bound so
later phases refine but never undo earlier ones:

1. Minimize bonds left in taxable accounts (fill sheltered room first).
2. Minimize trade volume *within taxable accounts* — the proxy for avoiding capital
   gains. **No cost-basis data is collected**, so this is an approximation, not a
   gains calculation. Do not describe it as one in user-facing text.
3. Minimize wash-sale exposure: dollars bought, in a shelter, of a fund also held in
   a taxable account.
4. Minimize bonds held in *tax-free* accounts, i.e. put them in tax-deferred space
   and leave Roth/HSA room for stocks.
5. Minimize the international fund held in *tax-advantaged* accounts, i.e. prefer it
   in taxable, where its foreign withholding is claimable as a credit.
6. Tie-break by minimizing total trade volume everywhere, so the plan
   disturbs the fewest positions.

The variable layout is `[ x (n) | y (n) | w (k) ]` — slot values, their absolute
deviations `|x - current|`, and one-sided purchase amounts for phase 3. It is built
once, so an objective is a vector over the same columns and a solved optimum is a row
appended to `A_ub`. An objective with no nonzero coefficient is skipped rather than
solved: it would only re-find a feasible point and carry a vacuous bound.

**Everything below phase 2 is free-rearrangement only.** Phases 3, 4 and 5 can decide
*which* fund an account being traded anyway should end up in, and can rearrange
sheltered accounts at will, but none of them can open a taxable trade. That ranking is
the whole design: the credit phase 5 chases is worth a couple of basis points against
a realized gain we cannot even measure, and the same logic governs 3 and 4.

Some finer points that are easy to undo by accident:

- **Phase 5 tests `slot.fund_type` directly** rather than going through
  `_fund_type_coefficient`. A target-date fund is not majority-foreign, so it passes
  no credit through from either kind of account, and counting its international sleeve
  makes the solver liquidate half a TDF for nothing
  (`test_target_date_international_sleeve_is_left_alone` pins this).
- **Phase 4 does the opposite, deliberately**, and uses `_fund_type_coefficient`.
  Bonds inside a Roth's target-date fund really are bonds occupying tax-free space,
  exactly as phase 1 counts them — and a TDF account is pinned by its own budget row
  anyway, so counting them states the truth without giving the solver anything to act
  on.
- **Phase 3 penalizes only the sheltered *buy* side.** The taxable sale is the leg
  that realizes the loss, but it is also the leg phase 2 has already minimized and the
  one the portfolio usually has no choice about; penalizing it too would put phase 3
  in direct opposition to phase 1, which exists to sell exactly those taxable bonds.
- **A taxable holding of zero creates no wash-sale variable.** You cannot sell what
  you do not own. Without that check an empty taxable slot standing ready to receive a
  fund suppresses the very purchase phase 5 wants to make, over a sale that can never
  happen — `test_an_empty_taxable_slot_does_not_suppress_a_sheltered_purchase` is the
  regression guard, and it caught this during implementation.

Phase 3 cannot condition on whether the taxable side is *actually* sold — that is
decided by the same solve, and no linear objective can express it. The residue is a
mild preference against accumulating, in a shelter, a fund you already hold in
taxable. It costs nothing and usually points the same way as phases 4 and 5. What the
LP cannot avoid, `_wash_sale_warnings` reports after the fact from the final trades.

A caution when testing placement: this LP is degenerate, so a scenario where the
preferred placement merely *ties* proves nothing — the old solver often picked it
anyway. A test earns its keep only if it fails against the previous ranking; see
`test_international_is_moved_out_of_tax_advantaged_when_the_trades_are_free` and
`test_bonds_fill_tax_deferred_space_before_tax_free_space`. Phase 3 is especially
prone to this: the alternative-fund choice is usually volume-symmetric, so HiGHS
often picks the non-overlapping vertex on its own. Its tests are therefore built on
the deterministic paths — an unavoidable overlap that must warn, and the zero-holding
exclusion — rather than on a tie it happens to win.

`_OBJECTIVE_SLACK` exists because HiGHS is not bit-exact — a hard `<=` against a raw
optimum can spuriously reject the next phase's true optimum. Don't set it to zero.
But note that every carried bound is also a *budget a later phase can spend*: giving
up that much of an earlier priority is permitted, and the volume-minimizing phase at
the bottom will take it. At the original cent, with five bounds stacked up, that
surfaced as "sell $5,999.99" where the answer is $6,000.00. It is now a tenth of a
cent — clear of HiGHS's noise (verified from $100k to $8B) but below the cent grid
every displayed amount rounds to, so it cannot produce a visible artifact.

`_check_capacity_feasible` deliberately fails early with an actionable message rather
than letting scipy surface a generic "infeasible". It bounds each asset class by the
smallest and largest coefficient among an account's own slots, because the account
must spend exactly its own total across them — so a single-fund account's floor and
ceiling are the same number. All three ceilings are checked before any floor: one
account holding one fund breaches both at once, and "nothing you hold can be bonds"
points at the missing piece, while "you are stuck holding this much U.S. stock"
describes the same problem from the side the user can do least about.

What has to be reachable is the nearest *band edge*, not the target — a band the
portfolio can satisfy is satisfiable even when the exact target is not, which is one
of the things a band is for. `_band_note` names that edge in the message only when the
band is what makes the number binding; with no band the edge is the target, and
printing the same figure twice reads like a bug.

Trades below `MIN_TRADE_DOLLARS` are dropped as impractical.

### VT allocation (`vt_allocation.py`)

Source chain, tried in freshness order: monthly JSON endpoint → quarterly fact-sheet
PDF (separate host, outside the interactive site's bot protection) → last cached
value → manual entry. **It never guesses silently.** `FALLBACK_VT_US_PCT` is only
ever a *suggested default* in the manual prompt.

The fund page's HTML is deliberately not scraped — client-side rendered behind bot
protection. Don't "improve" this by adding a scraper.

### Output structure (`formatting.py`)

Hierarchy uses two devices only: a rule under a heading, and indentation.
`=` banners the three steps *and the report they produce*, `-` underlines divisions
within either, and below that nesting is position alone — an account is a plain label,
indented, with its contents one level deeper. Resist adding a third rule style; that
was tried and reverted. `format_result_header` is not a third style: same rule, same
width, just no step number.

**Two widths, both following the terminal.** `formatting.prose_width()` is
`min(terminal - 2, PROSE_MAX_WIDTH)`; `formatting.table_width()` is `terminal - 2` with
no cap. They diverge because they want opposite things: a paragraph gets *harder* to
read as it widens, while a table of dollar figures does not. Prose, warnings, notes and
the `=` banners all use prose width; tables are sized to their own contents within the
table budget.

This replaced a fixed 78, which was fine for prose but squeezed the tables — the
comparison table silently passed 78 at a $5M portfolio, because seven-figure dollar
cells are four characters wider than five-figure ones, and no test covered it.
`terminal_width()` reads `$COLUMNS` first, which is what makes any of this testable;
`tests/conftest.py` pins it at 80 for the whole suite so wrapping assertions don't
depend on the window pytest happens to run in.

Never hand-break a paragraph: write it as one string and let `wrap` do it, so editing
the wording doesn't mean re-breaking the lines. `Prompter.say_wrapped` is the same
thing at the prompter's current depth. `wrap` keeps hyphens and long words intact,
because textwrap will otherwise split "tax-advantaged" across lines, and in a document
about tax treatment that reads as a different term.

**Only the per-account holdings table may exceed the width budget.** Everything else --
prose, warnings, account headings, trade lines, the comparison table -- wraps or is
sized to fit, and `test_long_names_do_not_push_prose_or_headings_off_the_page` holds
the line. The exception is deliberate: a fund entered by its real name rather than its
ticker ("Vanguard Total Stock Market Index Fund Admiral Shares") cannot fit alongside
an amount in 78 columns, truncating it is how someone buys the wrong fund at the
broker, and wrapping it destroys the alignment the table exists for. It runs wide and
stays aligned. Nicknames are capped at input instead (`MAX_ACCOUNT_NAME_LENGTH`) --
those are labels the user invents, unlike a fund's real name, and they were what
pushed the headings off the page.

An account heading names the tax treatment *inside* the parentheses
(`Fidelity Roth IRA (Roth IRA, tax-free)`) and only when the account type does not
already say it. That is both shorter than a trailing dash and safe at the cap: the
longest possible heading lands at 77 characters rather than wrapping and stranding a
`--` at the end of a line.

### Wording the output has to keep

These are compliance-driven, not stylistic, and an edit that reads better but
loses them is a regression:

- **The report always ends with the disclaimer.** It is the artifact that gets
  screenshotted and acted on days later; a disclaimer that lives only in the README
  does not travel with it.
- **Nothing is called a "recommendation" and no order is phrased as an instruction.**
  "Recommendation" is a term of art under Reg BI and FINRA Rule 2111. Hence "Orders to
  place" and "Review each order before placing it:" rather than "Recommended trades"
  and "Place the following orders:".

- **"Order" and "trade" are not synonyms — use the industry split.** An *order* is the
  instruction you submit to a broker; a *trade* is the transaction that results, and
  the activity in general. So: "Orders to place", "Review each order before placing
  it", "before placing these orders", "these orders do not reach the target exactly",
  "once these orders are filled" — all instructions. And: "After these trades",
  "no trades needed", "the trades needed to rebalance", "taxable trade volume" — all
  activity or outcome. The giveaway is the verb: you *place*, *submit* and *fill* an
  order; you *make* a trade and live with its result.

  Code identifiers stay on "trade" (`Trade`, `compute_trades`, `MIN_TRADE_DOLLARS`)
  because in portfolio-rebalancing systems the computed output is a *trade list* --
  "order" belongs to the execution layer this program never reaches. That is a
  deliberate split, not an oversight.
- **Tax statements are conditional and attributed.** The wash-sale warning says a sale
  "may be" a wash sale and that "the IRS has taken the position (Rev. Rul. 2008-5)"
  -- never that it *is* one or that a loss *is* lost. The tool cannot see cost basis,
  trade dates, or purchases made elsewhere in the 61-day window.
- **"Tax-free" is qualified once**, under "Your accounts", because Roth and HSA
  withdrawals are tax-free only when qualified.
- **No claim implies future performance.** The asset-location note calls preferring
  tax-deferred space "a common asset-location convention, not a prediction" rather than
  asserting that stocks will out-grow bonds.
- **Figures carry their provenance** -- "Values as entered", plus the last-saved date
  when they came from a config file. The numbers are the user's, and can be stale.
- **Dropped sub-minimum moves are disclosed**, so trades that do not reach the target
  exactly are explained rather than looking like an arithmetic error.

**Indentation is carried by `Prompter.indented()` and `INDENT_UNIT`, never spelled
into a prompt string.** `_prompt_target_date_allocation` and `_prompt_new_holding` are
each called from two places at different depths, so a literal `"    "` that lines up
in one lands two levels off in the other -- which is exactly what happened, and what
let a `say_wrapped` conversion silently drop a line four columns out from its own
siblings. Every level steps by exactly one `INDENT_UNIT`.
`TestIndentation` pins the report's depths.

**Percentages follow two rules, one per side of the program.** The report fixes every
percentage at **one decimal place** -- whole numbers included, because "20% bonds" two
lines under "20.0%" is exactly the inconsistency the rule exists to stop. Prompts and
echoed-back values do the opposite and **trim trailing zeros** via
`formatting.format_percent`, so a default reads the way someone would type it and one
prompt never offers `[80]` while the next offers `[62.0]`. `prompt_percent` is the
single door for asking one: it owns the 0-100 bounds, the `(%)` suffix, and the default
formatting.

A share of the portfolio is `%`; a distance between two percentages is **percentage
points**, abbreviated `pts` in exactly one place -- the comparison table's `Drift (pts)`
header, where the column cannot take the words. `TestPercentFormatting` asserts that
count is one.

**Dollar amounts are right-aligned in columns.** The comparison table and the
per-account holdings list both compute their column widths from their own contents.
The point of putting figures in rows is to compare them down the page, which ragged
`label: $amount` lines defeat. A declared position holding nothing renders as `--`
rather than `$0.00`: it is capacity the solver can use, not a holding, and `$0.00`
gives it a precision it does not have.

**The report ends with where the trades land** (`_describe_outcome`) — the question
the rest of it only answers by implication. It is computed from the holdings rather
than the class totals, so a trade in a target-date fund moves all three sleeves by
their own fractions.

The report restates every answer it was given — target allocation and where it came
from, the band, the accounts and their holdings — before the current-vs-target summary
and the trades. Read on its own with no scrollback it should still say what was asked
for and what to do. `RebalanceInputs` carries that set, so recapping one more answer
does not mean growing `format_report`'s signature again.

`Prompter.indented()` carries depth for interactive output, so indentation is a
property of where you are in the flow rather than something spelled into each string.
`_at_depth` intentionally leaves leading blank lines flush — several messages open
with `\n` as a separator, and padding it would emit trailing whitespace.

**`report.py` must not import `prompts.py`.** Shared presentation constants
(`INDENT_UNIT`) live in `formatting.py`, which both import.

### Persistence

`~/.three_fund_rebalance/config.json`, versioned by `SCHEMA_VERSION`, written
atomically (temp file + `os.replace`). Saved values are re-offered as *editable
defaults*, never silently trusted.

**Every way a config file can fail to load raises `PersistenceError`** — that is
what `cli.run()` catches to warn and continue blank instead of crashing, so any
other exception escaping the parse takes the whole run down over a file the user
can hand-edit. Valid JSON of the wrong shape counts: `"accounts": 7`, a holding
that isn't an object, a name that's a list. The inner parsers name what's wrong
where they can, and `config_from_dict` wraps the lot in a catch-all that converts
anything unanticipated (re-raising `PersistenceError` untouched so specific
messages survive). `tests/test_persistence.py::MALFORMED` is the table to extend
when a new shape shows up.

A config saved before accounts became one-kind-or-the-other can hold a mix, and no
longer loads; `Account`'s message names the account, and `cli.run()` warns and starts
blank as it does for any `PersistenceError`. That is deliberate — splitting such an
account automatically would invent an account boundary that is a hard constraint on
the solver.

The file is at v3, and upgrades run **one hop at a time** — `config_from_dict` chains
`v1 → _upgrade_v1 → v2 → _upgrade_v2 → v3`, so a v1 file walks the same path a v2 file
does. Each upgrade translates without validating: anything still wrong surfaces from
the normal parse, so a corrupt old file reports what a corrupt current file would.
Each copies at every level, because a failed load must not leave the caller's parsed
JSON half-renamed. Any further rename of a persisted name needs another hop, not an
in-place edit of an existing one — and `_upgrade_v1` must keep returning `2`, not
`SCHEMA_VERSION`, or it will skip every hop added after it.

- **v1 → v2** spelled the fund types after the academic asset classes
  (`domestic_equity`, `tdf`, `balance`, `balances_as_of`); v2 uses the same words the
  CLI prints (`us_stock`, `target_date`, `value`, `values_as_of`).
- **v2 → v3** splits the single `tax_advantaged` treatment into `tax_deferred` and
  `tax_free`, re-inferred from the account's own persisted `account_type` via
  `ACCOUNT_TYPE_TAX_TREATMENT`. An unrecognized type — including `"Other"`, whose v2
  answer was a yes/no that never recorded the difference — becomes `tax_deferred`:
  bonds fill that space first, so guessing this way costs nothing if it is wrong.
  `rebalance_band_pct` is deliberately left *absent* rather than defaulted, because
  absent means "never chosen" and the step 2 prompt offers the default; writing one in
  would make a guess look like the user's own saved answer.

## Testing conventions

Test files mirror modules 1:1; tests are grouped in `Test*` classes by the function
under test. Names are full sentences describing the behavior.

`Prompter` takes injected `input_func`/`print_func`, so the entire interactive flow is
driven by a scripted list of canned answers — **no monkeypatching of builtins**.
`tests/test_cli.py::ScriptedPrompter` and `new_account_responses()` are the helpers;
reuse them rather than writing new stdin plumbing. Adding a question to the flow means
threading one more answer into every scripted list — the band's answer sits between
the VT allocation and the first "Add an account?", and existing flows pass `"0"` so
they keep testing exact-target behavior.

`compute_trades`'s `band_pct` defaults to `Decimal(0)`, which is the exact target and
therefore the pre-band behavior — solver tests that aren't about the band say nothing
about it and keep asserting the same numbers.

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
