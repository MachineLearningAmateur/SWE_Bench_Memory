"""Compatibility tests for the SWE-bench evaluation command and dataset config."""

from pathlib import Path

import yaml

from src import evaluate
from src.evaluate import swebench_eval_command

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_swebench_eval_command_contains_expected_arguments():
    cmd = swebench_eval_command(
        "SWE-bench/SWE-bench_Verified",
        "predictions.jsonl",
        "smoke_test",
        max_workers=1,
    )
    assert "SWE-bench/SWE-bench_Verified" in cmd
    assert "--predictions_path" in cmd
    assert "--max_workers" in cmd and cmd[cmd.index("--max_workers") + 1] == "1"
    assert "--run_id" in cmd and cmd[cmd.index("--run_id") + 1] == "smoke_test"
    assert "--cache_level" not in cmd
    assert "--clean" not in cmd


def test_swebench_eval_command_exact_shape():
    cmd = swebench_eval_command(
        "SWE-bench/SWE-bench_Verified",
        "/workspace/results/predictions_no_memory.jsonl",
        "pilot_no_memory",
        max_workers=1,
    )
    assert cmd == [
        "python", "-m", "swebench.harness.run_evaluation",
        "--dataset_name", "SWE-bench/SWE-bench_Verified",
        "--predictions_path", "/workspace/results/predictions_no_memory.jsonl",
        "--max_workers", "1",
        "--run_id", "pilot_no_memory",
    ]


def test_swebench_eval_command_default_max_workers_is_one():
    cmd = swebench_eval_command("SWE-bench/SWE-bench_Verified", "p.jsonl", "r")
    assert cmd[cmd.index("--max_workers") + 1] == "1"


def test_experiment_config_uses_new_verified_dataset():
    cfg = yaml.safe_load((REPO_ROOT / "config" / "experiment.yaml").read_text())
    assert cfg["pilot"]["dataset_name"] == "SWE-bench/SWE-bench_Verified"


def test_run_eval_builds_command_and_calls_subprocess(monkeypatch):
    captured = {}

    def fake_command(dataset_name, predictions, run_id, *, max_workers=1):
        captured["command"] = swebench_eval_command(
            dataset_name, predictions, run_id, max_workers=max_workers
        )
        return captured["command"]

    class _Completed:
        returncode = 0

    def fake_run(cmd, **kwargs):
        captured["run_cmd"] = cmd
        captured["run_kwargs"] = kwargs
        return _Completed()

    monkeypatch.setattr(evaluate, "swebench_eval_command", fake_command)
    monkeypatch.setattr(evaluate.subprocess, "run", fake_run)

    result = evaluate.run_eval(
        "SWE-bench/SWE-bench_Verified",
        "predictions_remaining_graph.jsonl",
        "smoke_remaining_graph",
        max_workers=1,
        check=False,
    )

    assert result.returncode == 0
    assert captured["command"] == [
        "python", "-m", "swebench.harness.run_evaluation",
        "--dataset_name", "SWE-bench/SWE-bench_Verified",
        "--predictions_path", "predictions_remaining_graph.jsonl",
        "--max_workers", "1",
        "--run_id", "smoke_remaining_graph",
    ]
    assert captured["run_cmd"] == captured["command"]
    assert captured["run_kwargs"].get("text") is True
    assert captured["run_kwargs"].get("check") is False

