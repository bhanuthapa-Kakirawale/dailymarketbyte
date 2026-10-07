"""Private Trading Intelligence Desk (Phase 1): a LOCAL, READ-ONLY web dashboard over the
existing PRIVATE_ANALYTICS stores (docs/PRIVATE_DESK_USER_GUIDE.md).

Boundaries, each enforced by a test (tests/test_private_desk_*.py):

* Read-only. Every SQLite connection goes through `private_desk.db.open_ro`
  (`storage.readonly.connect_readonly` + `PRAGMA query_only`), never a `*Store` class (their
  constructors migrate and write). The only file the desk ever writes is a regeneratable cache
  under `<OUT_DIR>/private_desk/`.
* No acquisition. The desk never calls a provider, a website or a model; detector values are
  REPLAYED from the stored OHLCV with the Radar's own detector functions (`private_desk.replay`),
  never a second implementation of a detector.
* Local only. The server binds a loopback address; anything else is refused.
* Observations, never instructions. A Radar candidate is an ATTENTION item, not a trade idea:
  no order fields, no broker client, no upload path.
* Outside the DMB critical path. Nothing in main.py / products / the operator scripts imports
  this package, so a desk failure cannot affect REPORT / PRE / POST.
"""

DESK_VERSION = "1.0"
