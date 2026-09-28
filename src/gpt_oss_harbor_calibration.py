"""Run frozen no-memory July calibration once per task and inspect Harbor results."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from .gpt_oss_harbor_prepare import ROOT, _sha256_file, harbor_command, load_calibration_tasks
from .model_profiles import resolve_profile

STAGED = ROOT / "results" / "gpt_oss_staged" / "calibration"
JOBS = ROOT / "results" / "harbor_jobs_host"
CONFIG = ROOT / "config" / "harbor_gpt_oss.yaml"


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def prepared_command(task_id: str, *, allow_existing_job: bool = False) -> tuple[list[str], Path]:
    """Validate the immutable staging record before any paid call."""
    stage_dir = STAGED / task_id / "no_memory"
    policy = _read(stage_dir / "run_policy.json")
    command = _read(stage_dir / "harbor_command.json")
    if policy != {
        "phase": "calibration", "task_id": task_id, "condition": "no_memory",
        "agent_config_sha256": _sha256_file(CONFIG), "step_limit": 100,
        "one_attempt": True, "unfinished_is_unresolved": True,
        "text_only_submission_fallback": False,
    }:
        raise ValueError(f"Run policy changed for {task_id}")
    if not isinstance(command, list) or not all(isinstance(x, str) for x in command):
        raise ValueError(f"Malformed Harbor command for {task_id}")
    if command.count("--job-name") != 1 or command.count("--extra-instruction-path") != 1:
        raise ValueError(f"Harbor command lacks one job or instruction path for {task_id}")
    job_name = command[command.index("--job-name") + 1]
    instruction = Path(command[command.index("--extra-instruction-path") + 1])
    if job_name != f"gptoss-calibration-{task_id}-no_memory":
        raise ValueError(f"Unexpected job name for {task_id}")
    if instruction != stage_dir / "instruction.txt":
        raise ValueError(f"Unexpected instruction path for {task_id}")
    if "[none]" not in instruction.read_text(encoding="utf-8"):
        raise ValueError(f"No-memory instruction changed for {task_id}")
    expected = harbor_command(
        task_id, "no_memory", instruction,
        model_name=resolve_profile("gpt_oss_20b")["model_name"],
        job_name=job_name,
        harbor_bin=ROOT / "results" / "harbor_host_env" / "Scripts" / "harbor.exe",
    )
    if command != expected:
        raise ValueError(f"Harbor command changed for {task_id}")
    job_dir = JOBS / job_name
    if job_dir.exists() and not allow_existing_job:
        raise FileExistsError(f"Refusing a second attempt for {task_id}: {job_dir}")
    return command, job_dir


def summarize_completed(task_id: str, task: dict, job_dir: Path) -> dict:
    """Count agent failures as unresolved; fail on broken benchmark infrastructure."""
    job = _read(job_dir / "result.json")
    stats = job["stats"]
    if (job["n_total_trials"] != 1 or stats["n_completed_trials"] != 1 or
            stats["n_errored_trials"] != 0 or stats["n_retries"] != 0):
        raise ValueError(f"Harbor did not complete exactly one clean trial: {task_id}")
    trial_dirs = [p for p in job_dir.iterdir() if p.is_dir()]
    if len(trial_dirs) != 1:
        raise ValueError(f"Expected one trial directory for {task_id}")
    trial_dir = trial_dirs[0]
    result = _read(trial_dir / "result.json")
    trajectory_path = trial_dir / "agent" / "mini-swe-agent.trajectory.json"
    trajectory = _read(trajectory_path)
    if result["task_name"] != f"ibragim-badertdinov/{task_id}":
        raise ValueError(f"Harbor ran the wrong task: {task_id}")
    if result["exception_info"] is not None:
        raise ValueError(f"Trial infrastructure exception for {task_id}")
    reward = result["verifier_result"]["rewards"]["reward"]
    if reward not in (0, 1, 0.0, 1.0):
        raise ValueError(f"Unexpected verifier reward for {task_id}: {reward}")
    info = trajectory["info"]
    submission = info.get("submission") or ""
    submitted = info["exit_status"] == "Submitted" and bool(submission)
    if reward and not submitted:
        raise ValueError(f"Verifier rewarded an unsubmitted task: {task_id}")
    messages = trajectory["messages"]
    errors = [
        message for message in messages
        if message.get("role") == "user" and (
            "Tool call error:" in str(message.get("content", "")) or
            "output token limit" in str(message.get("content", ""))
        )
    ]
    repairs = [
        item["kind"]
        for message in messages if message.get("role") == "assistant"
        for item in message.get("extra", {}).get("response", {}).get("agent_tool_repairs", [])
    ]
    started = datetime.fromisoformat(result["started_at"])
    finished = datetime.fromisoformat(result["finished_at"])
    return {
        "task_id": task_id, "repo": task["repo"], "language": task["language"],
        "resolved": bool(reward), "official_reward": reward,
        "exit_status": info["exit_status"], "submitted": submitted,
        "model_calls": info["model_stats"]["api_calls"],
        "valid_tool_calls": sum(m.get("role") == "assistant" for m in messages),
        "format_errors": len(errors), "repair_kinds": repairs,
        "input_tokens": result["agent_result"]["n_input_tokens"],
        "output_tokens": result["agent_result"]["n_output_tokens"],
        "wall_seconds": round((finished - started).total_seconds(), 3),
        "patch_sha256": hashlib.sha256(submission.encode("utf-8")).hexdigest() if submission else None,
        "trial_id": trial_dir.name,
        "trajectory_path": str(trajectory_path.relative_to(ROOT)),
        "trajectory_sha256": _sha256_file(trajectory_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", action="append", help="Frozen calibration ID; default all 12")
    parser.add_argument("--run", action="store_true", help="Make the model-backed calls")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    tasks = load_calibration_tasks()
    task_ids = args.task_id or list(tasks)
    if len(set(task_ids)) != len(task_ids) or any(task_id not in tasks for task_id in task_ids):
        raise SystemExit("Duplicate or non-calibration task ID")
    prepared = [prepared_command(task_id) for task_id in task_ids]
    if not args.run:
        print(json.dumps({"ready": task_ids, "step_limit": 100, "attempts_per_task": 1}))
        return
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONUTF8": "1",
           "PYTHONIOENCODING": "utf-8"}
    for task_id, (command, job_dir) in zip(task_ids, prepared, strict=True):
        print(json.dumps({"starting": task_id}), flush=True)
        completed = subprocess.run(command, cwd=ROOT, env=env, check=False)
        if completed.returncode:
            raise SystemExit(f"Harbor command failed for {task_id} (exit {completed.returncode})")
        print(json.dumps(summarize_completed(task_id, tasks[task_id], job_dir)), flush=True)


if __name__ == "__main__":
    main()
