from __future__ import annotations

import json
from pathlib import Path
import pandas as pd


def collect_runs(results_dir: str | Path = "results") -> pd.DataFrame:
    rows = []
    for p in Path(results_dir).glob("*/*/run.json"):
        try:
            rows.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            pass
    return pd.DataFrame(rows)


def disagreement_table(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    # This table is pre-evaluation; merge official resolved labels later.
    cols = [c for c in ["instance_id", "condition", "exit_status", "elapsed_seconds", "error"] if c in df.columns]
    return df[cols].sort_values(["instance_id", "condition"])
