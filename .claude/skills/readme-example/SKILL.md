---
name: readme-example
description: Regenerate the README's Example block from real CLI output. Use after any change to report.py or formatting.py wording, or whenever the Example may be stale.
---

# Regenerate the README Example

The Example is **real output, pasted verbatim**. It is the first thing a reader
sees and the reason the README is structured around it, so it may never be
hand-edited or hand-idealized -- regenerate it and paste the result.

## Generate

Drive `run()` with a scripted prompter, the way `tests/test_cli.py` does. **Never
pipe stdin** -- the prompt sequence branches on answers, so a piped script is
brittle. `tests/test_cli.py::ScriptedPrompter` and `new_account_responses()` are
the helpers to reuse.

Run under `COLUMNS=80`, which is what `tests/conftest.py` pins the suite to and
therefore the width every wrapping assertion in the repo assumes.

The scenario is fixed:

- 80/20 stock/bond
- a 5/25 rebalancing band
- three accounts, **each declaring all three funds**:
  - a Brokerage holding $60k VTI and $30k VXUS
  - a Roth IRA holding $20k VTI
  - a Traditional 401(k) holding $30k VTI and $10k BND

The two empty Roth slots are the point of the example -- they are what lets the
whole bond target land in the shelters, so the taxable account is left alone and
the report carries no taxable-sale disclosure. Do not change the scenario to make
a new feature visible; add a second example or leave it.

## Then fix three things by hand, and only these three

1. **The VT provenance line.** Passing `--vt-us-pct` to skip the network stamps
   "manually specified via --vt-us-pct" (`cli.py`), where the README shows the
   fetched form -- `formatting.describe_as_of` on a real date, e.g. "(as of June
   30, 2026)". The README deliberately shows the fetch path, because that is what
   a reader running the CLI normally will see. Substitute that one line.

2. **Cut the `Generated ...` line** that opens the report. It is a wall clock in
   whatever zone and minute the regeneration happened to run in, so pasting it
   dates the *README* rather than the example, and every later regeneration shows
   up as a diff in a line that carries no information about the program.

3. **Cut the closing disclaimer.** It is two lines the README has already given
   in full, in its own `## Disclaimer` section directly above the Example.

Everything between those two trims is exactly as printed. The trim is **at the
ends only** -- nothing inside may be touched or idealized.

## Check

- `pytest tests/test_architecture.py` -- the README stays under 78 columns
  (options-table rows excepted) and carries no em dash.
- Re-read `docs/readme-spec.md` if anything else about the README changed.
