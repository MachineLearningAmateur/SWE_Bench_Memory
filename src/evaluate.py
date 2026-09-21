from __future__ import annotations

import subprocess
from pathlib import Path


def swebench_eval_command(dataset_name: str, predictions: str | Path, run_id: str, *, max_workers: int = 1) -> list[str]:
    return [
        "python", "-m", "swebench.harness.run_evaluation",
        "--dataset_name", dataset_name,
        "--predictions_path", str(predictions),
        "--max_workers", str(max_workers),
        "--run_id", run_id,
        "--cache_level", "env",
        "--clean", "True",
    ]


def run_eval(dataset_name: str, predictions: str | Path, run_id: str, *, max_workers: int = 1, check: bool = False):
    cmd = swebench_eval_command(dataset_name, predictions, run_id, max_workers=max_workers)
    return subprocess.run(cmd, text=True, check=check)
