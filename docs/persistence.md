# Persistence

Part of the notes in [`CLAUDE.md`](../CLAUDE.md).

`~/.three_fund_rebalance/config.json`, versioned by `SCHEMA_VERSION`, written
atomically (temp file + `os.replace`). Saved values are re-offered as *editable
defaults*, never silently trusted.

**The saved accounts are listed before they are asked about, and the instruction is
said once.** Step 3 lists them vertically under "Saved Accounts" -- one name per line,
because those names are the headings the questions below arrive in and a list read
down the page is what lets someone match one to the next -- then says how to answer
them ("For each, press Enter to use its saved value, or type a new value.") **above
the list rather than at the head of each account**, where it said nothing the previous
account had not already said. Each account then opens with "Keep this account?", which
is the one way the flow drops a saved account; answering no says `Removed '<name>'.`
and moves on. `TestSavedAccountsLine` pins the list and the single instruction.

**Every way a config file can fail to load raises `PersistenceError`** -- that is
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
blank as it does for any `PersistenceError`. That is deliberate -- splitting such an
account automatically would invent an account boundary that is a hard constraint on
the solver.

The file is at v4, and upgrades run **one hop at a time** -- `config_from_dict` chains
`v1 → _upgrade_v1 → v2 → _upgrade_v2 → v3 → _upgrade_v3 → v4`, so a v1 file walks the
same path a v3 file does. Each upgrade translates without validating: anything still wrong surfaces from
the normal parse, so a corrupt old file reports what a corrupt current file would.
Each copies at every level, because a failed load must not leave the caller's parsed
JSON half-renamed. Any further rename of a persisted name needs another hop, not an
in-place edit of an existing one -- and `_upgrade_v1` must keep returning `2`, not
`SCHEMA_VERSION`, or it will skip every hop added after it.

- **v1 → v2** spelled the fund types after the academic asset classes
  (`domestic_equity`, `tdf`, `balance`, `balances_as_of`); v2 uses the same words the
  CLI prints (`us_stock`, `target_date`, `value`, `values_as_of`).
- **v2 → v3** splits the single `tax_advantaged` treatment into `tax_deferred` and
  `tax_free`, re-inferred from the account's own persisted `account_type` via
  `ACCOUNT_TYPE_TAX_TREATMENT`. An unrecognized type -- including `"Other"`, whose v2
  answer was a yes/no that never recorded the difference -- becomes `tax_deferred`:
  bonds fill that space first, so guessing this way costs nothing if it is wrong.

  Note `_upgrade_v2` looks types up in the *current* `ACCOUNT_TYPE_TAX_TREATMENT`, which
  no longer holds v2's spellings. That is safe only because the lookup runs solely for
  accounts marked `tax_advantaged`, and the one type v4 renamed is taxable. A rename
  that touches a shelter will need `_upgrade_v2` to carry its own frozen v2-era map.
  `rebalance_band_pct` is deliberately left *absent* rather than defaulted, because
  absent means "never chosen" and the step 2 prompt offers the default; writing one in
  would make a guess look like the user's own saved answer.

- **v3 → v4** renames the `Taxable Brokerage` account type to `Brokerage`. Every other
  entry on the list is the account's actual name -- Roth IRA, 403(b), HSA -- while
  "Taxable" is a descriptor, and Title-Casing it put the one word the report otherwise
  always writes lowercase (beside "tax-free" and "tax-deferred") into a proper noun. An
  account type the map does not know, `"Other"` included, is left exactly as it is.

`rebalance_relative_band_pct` was added later **without a hop**, and deliberately: a
new optional key translates nothing, and its absence already means "never chosen"
exactly as an absent `rebalance_band_pct` does. A hop is for a name or a meaning that
changed. Note that `_upgrade_v2` now writes the literal `3` rather than
`SCHEMA_VERSION` -- same trap as `_upgrade_v1`, harmless only until the next hop
exists.
