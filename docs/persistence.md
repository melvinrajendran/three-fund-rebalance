# Persistence

Part of the notes in [`CLAUDE.md`](../CLAUDE.md).

`~/.three_fund_rebalance/config.json`, versioned by `SCHEMA_VERSION`, written
atomically (temp file + `os.replace`). Saved values are re-offered as *editable
defaults*, never silently trusted.

**The saved accounts are listed before they are asked about, and the instruction is
said once.** Step 3 lists them vertically under "Saved Accounts" -- one name per line,
because those names are the headings the questions below arrive in and a list read
down the page is what lets someone match one to the next -- then says how to answer
them ("For each, press Enter to keep a saved answer, or type a new one.") **above
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

An account holding a multi-asset fund alongside single-asset ones used to be refused
on load, because the model forbade the mix. It is an ordinary account now, and both a
file written before the rule and one written under it load the same way. What
`Account` still refuses is two funds in one account sharing a name -- that name is the
key an order is placed against -- and that reaches the user the way any
`PersistenceError` does: `cli.run()` warns and starts blank.

The file is at v6, and upgrades run **one hop at a time** -- `config_from_dict` chains
`v1 → _upgrade_v1 → v2 → … → _upgrade_v5 → v6`, so a v1 file walks the
same path a v4 file does. Each upgrade translates without validating: anything still wrong surfaces from
the normal parse, so a corrupt old file reports what a corrupt current file would.
Each copies at every level, because a failed load must not leave the caller's parsed
JSON half-renamed. Any further rename of a persisted name needs another hop, not an
in-place edit of an existing one -- and `_upgrade_v1` must keep returning `2`, not
`SCHEMA_VERSION`, or it will skip every hop added after it.

- **v1 → v2** spelled the fund types after the academic asset classes
  (`domestic_equity`, `tdf`, `balance`, `balances_as_of`); v2 uses the same words the
  CLI prints (`us_stock`, `target_date`, `value`, `values_as_of`) -- `target_date`
  being renamed again by `_upgrade_v4`, which is why this hop keeps writing the
  spelling that was current when it was written rather than the spelling of the day.
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

- **v4 → v5** renames the `target_date` fund type to `multi_asset` and the
  `target_date_allocation` it carries to `allocation`. v4 could describe only one kind
  of fund with a fixed internal mix and called it a target-date fund, because an
  account could hold nothing beside one; v5 lets an account hold any combination,
  which makes a user-declared 60/40 fund expressible and the dated name wrong for it.
  A v4 target-date account loads as a one-fund multi-asset account, which is exactly
  what it always was. No account structure moves: v4 refused to load a mixed account,
  so no such file exists to translate -- what changed is that v5's model accepts one.

- **v5 → v6** moves each fund's `fund_type` and `allocation` off its holdings into one
  top-level `"funds"` list, because a fund name maps to one set of details across every
  account (see [`invariants.md`](invariants.md)) and two copies are what let them
  disagree. A fund holding is now `{name, value}` and is resolved against `funds` by
  `fund_name_key`; cash is `{fund_type: "cash", value}`. A holding that still carries
  its own details, or names a fund `funds` does not list, is a `PersistenceError`.
  The hop appends one `funds` entry per fund holding, repeats included, and the parse
  collapses repeats that agree. **Repeats that disagree are refused**, with the message
  naming the fund -- the hop is not the place to pick which of two answers was the
  user's, and it is the same treatment a v6 file describing one fund two ways gets.

`funds` holds exactly the funds some account holds. **A fund removed from every account
is removed from the file** on the next save -- `config_to_dict` writes
`FundCatalog.held_by(accounts)` -- because a fund the portfolio no longer holds is not
part of it, and a list that only grows is one nobody can prune. It also adds any
account fund `funds` lacks, which is what lets a `PersistedConfig` built from accounts
alone still write a loadable file. A hand-edited file listing an unheld fund still
loads; the entry is simply gone after the next save.

`rebalance_relative_band_pct` was added later **without a hop**, and deliberately: a
new optional key translates nothing, and its absence already means "never chosen"
exactly as an absent `rebalance_band_pct` does. A hop is for a name or a meaning that
changed. Note that `_upgrade_v2` now writes the literal `3` rather than
`SCHEMA_VERSION` -- same trap as `_upgrade_v1`, harmless only until the next hop
exists.
