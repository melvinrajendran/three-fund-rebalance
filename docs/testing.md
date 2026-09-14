# Testing conventions

Part of the notes in [`CLAUDE.md`](../CLAUDE.md).

Test files mirror modules 1:1; tests are grouped in `Test*` classes by the function
under test. Names are full sentences describing the behavior.

`Prompter` takes injected `input_func`/`print_func`, so the entire interactive flow is
driven by a scripted list of canned answers -- **no monkeypatching of builtins**.
`tests/test_cli.py::ScriptedPrompter` and `new_account_responses()` are the helpers;
reuse them rather than writing new stdin plumbing. Adding a question to the flow means
threading one more answer into every scripted list. `NO_REVISION` is the one
every flow that reaches a report now has to answer ("Update an answer and
recompute?"), named rather than left as a bare `"n"` among the account answers
because it is the only one that is not about an account. Note the scripted
prompter discards question *text* -- it goes to `input_func` -- so a test can
never assert on a prompt's wording, only on what `say` printed and on
`all_consumed()`, which is what actually pins "one answer was re-asked and not
the whole flow". The band's two answers sit
between the VT allocation and the first "Add an account?", and existing flows pass
`"0"` for both so they keep testing exact-target behavior.

Two shapes to know when writing one. A stock/bond target is `"80", "y"` -- the stock
share and then the confirmation of the derived bond share -- and a multi-asset fund's
mix is `"60", "20", "y"` for the same reason. (`"100", "y"` is the third: a first
answer of 100 settles both remaining sleeves, so the second question is never asked.)

An account's funds are a **list the user builds**, so its answers are a loop rather
than a fixed run of slots. One fund is `name, kind, value`, where kind is `"1"`–`"4"`
for U.S. stocks / international stocks / bonds / a mix, and a mix inserts its two
sleeve percentages and the confirmation between the kind and the value. Between funds
comes `"Add another fund?"`, which defaults to **yes** -- so a list reads
`fund, "y", fund, "y", fund, "n"`, one `"y"` before each fund after the first and one
`"n"` to close it. Then the cash.

**A fund name already known in the run asks one question fewer.** Funds are remembered
by name, so a ticker typed into a second account -- or one the saved file lists -- is
`name, "Use these details?", value`: `known_fund_responses(name, value)`, where `""`
takes the saved kind and mix. A `"n"` there is followed by the kind (and mix) as for a
new fund. `new_account_responses(..., known=True)` is the VTI/VXUS/BND account for every
such account after the first in a run; forgetting it runs the script one answer out of
step.

The helpers are `fund_responses`, `multi_asset_fund_responses` and (in `test_cli.py`)
`account_responses` / `new_account_responses`; reuse them rather than spelling a list
out. On the update path every saved fund arrives behind its own `"Keep this fund?"`
and then offers name, kind and value as defaults, so `keep_fund_responses()` -- four
empty strings -- walks one fund through unchanged, and `keep_account_responses(*values)`
walks a whole account. All of that still sits behind the leading `"y"` for
"Keep this account?" that every saved account starts with.

`compute_trades`'s `band_pct` defaults to `Decimal(0)`, which is the exact target and
therefore the pre-band behavior -- solver tests that aren't about the band say nothing
about it and keep asserting the same numbers. `relative_band_pct` defaults to `None`
for the same reason: `0` there would collapse every one of those tests onto the exact
target by a different route, and silently.

Every network call is monkeypatched, including failure paths. The suite must stay
runnable offline -- CI depends on it.

`tests/test_network_sources.py` is the one deliberate exception, and it proves the rule
rather than bending it: mocks cannot tell you a URL has stopped being a URL, which is how
the VT endpoint stayed dead through several releases with every test green. Those tests
carry `pytest.mark.network`, `addopts` in `pyproject.toml` deselects them, and CI runs
bare `pytest` so it never sees them. Keep it that way -- a live source in the default run
would fail on a plane and flake in CI. What belongs there is only what a mock cannot
answer: that each source still responds, that the live payload still has the shape the
saved fixtures are written against, and that the two sources still agree to within a few
points. Assertions are deliberately loose, because a failure should mean rot rather than
a market that moved.
