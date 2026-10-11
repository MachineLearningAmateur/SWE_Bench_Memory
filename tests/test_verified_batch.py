from collections import Counter

import pytest

from src.experiment import run_one
from src.verified_batch import CONDITIONS, trial_order, write_json, completed_records


def test_ten_tasks_get_exactly_one_attempt_per_condition():
    tasks = [{"instance_id": f"task-{i}"} for i in range(10)]
    trials = trial_order(tasks)
    assert len(trials) == 30
    assert len({(t["instance_id"], t["condition"]) for t in trials}) == 30
    assert Counter(t["condition"] for t in trials) == dict.fromkeys(CONDITIONS, 10)
    assert trial_order(tasks) == trials
    assert len({t["condition"] for t in trials[::3]}) == 3


def test_existing_attempt_is_not_overwritten(tmp_path):
    run_dir = tmp_path / "no_memory" / "task-0"
    run_dir.mkdir(parents=True)
    marker = run_dir / "trajectory.json"
    marker.write_text("original attempt")
    with pytest.raises(FileExistsError):
        run_one({"instance_id": "task-0"}, "no_memory", {}, root=tmp_path)
    assert marker.read_text() == "original attempt"


def test_status_is_atomically_replaced(tmp_path):
    path = tmp_path / "status.json"
    write_json(path, {"state": "prepared"})
    write_json(path, {"state": "running"})
    assert '"running"' in path.read_text()
    assert not path.with_suffix(".json.tmp").exists()


def test_resume_keeps_failed_model_attempts_in_completed_prefix(tmp_path):
    trial = {"instance_id": "task-0", "condition": "graph"}
    record = {**trial, "exit_status": "RepeatedFormatError", "model_patch": "", "error": None}
    directory = tmp_path / "runs" / "graph" / "task-0"
    directory.mkdir(parents=True)
    write_json(directory / "run.json", record)
    write_json(tmp_path / "records.json", [record])
    manifest = {"trial_order": [trial, {"instance_id": "task-1", "condition": "no_memory"}]}
    assert completed_records(tmp_path, manifest) == [record]
    write_json(directory / "run.json", {**record, "model_patch": "changed"})
    with pytest.raises(ValueError, match="differs"):
        completed_records(tmp_path, manifest)


def test_resume_rejects_nonprefix_records(tmp_path):
    write_json(tmp_path / "records.json", [{"instance_id": "task-2", "condition": "graph"}])
    with pytest.raises(ValueError, match="prefix"):
        completed_records(tmp_path, {"trial_order": [{"instance_id": "task-1", "condition": "graph"}]})


def test_prepared_memory_bypasses_old_corpus_and_reaches_solver(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from src import experiment
    from src.memory_conditions import MemoryResult

    captured = {}

    class Agent:
        def run(self, task):
            captured["task"] = task
            return {"exit_status": "Submitted", "submission": "patch"}

        def save(self, path, extra):
            path.write_text("{}")

    monkeypatch.setattr(experiment, "bootstrap_memory", lambda: pytest.fail("Legacy corpus was accessed"))
    monkeypatch.setattr(experiment, "_agent_config", lambda *args: {})
    monkeypatch.setattr(experiment, "get_model", lambda **kwargs: SimpleNamespace(config=SimpleNamespace(model_name="fixture")))
    monkeypatch.setattr(experiment, "get_sb_environment", lambda *args: SimpleNamespace(cleanup=lambda: None))
    monkeypatch.setattr(experiment, "get_agent", lambda *args, **kwargs: Agent())
    memory = MemoryResult("hybrid_rag", "new problem", "new evidence", [], {"memory_tokens": 2})
    record = experiment.run_one({"instance_id": "new-task", "problem_statement": "new problem"}, "hybrid_rag", {},
                                root=tmp_path, prepared_memory=memory, uniform_wrapper=True)
    assert "new evidence" in captured["task"]
    assert record["exit_status"] == "Submitted"
    assert record["model_patch"] == "patch"
