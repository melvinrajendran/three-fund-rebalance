---
paths:
  - "three_fund_rebalance/persistence.py"
  - "three_fund_rebalance/config.py"
  - "tests/test_persistence.py"
---

Read `docs/persistence.md` before changing this file. Every way a config file can fail to load must raise
`PersistenceError`, and schema upgrades run one hop at a time -- a rename of a
persisted name needs a new hop, never an in-place edit of an existing one.
