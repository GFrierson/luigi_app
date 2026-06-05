"""
Aggregate runner for deterministic-extractor eval scripts (Phase 13).

Add one entry per eval script to _REGISTERED_EVALS as Phases 14/15 complete.
Entry shape: {"name": str, "run_eval": Callable[[], dict]} where run_eval
returns a dict with a "passed" boolean key.

Exits 0 if all registered evals pass (or none are registered), 1 otherwise.
"""

import logging
import sys

logger = logging.getLogger(__name__)

# Add one entry per eval script as Phases 14/15 complete.
# Entry shape: {"name": str, "run_eval": Callable[[], dict]}
#
# Anthem EOB eval (Phase 2) is intentionally NOT registered here yet — it is
# manual/local-only until N>=15 EOB samples are annotated in
# experiments/medical/anthm_eob/annotations.csv. Once that threshold is met,
# register it like so:
#     from experiments.medical.anthm_eob.eval_anthm_eob import run_eval
#     _REGISTERED_EVALS.append({"name": "anthm_eob", "run_eval": run_eval})
_REGISTERED_EVALS: list[dict] = []


def run_all() -> bool:
    if not _REGISTERED_EVALS:
        print("No registered evals. Pass.")
        return True
    all_passed = True
    for eval_entry in _REGISTERED_EVALS:
        result = eval_entry["run_eval"]()
        status = "PASS" if result.get("passed") else "FAIL"
        print(f"{eval_entry['name']:30s} | {status}")
        if not result.get("passed"):
            all_passed = False
    return all_passed


if __name__ == "__main__":
    sys.exit(0 if run_all() else 1)
