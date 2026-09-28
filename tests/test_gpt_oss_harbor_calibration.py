"""Calibration may count agent failures, but must never silently retry or switch arms."""

import hashlib
import json
from pathlib import Path

import pytest

from src import gpt_oss_harbor_calibration as module
from src.gpt_oss_harbor_prepare import harbor_command


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_prepared_command_is_exact_and_refuses_existing_attempt(tmp_path, monkeypatch):
    task_id = "example__task-1"
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "STAGED", tmp_path / "staged")
    monkeypatch.setattr(module, "JOBS", tmp_path / "jobs")
    monkeypatch.setattr(module, "CONFIG", tmp_path / "config.yaml")
    monkeypatch.setattr(module, "resolve_profile", lambda _: {"model_name": "openai/test"})
    module.CONFIG.write_text("agent:\n  step_limit: 100\n", encoding="utf-8")
    stage_dir = module.STAGED / task_id / "no_memory"
    instruction = stage_dir / "instruction.txt"
    instruction.parent.mkdir(parents=True)
    instruction.write_text("HISTORICAL MEMORY:\n[none]", encoding="utf-8")
    job_name = f"gptoss-calibration-{task_id}-no_memory"
    command = harbor_command(
        task_id, "no_memory", instruction, model_name="openai/test",
        job_name=job_name,
        harbor_bin=tmp_path / "results" / "harbor_host_env" / "Scripts" / "harbor.exe",
    )
    policy = {
        "phase": "calibration", "task_id": task_id, "condition": "no_memory",
        "agent_config_sha256": hashlib.sha256(module.CONFIG.read_bytes()).hexdigest(),
        "step_limit": 100, "one_attempt": True,
        "unfinished_is_unresolved": True, "text_only_submission_fallback": False,
    }
    _write_json(stage_dir / "run_policy.json", policy)
    _write_json(stage_dir / "harbor_command.json", command)
    assert module.prepared_command(task_id)[0] == command
    command[command.index("--model") + 1] = "openai/other"
    _write_json(stage_dir / "harbor_command.json", command)
    with pytest.raises(ValueError, match="command changed"):
        module.prepared_command(task_id)
    command[command.index("--model") + 1] = "openai/test"
    _write_json(stage_dir / "harbor_command.json", command)
    (module.JOBS / job_name).mkdir(parents=True)
    with pytest.raises(FileExistsError, match="second attempt"):
        module.prepared_command(task_id)


def test_limit_exit_is_unresolved_even_with_valid_actions(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "ROOT", tmp_path)
    task_id = "example__task-1"
    job_dir = tmp_path / "job"
    trial_dir = job_dir / (task_id + "__trial")
    _write_json(job_dir / "result.json", {
        "n_total_trials": 1,
        "stats": {"n_completed_trials": 1, "n_errored_trials": 0, "n_retries": 0},
    })
    _write_json(trial_dir / "result.json", {
        "task_name": "ibragim-badertdinov/" + task_id,
        "exception_info": None,
        "verifier_result": {"rewards": {"reward": 0.0}},
        "agent_result": {"n_input_tokens": 12, "n_output_tokens": 4},
        "started_at": "2026-09-28T00:00:00",
        "finished_at": "2026-09-28T00:00:10",
    })
    _write_json(trial_dir / "agent" / "mini-swe-agent.trajectory.json", {
        "info": {"exit_status": "LimitsExceeded", "submission": "",
                 "model_stats": {"api_calls": 100}},
        "messages": [{"role": "assistant", "extra": {"response": {}}}],
    })
    summary = module.summarize_completed(
        task_id, {"repo": "example/task", "language": "python"}, job_dir
    )
    assert summary["resolved"] is False
    assert summary["submitted"] is False
    assert summary["model_calls"] == 100
    assert summary["patch_sha256"] is None
