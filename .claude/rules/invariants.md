---
paths:
  - "three_fund_rebalance/models.py"
  - "three_fund_rebalance/allocation.py"
  - "three_fund_rebalance/rebalance.py"
  - "tests/test_models.py"
---

Read `docs/invariants.md` in full before changing anything here, and before
writing a test that asserts one of these rules. It states the rules that
span files -- money as `Decimal`, one key per asset class, one kind of fund per
account, a declared holding as capacity -- each with the failure that motivated
it.
