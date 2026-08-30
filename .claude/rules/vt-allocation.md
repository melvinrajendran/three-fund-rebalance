---
paths:
  - "three_fund_rebalance/vt_allocation.py"
  - "tests/test_vt_allocation.py"
  - "tests/test_network_sources.py"
---

Read `docs/vt-allocation.md` before changing this file. Two things to know: a URL here is
verified against a live response body and never a status code (the primary
source was dead for five releases with every test green), and
`requests.exceptions.JSONDecodeError` must be caught before
`requests.RequestException`.
