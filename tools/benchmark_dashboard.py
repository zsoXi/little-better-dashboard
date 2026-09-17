"""Stub benchmark runner (honest skeleton).

Until benchmark scenarios exist, any invocation reports
NOT_IMPLEMENTED and exits with code 2. Never fabricates numbers.

Usage:
    py -3.14 tools/benchmark_dashboard.py --help
    py -3.14 tools/benchmark_dashboard.py --scenario ci
"""
import argparse
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Stub benchmark runner: honest NOT_IMPLEMENTED until scenarios exist."
    )
    parser.add_argument(
        "--scenario",
        default="ci",
        help="benchmark scenario name (only 'ci' is recognized by the skeleton)",
    )
    args = parser.parse_args(argv)

    print(f"scenario: {args.scenario}")
    print("NOT_IMPLEMENTED: no benchmark scenarios exist yet; no numbers collected.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
