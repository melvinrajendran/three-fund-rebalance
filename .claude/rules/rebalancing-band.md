---
paths:
  - "three_fund_rebalance/allocation.py"
  - "three_fund_rebalance/config.py"
  - "tests/test_allocation.py"
---

Read `docs/rebalancing-band.md`. The band is two rules a class must satisfy
*both* of (the 5/25 rule), `relative_band_pct` of `None` is distinct from `0`,
and the names `band_pct` / `relative_band_pct` are shared by the prompts, the
report, the saved keys and the README -- renaming one renames five.
