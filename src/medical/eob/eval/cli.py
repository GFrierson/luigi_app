"""
CLI entry point for the EOB eval harness (Workstream B).

Runs the harness over the fixture corpus and prints a per-failure-mode report:

    python -m src.medical.eob.eval.cli \\
        --fixture-dir tests/fixtures \\
        --expected-dir tests/fixtures/expected \\
        --eval-db /tmp/eval.db \\
        [--run-id RUN_ID] \\
        [--llm] \\
        [--report worst|by-insurer|by-column|by-subtype]

The report tables are the cutover-gate input: read ``worst`` to see which
buckets fall below threshold before promoting an insurer off the LLM fallback.
"""

import argparse
import logging

from src.medical.eob.eval.harness import run_harness
from src.medical.eob.eval.report import (
    accuracy_by_column,
    accuracy_by_insurer_kind,
    accuracy_by_subtype,
    load_results,
    worst_buckets,
)

logger = logging.getLogger(__name__)


_REPORTERS = {
    "worst": worst_buckets,
    "by-insurer": accuracy_by_insurer_kind,
    "by-column": accuracy_by_column,
    "by-subtype": accuracy_by_subtype,
}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m src.medical.eob.eval.cli",
        description="Run the EOB eval harness and print a per-failure-mode report.",
    )
    parser.add_argument("--fixture-dir", required=True, help="dir with <fixture>.pdf files")
    parser.add_argument(
        "--expected-dir", required=True, help="dir with <fixture>.json expectations"
    )
    parser.add_argument("--eval-db", required=True, help="SQLite path for eval_results")
    parser.add_argument("--run-id", default=None, help="reuse/label a run id")
    parser.add_argument(
        "--llm", action="store_true", help="enable LLM fallback for unknown issuers"
    )
    parser.add_argument(
        "--report",
        choices=sorted(_REPORTERS),
        default="worst",
        help="which report to print after the run (default: worst)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Run the harness, then print the requested report to stdout."""
    logging.basicConfig(level=logging.INFO)
    args = _parse_args(argv)

    run_id = run_harness(
        fixture_dir=args.fixture_dir,
        expected_dir=args.expected_dir,
        eval_db_path=args.eval_db,
        run_id=args.run_id,
        llm_override=args.llm,
    )

    df = load_results(args.eval_db, run_id=run_id)
    report = _REPORTERS[args.report](df)

    # Stdout output is the deliverable of the CLI; logging stays for tracing.
    logger.info(f"run_id={run_id} rows={len(df)} report={args.report}")
    print(f"\n=== eval run {run_id} :: {args.report} ===")
    print(report.to_string(index=False))


if __name__ == "__main__":
    main()
