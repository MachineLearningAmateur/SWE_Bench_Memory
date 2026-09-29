"""Run a predeclared, post-hoc flat-RAG/graph replay on calibration tasks.

This is not the stopped three-arm pilot. Existing no-memory trials are reused
for descriptive pairing; no completed task/arm is retried.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from dotenv import load_dotenv

from .bootstrap import bootstrap_memory
from .gpt_oss_calibration_report import collect as collect_calibration
from .gpt_oss_harbor_calibration import JOBS, summarize_completed
from .gpt_oss_harbor_prepare import (
    FROZEN, MEMORY_DB_SHA256, ROOT, _sha256_file, load_calibration_tasks,
    memory_settings, stage,
)
from .model_profiles import resolve_profile

PLAN = FROZEN / "posthoc_memory_replay_plan.json"
STAGED = ROOT / "results" / "gpt_oss_staged" / "replay"
CONFIG = ROOT / "config" / "harbor_gpt_oss.yaml"
GRAPH_POLICY = ROOT / "config" / "graph_v2.yaml"


def checked_plan() -> tuple[dict, dict[str, dict]]:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    tasks = load_calibration_tasks()
    first_by_language = [
        next(task_id for task_id, task in tasks.items() if task["language"] == language)
        for language in ("python", "typescript", "go")
    ]
    if plan["label"] != "posthoc_exploratory_memory_replay":
        raise ValueError("Unexpected replay plan")
    if plan["task_ids"] != first_by_language:
        raise ValueError("Replay task selection changed")
    schedule = [tuple(pair) for pair in plan["new_arm_order"]]
    expected = {(task_id, arm) for task_id in first_by_language for arm in ("flat_rag", "graph")}
    if len(schedule) != 6 or set(schedule) != expected:
        raise ValueError("Replay schedule is not exactly three complete pairs")
    if (plan["attempts_per_new_arm"] != 1 or plan["step_limit"] != 100
            or plan["text_only_submission_fallback"] is not False):
        raise ValueError("Replay run policy changed")
    if _sha256_file(CONFIG) != plan["agent_config_sha256"]:
        raise ValueError("Agent config differs from replay plan")
    if _sha256_file(GRAPH_POLICY) != plan["graph_policy_sha256"]:
        raise ValueError("Graph policy differs from replay plan")
    if plan["memory_corpus_sha256"] != MEMORY_DB_SHA256:
        raise ValueError("Replay plan names a different memory corpus")
    calibration = collect_calibration()
    if calibration["completed_tasks"] != 12 or calibration["gate"] != "floor_stop":
        raise ValueError("Expected completed floor-stop calibration")
    return plan, tasks


def prepare(*, dry_run: bool = False, allow_completed: bool = False) -> list[dict]:
    """Stage exact contexts once and reject altered or existing attempts."""
    plan, tasks = checked_plan()
    db = bootstrap_memory() / "data" / "flat_rag.sqlite"
    db_hash = _sha256_file(db)
    if db_hash != MEMORY_DB_SHA256:
        raise ValueError("Historical memory SQLite differs from frozen corpus")
    settings = memory_settings()
    profile = resolve_profile("gpt_oss_20b")
    harbor_bin = ROOT / "results" / "harbor_host_env" / "Scripts" / "harbor.exe"
    if not harbor_bin.exists():
        raise FileNotFoundError("Harbor host installation is missing")
    summaries: list[dict] = []
    for task_id, arm in plan["new_arm_order"]:
        summary = stage(
            tasks[task_id], arm, db=db, db_sha256=db_hash, settings=settings,
            model_name=profile["model_name"], harbor_bin=harbor_bin,
            output_root=STAGED, phase="replay",
        )
        if summary["context_chars"] == 0 or summary["selected_count"] == 0:
            raise ValueError(f"Empty retrieval for {task_id}/{arm}")
        job_dir = JOBS / summary["job_name"]
        if job_dir.exists():
            if not allow_completed:
                raise FileExistsError(f"Refusing second attempt for {task_id}/{arm}: {job_dir}")
            # A completed clean trial may be skipped after process interruption;
            # an incomplete or errored job must be inspected, never retried.
            summarize_completed(task_id, tasks[task_id], job_dir)
            summary["already_completed"] = True
        if dry_run and not summary.get("already_completed"):
            env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONUTF8": "1",
                   "PYTHONIOENCODING": "utf-8"}
            check = subprocess.run(summary["command"] + ["--dry-run"], cwd=ROOT,
                                   env=env, capture_output=True, text=True, check=False)
            if check.returncode:
                raise RuntimeError(f"Harbor dry-run failed for {task_id}/{arm}: {check.stderr[-500:]}")
            summary["harbor_dry_run_passed"] = True
        summary.pop("command")
        summaries.append(summary)
    for task_id in plan["task_ids"]:
        flat = json.loads((STAGED / task_id / "flat_rag" / "retrieval.json").read_text(encoding="utf-8"))
        graph = json.loads((STAGED / task_id / "graph" / "retrieval.json").read_text(encoding="utf-8"))
        if (flat["metadata"]["common_seed_chunk_ids"] != graph["metadata"]["common_seed_chunk_ids"]
                or flat["context_sha256"] == graph["context_sha256"]
                or graph["metadata"]["graph_neighbors_added"] < 1):
            raise ValueError(f"Flat/graph exposure check failed for {task_id}")
    return summaries


def collect_report() -> dict:
    """Read-only paired summary; never interprets incomplete arms as failures."""
    plan, tasks = checked_plan()
    calibration = collect_calibration()
    baselines = {row["task_id"]: row for row in calibration["tasks"]}
    rows: list[dict] = []
    pending: list[list[str]] = []
    for task_id in plan["task_ids"]:
        arms = {"no_memory": baselines[task_id]}
        exposure = {}
        for arm in ("flat_rag", "graph"):
            retrieval = json.loads((STAGED / task_id / arm / "retrieval.json").read_text(encoding="utf-8"))
            exposure[arm] = {
                "context_chars": retrieval["context_chars"],
                "context_sha256": retrieval["context_sha256"],
                "selected_count": len(retrieval["selected"]),
                "common_seed_chunk_ids": retrieval["metadata"]["common_seed_chunk_ids"],
                "graph_neighbors_added": retrieval["metadata"].get("graph_neighbors_added", 0),
                "relations_followed": retrieval["metadata"].get("relations_followed", {}),
            }
            job_dir = JOBS / f"gptoss-replay-{task_id}-{arm}"
            result_path = job_dir / "result.json"
            if not result_path.exists() or not json.loads(result_path.read_text(encoding="utf-8")).get("finished_at"):
                pending.append([task_id, arm])
            else:
                arms[arm] = summarize_completed(task_id, tasks[task_id], job_dir)
        rows.append({"task_id": task_id, "language": tasks[task_id]["language"],
                     "arms": arms, "retrieval_exposure": exposure})
    counts = {}
    for arm in ("no_memory", "flat_rag", "graph"):
        outcomes = [row["arms"][arm] for row in rows if arm in row["arms"]]
        counts[arm] = {
            "completed": len(outcomes),
            "resolved": sum(row["resolved"] for row in outcomes),
            "raw_verifier_reward": sum(row["official_reward"] for row in outcomes),
            "submit_command_issued": sum(row["submit_command_issued"] for row in outcomes),
            "nonempty_submissions": sum(row["submitted"] for row in outcomes),
        }
    return {
        "label": plan["label"], "source_partition": "calibration",
        "task_ids": plan["task_ids"], "planned_new_trials": len(plan["new_arm_order"]),
        "completed_new_trials": len(plan["new_arm_order"]) - len(pending), "pending_pairs": pending,
        "arm_counts": counts, "tasks": rows,
        "interpretation": plan["interpretation"],
    }


def run() -> None:
    # Stage/preflight all six before the first model call. The written staging
    # files are immutable: stage() refuses to overwrite changed instructions,
    # retrieval records, commands, or run policies.
    summaries = prepare(dry_run=True, allow_completed=True)
    tasks = load_calibration_tasks()
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONUTF8": "1",
           "PYTHONIOENCODING": "utf-8"}
    for summary in summaries:
        task_id, arm = summary["task_id"], summary["condition"]
        if summary.get("already_completed"):
            print(json.dumps({"skipping_completed": task_id, "condition": arm}), flush=True)
            continue
        command = json.loads((STAGED / task_id / arm / "harbor_command.json").read_text(encoding="utf-8"))
        job_dir = JOBS / summary["job_name"]
        if job_dir.exists():
            raise FileExistsError(f"Refusing second attempt for {task_id}/{arm}: {job_dir}")
        print(json.dumps({"starting": task_id, "condition": arm}), flush=True)
        completed = subprocess.run(command, cwd=ROOT, env=env, check=False)
        if completed.returncode:
            raise SystemExit(f"Harbor failed for {task_id}/{arm} (exit {completed.returncode})")
        row = summarize_completed(task_id, tasks[task_id], job_dir)
        print(json.dumps({**row, "condition": arm}), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true", help="Stage and dry-run six tasks; no model calls")
    mode.add_argument("--run", action="store_true", help="Run the six frozen model-backed arms")
    mode.add_argument("--report", action="store_true", help="Read-only paired result summary")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    if args.prepare:
        print(json.dumps(prepare(dry_run=True), indent=2))
    elif args.report:
        print(json.dumps(collect_report(), indent=2))
    else:
        run()


if __name__ == "__main__":
    main()
