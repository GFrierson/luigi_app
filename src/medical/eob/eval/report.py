"""
Per-failure-mode reporting over the ``eval_results`` table (Workstream B).

"Measure the process, not the model": aggregate accuracy lies. Every report here
groups the results by a failure-mode dimension and reports accuracy
(``(outcome == "match").mean()``) per bucket, sorted worst-first, so the
insurer-cutover gate reads which (insurer × kind × subtype × field) combinations
fail rather than a single headline number.
"""

import logging
from typing import Optional

import pandas as pd

from src.medical.eob.eval.store import get_eval_results

logger = logging.getLogger(__name__)


def load_results(eval_db_path: str, run_id: Optional[str] = None) -> pd.DataFrame:
    """Load eval rows (optionally one run) into a DataFrame."""
    rows = get_eval_results(eval_db_path, run_id=run_id)
    return pd.DataFrame(rows)


def _accuracy_by(df: pd.DataFrame, dims: list[str]) -> pd.DataFrame:
    """Group by ``dims`` and compute match-rate accuracy, worst-first."""
    if df.empty:
        return pd.DataFrame(columns=[*dims, "accuracy", "n"])
    grouped = (
        df.assign(_match=(df["outcome"] == "match"))
        .groupby(dims, dropna=False)
        .agg(accuracy=("_match", "mean"), n=("_match", "size"))
        .reset_index()
        .sort_values("accuracy", ascending=True)
        .reset_index(drop=True)
    )
    return grouped


def accuracy_by_insurer_kind(df: pd.DataFrame) -> pd.DataFrame:
    """Accuracy per (insurer, kind), worst-first."""
    return _accuracy_by(df, ["insurer", "kind"])


def accuracy_by_column(df: pd.DataFrame) -> pd.DataFrame:
    """Accuracy per field (column), worst-first."""
    return _accuracy_by(df, ["field"])


def accuracy_by_subtype(df: pd.DataFrame) -> pd.DataFrame:
    """Accuracy per subtype, worst-first."""
    return _accuracy_by(df, ["subtype"])


def worst_buckets(df: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    """The ``n`` worst (insurer, kind, subtype, field) buckets, accuracy ascending."""
    buckets = _accuracy_by(df, ["insurer", "kind", "subtype", "field"])
    return buckets.head(n).reset_index(drop=True)
