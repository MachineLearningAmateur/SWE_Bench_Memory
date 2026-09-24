import json

import pytest

from src.gpt_oss_partitions import SOURCE, build_partitions, _source_tasks
from src.gpt_oss_smoke import chat_url, run_smoke
from src.model_profiles import resolve_profile


def test_frozen_partitions_match_snapshot_and_have_no_cross_arm_leakage():
    tasks = _source_tasks(SOURCE / "source_tasks.jsonl")
    dev = json.loads((SOURCE / "dev_task_ids.json").read_text(encoding="utf-8"))
    source = json.loads((SOURCE / "benchmark_source.json").read_text(encoding="utf-8"))
    out = build_partitions(tasks, dev, source)
    assert [len(out[k]) for k in ("calibration", "pilot", "reserve")] == [12, 24, 12]
    assert not ({t["instance_id"] for k in ("calibration", "pilot", "reserve") for t in out[k]} & set(dev))
    assert not ({t["repo"] for t in out["calibration"]} & {t["repo"] for t in out["pilot"]})
    assert out["manifest"]["partitions"]["pilot"]["languages"] == {"go": 8, "python": 8, "typescript": 8}


def test_profile_uses_separate_gpt_oss_env_and_per_run_limits():
    env = {"GPT_OSS_DEPLOYMENT": "oss-deploy", "GPT_OSS_API_BASE": "https://example.services.ai.azure.com/openai/v1/",
           "GPT_OSS_API_KEY": "secret", "GPT_OSS_LITELLM_MODEL_NAME": "verified/oss-deploy"}
    profile = resolve_profile("gpt_oss_20b", environ=env)
    assert profile["model_class"] == "litellm"
    assert profile["model_name"] == "verified/oss-deploy"
    assert profile["step_limit"] == 60
    assert "reasoning_effort" not in profile["model_kwargs"]
    env["GPT_OSS_REASONING_EFFORT_SUPPORTED"] = "1"
    assert resolve_profile("gpt_oss_20b", environ=env)["model_kwargs"]["reasoning_effort"] == "medium"
    with pytest.raises(ValueError, match="GPT_OSS_LITELLM_MODEL_NAME"):
        resolve_profile("gpt_oss_20b", environ={k: v for k, v in env.items() if k != "GPT_OSS_LITELLM_MODEL_NAME"})


def test_smoke_requires_native_tool_round_trip(monkeypatch):
    calls = []

    def fake_request(url, key, payload):
        calls.append(payload)
        if len(calls) <= 2:
            return {"choices": [{"message": {"content": "gpt-oss Azure works"}}]}, 0.1
        if len(calls) == 3:
            return {"choices": [{"message": {"content": None, "tool_calls": [{"id": "abc", "type": "function",
                "function": {"name": "get_magic_number", "arguments": "{}"}}]}}]}, 0.2
        return {"choices": [{"message": {"content": "42"}}]}, 0.1

    monkeypatch.setattr("src.gpt_oss_smoke._request", fake_request)
    result = run_smoke(deployment="oss", url="https://x/openai/v1/chat/completions", key="secret")
    assert result["structured_tool_call"] and result["tool_executed"] and result["final_answer"]
    assert calls[3]["messages"][-1] == {"role": "tool", "tool_call_id": "abc", "content": "42"}


def test_chat_url_respects_verified_route():
    assert chat_url("https://x/openai/v1/", "d") == "https://x/openai/v1/chat/completions"
    assert chat_url("https://x/managed-deployments/d/", "d") == "https://x/managed-deployments/d/chat/completions"
    with pytest.raises(ValueError):
        chat_url("https://x/random/", "d")
