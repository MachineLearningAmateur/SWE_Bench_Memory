import pytest

from src.openrouter_profile import PROFILE, relabel_record, resolve_openrouter_profile


def test_profile_defaults_to_qwen3_32b_pinned_to_deepinfra():
    profile = resolve_openrouter_profile(environ={"OPENROUTER_API_KEY": "secret"})
    assert profile["profile_name"] == PROFILE
    assert profile["model_class"] == "litellm"
    assert profile["model_name"] == "openrouter/qwen/qwen3-32b"
    assert profile["deployment"] == "qwen/qwen3-32b"
    assert profile["step_limit"] == 100
    kwargs = profile["model_kwargs"]
    assert kwargs["api_base"] == "https://openrouter.ai/api/v1"
    assert kwargs["api_key"] == "secret"
    assert (kwargs["temperature"], kwargs["top_p"], kwargs["max_tokens"]) == (0.6, 0.95, 8192)
    assert kwargs["extra_body"] == {"top_k": 20,
                                    "provider": {"require_parameters": False,
                                                 "order": ["deepinfra"],
                                                 "allow_fallbacks": False}}


def test_profile_requires_only_the_openrouter_key():
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        resolve_openrouter_profile(environ={"AZURE_API_KEY": "azure-secret"})


def test_environment_overrides_model_base_and_providers():
    env = {"OPENROUTER_API_KEY": "secret", "OPENROUTER_MODEL": "arcee-ai/trinity-large-thinking",
           "OPENROUTER_API_BASE": "https://proxy.example/v1", "OPENROUTER_PROVIDERS": "arcee-ai"}
    profile = resolve_openrouter_profile(environ=env)
    assert profile["model_name"] == "openrouter/arcee-ai/trinity-large-thinking"
    assert profile["model_kwargs"]["api_base"] == "https://proxy.example/v1"
    assert profile["model_kwargs"]["extra_body"]["provider"]["order"] == ["arcee-ai"]
    env["OPENROUTER_PROVIDERS"] = "any"
    routing = resolve_openrouter_profile(environ=env)["model_kwargs"]["extra_body"]["provider"]
    assert routing == {"require_parameters": False}


def test_relabel_record_drops_azure_deployment():
    profile = resolve_openrouter_profile(environ={"OPENROUTER_API_KEY": "secret"})
    record = relabel_record({"instance_id": "x", "model_profile": "gpt_5_1_codex_mini",
                             "azure_deployment": "gpt-5.1-codex-mini"}, profile)
    assert record == {"instance_id": "x", "model_profile": PROFILE, "azure_deployment": None,
                      "provider": "openrouter", "openrouter_model": "qwen/qwen3-32b"}


def test_use_openrouter_replaces_experiment_profile(monkeypatch):
    pytest.importorskip("minisweagent")
    from src import experiment
    from src.openrouter_profile import use_openrouter

    monkeypatch.setenv("OPENROUTER_API_KEY", "secret")
    monkeypatch.setattr(experiment, "resolve_profile", experiment.resolve_profile)
    use_openrouter()
    assert experiment.resolve_profile()["model_name"] == "openrouter/qwen/qwen3-32b"


def test_smoke_tool_matches_agent_tool():
    pytest.importorskip("minisweagent")
    from src.gpt_oss_tool_model import STRICT_BASH_TOOL
    from src.openrouter_smoke import BASH_TOOL

    assert BASH_TOOL == STRICT_BASH_TOOL
