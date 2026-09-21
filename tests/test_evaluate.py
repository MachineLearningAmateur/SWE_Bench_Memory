"""Compatibility tests for the SWE-bench evaluation command and dataset config."""

from pathlib import Path

import yaml

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
