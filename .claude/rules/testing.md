---
paths:
  - "tests/**/*.py"
---

Read `docs/testing.md` for the scripted-prompter conventions and the exact shapes
of canned answers. Every network call is monkeypatched and the suite must stay
runnable offline; `tests/test_network_sources.py` is the one deliberate
exception, deselected by default.
