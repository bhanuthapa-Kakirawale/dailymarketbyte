"""State store operations (manual / workflow use).

    python -m state status                   what the configured store holds, per namespace
    python -m state hydrate                  download the approved state into output/
    python -m state seed                     one-off: upload an existing output/ into an EMPTY
                                             store (bootstrap; immutable objects create-only)

Scheduled jobs hydrate and persist in-process (main.py); these commands exist for bootstrap and
diagnosis. Configuration: DMB_STATE_BACKEND / DMB_STATE_DIR / DMB_GCS_BUCKET / DMB_GCS_PREFIX.
"""
from __future__ import annotations

import argparse
import json
import sys


def main(argv=None) -> int:
    import config
    from . import NAMESPACES, from_env, hydrate, persist
    ap = argparse.ArgumentParser(prog="python -m state")
    ap.add_argument("command", choices=("status", "hydrate", "seed"))
    args = ap.parse_args(argv)
    store = from_env(config.OUT_DIR)
    if store is None:
        print("state store: local backend - the output directory is the state (nothing to do)")
        return 0
    if args.command == "status":
        out = {ns.key: len(store.list(ns.key)) for ns in NAMESPACES}
        print(json.dumps({"store": store.describe(), "objects": out}, indent=2))
        return 0
    if args.command == "hydrate":
        print(json.dumps(hydrate(config.OUT_DIR, store, job="MANUAL"), indent=2))
        return 0
    if any(store.list(ns.key) for ns in NAMESPACES):
        print("refusing to seed: the store is not empty (hydrate first; jobs persist in-process)")
        return 2
    res = persist(config.OUT_DIR, store, job="SEED", require_hydrated=False)
    print(json.dumps({k: v for k, v in res.items() if k != "uploaded_keys"}, indent=2))
    return 0 if res["persisted_to_state_store"] else 1


if __name__ == "__main__":
    sys.exit(main())
