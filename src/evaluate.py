from __future__ import annotations

import subprocess
import hashlib
from pathlib import Path


def swebench_eval_command(dataset_name: str, predictions: str | Path, run_id: str, *, max_workers: int = 1) -> list[str]:
    return [
        "python", "-m", "swebench.harness.run_evaluation",
        "--dataset_name", dataset_name,
        "--predictions_path", str(predictions),
        "--max_workers", str(max_workers),
        "--run_id", run_id,
    ]


def run_eval(dataset_name: str, predictions: str | Path, run_id: str, *, max_workers: int = 1, check: bool = False):
    cmd = swebench_eval_command(dataset_name, predictions, run_id, max_workers=max_workers)
    return subprocess.run(cmd, text=True, check=check)


def validate_grading_report(report: dict, instance_logs: Path) -> dict:
    """Accept evidenced patch rejection as unresolved; stop on unexplained errors."""
    if report.get("incomplete_ids") or report.get("infra_failure_instances"):
        raise ValueError("Official grading has incomplete tasks or infrastructure failures")
    errors = report.get("error_ids", [])
    if report.get("error_instances", 0) != len(errors):
        raise ValueError("Official grading error count differs from its IDs")
    rejected = {}
    for task_id in errors:
        if task_id in report.get("resolved_ids", []):
            raise ValueError("A task cannot be both resolved and a grading error")
        path = instance_logs / task_id / "run_instance.log"
        content = path.read_bytes() if path.exists() else b""
        if b" - INFO - >>>>> Patch Apply Failed:" not in content:
            raise ValueError(f"Unexplained grading error: {task_id}")
        rejected[task_id] = {"classification": "patch_apply_failure", "resolved": False,
                             "log_sha256": hashlib.sha256(content).hexdigest()}
    return rejected
