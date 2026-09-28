"""Preparation must preserve the frozen July split and identical agent path."""

import json
from pathlib import Path

import pytest
import yaml

from src.gpt_oss_harbor_prepare import (
    CONDITIONS,
    _write_frozen,
    harbor_command,
    instruction_addendum,
    load_development_tasks,
    memory_settings,
    retrieve,
    stage,
)
from src.memory_conditions import MemoryResult


def test_frozen_harbor_run_policy_has_one_shared_bounded_agent():
    config = yaml.safe_load(Path("config/harbor_gpt_oss.yaml").read_text(encoding="utf-8"))
    assert config["agent"]["step_limit"] == 100
    assert config["agent"]["cost_limit"] == 0
    assert config["model"]["model_class"] == "gpt_oss_tool_model.RepairingToolLitellmModel"
    assert config["model"]["model_kwargs"]["reasoning_effort"] == "medium"


def test_only_frozen_development_tasks_are_loaded():
    tasks = load_development_tasks()
    assert len(tasks) == 30
    assert len({task["instance_id"] for task in tasks.values()}) == 30
    assert all(task["instance_id"] == task_id for task_id, task in tasks.items())


def test_frozen_graph_policy_and_no_memory_needs_no_database():
    settings = memory_settings()
    assert settings["graph_hops"] == 2 and settings["semantic_first"] is True
    assert settings["character_budget"] == 24000
    task = {"problem_statement": "Fix the bug", "repo": "owner/repo"}
    result = retrieve(task, "no_memory", Path("missing.sqlite"), settings)
    assert result.context == "" and result.selected == []


def test_instruction_wrapper_changes_only_memory_field():
    none = instruction_addendum("")
    graph = instruction_addendum("GRAPH_RELATION example")
    assert none.replace("[none]", "GRAPH_RELATION example") == graph
    assert "untrusted evidence" in none
    assert none.count("HISTORICAL MEMORY:") == 1


def test_all_conditions_use_same_harbor_agent_and_no_inline_key(tmp_path):
    commands = [harbor_command("Task__1", condition, tmp_path / condition / "instruction.txt",
                               model_name="openai/deploy", job_name=f"job-{condition}",
                               harbor_bin=tmp_path / "harbor.exe") for condition in CONDITIONS]
    assert len(commands) == 3
    for command in commands:
        assert command[command.index("--agent") + 1].endswith(":RepairingMiniSweAgent")
        assert command[command.index("--model") + 1] == "openai/deploy"
        assert command[command.index("--dataset") + 1].endswith("swe-rebench-07-2026@1")
        assert command[command.index("--env-file") + 1] == ".env"
        assert "--extra-instruction-path" in command
        assert not any("api_key=" in arg.lower() for arg in command)


def test_stage_preserves_exact_retrieval_and_refuses_changes(tmp_path, monkeypatch):
    from src import gpt_oss_harbor_prepare as module

    task = {"instance_id": "Task__1", "repo": "owner/repo", "problem_statement": "Fix the bug"}
    memory = MemoryResult("graph", task["problem_statement"], "GRAPH_RELATION example",
                          [{"chunk_id": "c1"}], {"retrieval": "graph"})
    monkeypatch.setattr(module, "retrieve", lambda *_args: memory)
    settings = {"character_budget": 24000}
    kwargs = dict(db=tmp_path / "db.sqlite", db_sha256="0" * 64, settings=settings,
                  model_name="openai/deploy", harbor_bin=tmp_path / "harbor.exe",
                  output_root=tmp_path / "staged")
    first = stage(task, "graph", **kwargs)
    second = stage(task, "graph", **kwargs)
    assert first == second
    saved = json.loads(Path(first["retrieval_path"]).read_text(encoding="utf-8"))
    assert saved["context"] == memory.context
    assert saved["selected"] == memory.selected
    assert saved["context_chars"] == len(memory.context)
    assert "GRAPH_RELATION example" in Path(first["instruction_path"]).read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="Refusing to overwrite"):
        _write_frozen(Path(first["instruction_path"]), "different")
