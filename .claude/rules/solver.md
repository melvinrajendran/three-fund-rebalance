---
paths:
  - "three_fund_rebalance/rebalance.py"
  - "tests/test_rebalance.py"
---

Before changing the solver, read `docs/solver.md` in full. It is normative, not
background: most of it records a behavior that shipped wrong and was caught in
real use, and the phase ranking *is* the design. `docs/invariants.md` covers the
model the solver reads.
