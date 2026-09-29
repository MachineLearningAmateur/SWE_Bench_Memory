"""The post-hoc replay is paired, bounded, and separate from the frozen pilot."""

import json

from src import gpt_oss_exploratory_replay as replay
from src.gpt_oss_harbor_prepare import load_calibration_tasks


def test_replay_plan_selects_first_calibration_task_per_language():
    plan = json.loads(replay.PLAN.read_text(encoding="utf-8"))
    tasks = load_calibration_tasks()
    expected = [
        next(task_id for task_id, task in tasks.items() if task["language"] == language)
        for language in ("python", "typescript", "go")
    ]
    assert plan["task_ids"] == expected
    assert len(plan["new_arm_order"]) == 6
    assert {tuple(pair) for pair in plan["new_arm_order"]} == {
        (task_id, arm) for task_id in expected for arm in ("flat_rag", "graph")
    }
    assert plan["attempts_per_new_arm"] == 1
    assert plan["step_limit"] == 100
    assert plan["text_only_submission_fallback"] is False


def test_replay_report_keeps_missing_arms_pending(tmp_path, monkeypatch):
    plan = {"label": "posthoc_exploratory_memory_replay",
            "task_ids": ["example__task-1"],
            "new_arm_order": [["example__task-1", "flat_rag"],
                              ["example__task-1", "graph"]],
            "interpretation": "descriptive only"}
    tasks = {"example__task-1": {"language": "python"}}
    baseline = {"task_id": "example__task-1", "resolved": False,
                "official_reward": 0.0, "submit_command_issued": False,
                "submitted": False}
    monkeypatch.setattr(replay, "checked_plan", lambda: (plan, tasks))
    monkeypatch.setattr(replay, "collect_calibration", lambda: {"tasks": [baseline]})
    monkeypatch.setattr(replay, "STAGED", tmp_path / "staged")
    monkeypatch.setattr(replay, "JOBS", tmp_path / "jobs")
    for arm in ("flat_rag", "graph"):
        path = replay.STAGED / "example__task-1" / arm / "retrieval.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "context_chars": 100, "context_sha256": arm, "selected": [{}],
            "metadata": {"common_seed_chunk_ids": ["s1"],
                         "graph_neighbors_added": 1 if arm == "graph" else 0},
        }), encoding="utf-8")
    report = replay.collect_report()
    assert report["completed_new_trials"] == 0
    assert len(report["pending_pairs"]) == 2
    assert report["arm_counts"]["no_memory"]["completed"] == 1
    assert report["arm_counts"]["graph"]["completed"] == 0
