"""Summarize frozen July no-memory calibration from completed Harbor trials."""

from __future__ import annotations

import json
from collections import Counter

from .gpt_oss_harbor_calibration import JOBS, summarize_completed
from .gpt_oss_harbor_prepare import DATASET, load_calibration_tasks


def gate_for_resolved(resolved: int, total: int = 12) -> str:
    if total != 12 or not 0 <= resolved <= total:
        raise ValueError("Calibration gate requires all 12 tasks")
    if resolved <= 2:
        return "floor_stop"
    if resolved <= 9:
        return "pilot_eligible_if_graph_frozen"
    return "ceiling_stop"


def collect() -> dict:
    tasks = load_calibration_tasks()
    rows: list[dict] = []
    pending: list[str] = []
    for task_id, task in tasks.items():
        job_dir = JOBS / f"gptoss-calibration-{task_id}-no_memory"
        job_result = job_dir / "result.json"
        if not job_result.exists() or not json.loads(
            job_result.read_text(encoding="utf-8")
        ).get("finished_at"):
            pending.append(task_id)
            continue
        rows.append(summarize_completed(task_id, task, job_dir))
    resolved = sum(row["resolved"] for row in rows)
    return {
        "dataset": DATASET,
        "partition": "calibration",
        "condition": "no_memory",
        "planned_tasks": 12,
        "completed_tasks": len(rows),
        "pending_task_ids": pending,
        "resolved": resolved,
        "official_reward_sum": sum(row["official_reward"] for row in rows),
        "rewarded_without_submission": sum(row["rewarded_without_submission"] for row in rows),
        "submit_command_issued": sum(row["submit_command_issued"] for row in rows),
        "nonempty_submissions": sum(row["submitted"] for row in rows),
        "gate": gate_for_resolved(resolved) if not pending else "pending",
        "exit_status_counts": dict(Counter(row["exit_status"] for row in rows)),
        "total_format_errors": sum(row["format_errors"] for row in rows),
        "tasks": rows,
    }


if __name__ == "__main__":
    print(json.dumps(collect(), indent=2))
