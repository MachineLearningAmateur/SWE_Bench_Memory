import sys
import types

import pytest

from src import openrouter_failure50 as runner
from src.failure_corpus import digest, read_json, write_json

OVERRIDES = {"agent": {"step_limit": 100}, "model": {"model_class": "x"}}
needs_source = pytest.mark.skipif(not (runner.SOURCE / "manifest.json").exists(),
                                  reason="prepared Failure50 experiment is not restored")


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "src.verified_batch", types.SimpleNamespace(OVERRIDES=OVERRIDES))
    monkeypatch.setattr(runner, "version", lambda _name: "test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret")
    monkeypatch.delenv("OPENROUTER_PROVIDERS", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    out = tmp_path / "run"
    runner.prepare(out, runner.SOURCE, ["bm25", "graph_rag"], "tool")
    return out


@needs_source
def test_prepare_copies_frozen_trace_bundles_for_chosen_arms(prepared):
    m = runner.validate(prepared)
    source = read_json(runner.SOURCE / "manifest.json")
    frozen = read_json(runner.SOURCE / "contexts_manifest.json")["files"]
    assert m["conditions"] == ["bm25", "graph_rag"]
    assert m["trial_order"] == [t for t in source["trial_order"] if t["condition"] in {"bm25", "graph_rag"}]
    assert len(m["trial_order"]) == 20
    assert m["contexts_sha256"] == {rel: frozen[rel] for rel in m["contexts_sha256"]}
    assert m["trace_source_commit"] == source["source_commit"]
    assert m["model_name"] == "openrouter/qwen/qwen3-32b"
    assert "api_key" not in m["model_settings"]
    assert read_json(prepared / "status.json")["state"] == "prepared"


@needs_source
def test_validate_rejects_a_changed_bundle(prepared):
    rel = next(iter(read_json(prepared / "manifest.json")["contexts_sha256"]))
    bundle = read_json(prepared / rel)
    bundle["context"] += " changed"
    write_json(prepared / rel, bundle)
    with pytest.raises(ValueError, match="Frozen context changed"):
        runner.validate(prepared)


@needs_source
def test_prepare_rejects_unknown_condition(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "src.verified_batch", types.SimpleNamespace(OVERRIDES=OVERRIDES))
    with pytest.raises(ValueError, match="Unknown Failure50 conditions: graph"):
        runner.prepare(tmp_path / "run", runner.SOURCE, ["graph"])


def test_text_overrides_use_backticks_templates_and_batch_limits():
    pytest.importorskip("minisweagent")
    overrides = runner.agent_overrides("text")
    assert overrides["model"]["model_class"] == runner.TEXT_MODEL_CLASS
    assert overrides["model"]["cost_tracking"] == "ignore_errors"
    assert "model_name" not in overrides["model"] and "model_kwargs" not in overrides["model"]
    assert "mswea_bash_command" in overrides["agent"]["system_template"]
    assert (overrides["agent"]["step_limit"], overrides["agent"]["cost_limit"],
            overrides["agent"]["wall_time_limit_seconds"]) == (100, 0, 3600)


def test_text_model_gets_profile_settings_without_tool_params(monkeypatch, tmp_path):
    pytest.importorskip("minisweagent")
    from minisweagent.models import get_model
    from minisweagent.utils.serialize import recursive_merge

    from src import experiment
    from src.openrouter_profile import use_openrouter

    monkeypatch.setenv("OPENROUTER_API_KEY", "secret")
    monkeypatch.setattr(experiment, "resolve_profile", experiment.resolve_profile)
    use_openrouter()
    cfg = recursive_merge(experiment._agent_config(experiment.load_config(), tmp_path / "t.json"),
                          runner.agent_overrides("text"))
    model = get_model(config=cfg["model"])
    assert type(model).__name__ == "TextbasedLitellmModel"
    assert model.config.model_name == "openrouter/qwen/qwen3-32b"
    kwargs = model.config.model_kwargs
    assert not {"parallel_tool_calls", "tools", "tool_choice"} & set(kwargs)
    assert (kwargs["temperature"], kwargs["max_tokens"], kwargs["extra_body"]["top_k"]) == (0.6, 8192, 20)
    assert model._parse_actions(types.SimpleNamespace(choices=[types.SimpleNamespace(
        message=types.SimpleNamespace(content="THOUGHT: look\n\n```mswea_bash_command\nls\n```"),
        finish_reason="stop")])) == [{"command": "ls"}]


def test_format_error_count_reads_the_interrupt_marker():
    trajectory = {"messages": [{"role": "user", "content": "Format error", "extra": {"interrupt_type": "FormatError"}},
                               {"role": "user", "content": "Tool call error:", "extra": {"interrupt_type": "FormatError"}},
                               {"role": "tool", "content": "ok", "extra": {}}, {"role": "system", "content": "x"}]}
    assert runner.format_error_count(trajectory) == 2


def test_deepinfra_overflow_through_openrouter_counts_as_context_limit():
    message = ("litellm.BadRequestError: OpenrouterException - Upstream error from DeepInfra: This model's "
               "maximum context length is 40960 tokens. However, you requested 14746 output tokens")
    assert runner.is_context_limit({"type": "BadRequestError", "message": message})
    assert runner.is_context_limit({"type": "ContextWindowExceededError", "message": ""})
    assert not runner.is_context_limit({"type": "BadRequestError", "message": "Invalid tool schema"})
    assert not runner.is_context_limit(None)


def test_completed_prefix_accepts_context_overflow_but_not_other_errors(tmp_path):
    trials = [{"instance_id": "a", "condition": "bm25"}, {"instance_id": "b", "condition": "bm25"}]
    records = [{**trials[0], "error": None},
               {**trials[1], "error": {"type": "ContextWindowExceededError", "message": "too long"}}]
    for record in records:
        path = tmp_path / "runs" / record["condition"] / record["instance_id"] / "run.json"
        path.parent.mkdir(parents=True)
        write_json(path, record)
    write_json(tmp_path / "records.json", records)
    assert runner.completed_prefix(tmp_path, {"trial_order": trials}) == records
    records[1]["error"]["type"] = "APIConnectionError"
    write_json(tmp_path / "records.json", records)
    write_json(tmp_path / "runs/bm25/b/run.json", records[1])
    with pytest.raises(ValueError, match="clean completed prefix"):
        runner.completed_prefix(tmp_path, {"trial_order": trials})
