"""Remove isolated test runs (output/test_runs/<run_id>/). DRY RUN unless --execute.

    python scripts\\clean_test_artifacts.py                        list every test run
    python scripts\\clean_test_artifacts.py --run-id <id>          just that run
    python scripts\\clean_test_artifacts.py --older-than-days 7    runs older than 7 days
    python scripts\\clean_test_artifacts.py ... --execute          delete exactly that list

Only folders directly under output/test_runs/ can be deleted; run ids are validated (no path
separators, no '..'), symlinks are never followed, and nothing outside output/test_runs/ is
ever touched - production state and private intelligence cannot be selected.
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from operations.test_run import execute_clean, plan_clean, test_root  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="remove isolated test runs (dry run by default)")
    ap.add_argument("--run-id")
    ap.add_argument("--older-than-days", type=float)
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args(argv)
    try:
        plan = plan_clean(args.run_id, args.older_than_days)
    except ValueError as exc:
        print(f"REFUSED: {exc}")
        return 2
    print(f"{'EXECUTE' if args.execute else 'DRY RUN'} - {test_root()}")
    for p, files, size in plan:
        print(f"  {os.path.basename(p):<40} {files:>6} files  {size / 1_048_576:>8.1f} MB")
    total = sum(s for _, _, s in plan)
    print(f"  {len(plan)} run(s), {total / 1_048_576:.1f} MB")
    if not args.execute:
        print("Nothing deleted. Re-run with --execute to delete the runs above.")
        return 0
    res = execute_clean(plan)
    print(f"Deleted {res['removed_runs']} run(s), freed {res['freed_bytes'] / 1_048_576:.1f} MB.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
