"""Remove disposable development artifacts. DRY RUN unless --execute (docs/CLEANUP_POLICY.md).

    python scripts\\clean_dev_artifacts.py             list what WOULD be deleted (nothing touched)
    python scripts\\clean_dev_artifacts.py --verbose   ... every file, and every protected match
    python scripts\\clean_dev_artifacts.py --execute   delete exactly that list

Never deleted: .env / credentials, databases, canonical reports, official snapshots, Market
Structure, Radar / private intelligence, run records and audits, source, tests, docs, and any
file the run history points at. The rules live in operations/dev_cleanup.py.
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from operations.dev_cleanup import execute, plan, summarize  # noqa: E402


def _mb(n: int) -> str:
    return f"{n / 1_048_576:.1f} MB"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--execute", action="store_true", help="actually delete (default: dry run)")
    ap.add_argument("--verbose", action="store_true", help="list every file and every kept match")
    args = ap.parse_args(argv)
    cands, kept = plan(ROOT)
    s = summarize(cands, kept)
    print(f"{'EXECUTE' if args.execute else 'DRY RUN'} - project {ROOT}")
    for rule, (n, b) in sorted(s["by_rule"].items()):
        print(f"  {rule:<14} {n:>5} item(s)  {_mb(b):>10}")
    print(f"  {'TOTAL':<14} {s['candidates']:>5} item(s)  {_mb(s['bytes']):>10}")
    print(f"  protected matches kept: {s['kept_matches']}")
    if args.verbose:
        for c in cands:
            print(f"    - [{c.rule}] {os.path.relpath(c.path, ROOT)} ({_mb(c.size)})")
        for rel, why in kept:
            print(f"    = KEEP {rel}: {why}")
    if not args.execute:
        print("Nothing deleted. Re-run with --execute to delete the items above.")
        return 0
    res = execute(ROOT, cands)
    print(f"Deleted {res['removed']} item(s), freed {_mb(res['freed_bytes'])}; "
          f"skipped {len(res['skipped'])}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
