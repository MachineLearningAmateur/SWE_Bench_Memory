"""The gpt-oss harness must request the tool contract its parser expects."""

import importlib.util
import subprocess
import sys
from textwrap import dedent

import pytest


def run_with_agent_package(code: str):
    if importlib.util.find_spec("minisweagent") is None:
        pytest.skip("mini-SWE-agent is installed only in the research container")
    # Keep minisweagent out of the test process: the exposure audit checks that
    # simply importing repo modules never loads agent/benchmark dependencies.
    result = subprocess.run([sys.executable, "-c", dedent(code)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_strict_bash_request_overrides_optional_tool_choice():
    run_with_agent_package("""
        from src import gpt_oss_tool_model as module

        captured = {}

        def fake_completion(**kwargs):
            captured.update(kwargs)
            return object()

        module.litellm.completion = fake_completion
        model = module.StrictToolLitellmModel(
            model_name="openai/test-deployment",
            model_kwargs={"tool_choice": "auto", "temperature": 0},
        )
        model._query([{"role": "user", "content": "test"}])

        assert captured["tool_choice"] == "required"
        assert captured["parallel_tool_calls"] is False
        assert captured["temperature"] == 0
        assert captured["tools"] == [module.STRICT_BASH_TOOL]
        bash = captured["tools"][0]["function"]
        assert bash["name"] == "bash" and bash["strict"] is True
        assert bash["parameters"]["required"] == ["command"]
        assert bash["parameters"]["additionalProperties"] is False
    """)


def test_observed_patch_typo_is_repaired_and_original_is_audited():
    run_with_agent_package("""
        import json
        from src import gpt_oss_tool_model as module

        command = "apply_patch <<'PATCH'\\n*** Begin Patch\\n*** End Patch\\nPATCH"
        original = json.dumps({"command": command})[:-1] + "]}"
        def fake_completion(**kwargs):
            return module.litellm.ModelResponse(
                model="openai/test-deployment",
                choices=[{"index": 0, "finish_reason": "tool_calls", "message": {
                    "role": "assistant", "content": None,
                    "tool_calls": [{"id": "call_1", "type": "function", "function": {
                        "name": "bash", "arguments": original,
                    }}],
                }}],
            )

        module.litellm.completion = fake_completion
        model = module.RepairingToolLitellmModel(
            model_name="openai/test-deployment", cost_tracking="ignore_errors",
        )
        message = model.query([{"role": "user", "content": "test"}])
        assert message["extra"]["actions"][0]["command"] == command
        stored = message["extra"]["response"]
        repairs = stored["agent_tool_repairs"]
        assert repairs[0]["original_arguments"] == original
        assert json.loads(repairs[0]["repaired_arguments"]) == {"command": command}
        assert stored["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] == repairs[0]["repaired_arguments"]
    """)


def test_unobserved_errors_are_not_repaired():
    run_with_agent_package("""
        import json
        from src.gpt_oss_tool_model import repair_observed_patch_arguments

        patch = "apply_patch <<'PATCH'\\n*** Begin Patch\\n*** End Patch\\nPATCH"
        valid = json.dumps({"command": patch})
        assert repair_observed_patch_arguments(valid) is None
        assert repair_observed_patch_arguments('{"command":"echo hi"]}') is None
        assert repair_observed_patch_arguments(json.dumps({"command": patch, "extra": 1})[:-1] + "]}") is None
        assert repair_observed_patch_arguments('{"command":"' + patch + '"]}') is None
        assert repair_observed_patch_arguments('{"command":"' + patch + '"}') is None
    """)


def test_unknown_tool_is_not_repaired():
    run_with_agent_package("""
        import json
        from src import gpt_oss_tool_model as module

        command = "apply_patch <<'PATCH'\\n*** Begin Patch\\n*** End Patch\\nPATCH"
        original = json.dumps({"command": command})[:-1] + "]}"
        def fake_completion(**kwargs):
            return module.litellm.ModelResponse(
                model="openai/test-deployment",
                choices=[{"index": 0, "finish_reason": "tool_calls", "message": {
                    "role": "assistant", "content": None,
                    "tool_calls": [{"id": "call_1", "type": "function", "function": {
                        "name": "bash<|channel|>commentary", "arguments": original,
                    }}],
                }}],
            )

        module.litellm.completion = fake_completion
        model = module.RepairingToolLitellmModel(model_name="openai/test-deployment")
        response = model._query([{"role": "user", "content": "test"}])
        assert response.choices[0].message.tool_calls[0].function.arguments == original
        assert not response.model_dump().get("agent_tool_repairs")
    """)


def test_observed_channel_suffix_is_repaired_and_logged():
    run_with_agent_package("""
        import json
        from src import gpt_oss_tool_model as module

        arguments = json.dumps({"command": "pwd"})
        def fake_completion(**kwargs):
            return module.litellm.ModelResponse(
                model="openai/test-deployment",
                choices=[{"index": 0, "finish_reason": "tool_calls", "message": {
                    "role": "assistant", "content": None,
                    "tool_calls": [{"id": "call_1", "type": "function", "function": {
                        "name": "bash<|channel|>commentary", "arguments": arguments,
                    }}],
                }}],
            )

        module.litellm.completion = fake_completion
        model = module.RepairingToolLitellmModel(model_name="openai/test-deployment")
        response = model._query([{"role": "user", "content": "test"}])
        call = response.choices[0].message.tool_calls[0]
        assert call.function.name == "bash"
        assert call.function.arguments == arguments
        repair = response.model_dump()["agent_tool_repairs"][0]
        assert repair["kind"] == "channel_suffix_in_bash_name"
        assert repair["original_name"] == "bash<|channel|>commentary"
        assert repair["repaired_name"] == "bash"
        assert repair["original_arguments"] == arguments
    """)


def test_channel_suffix_repair_stays_fail_closed():
    run_with_agent_package("""
        import json
        from src.gpt_oss_tool_model import repair_observed_tool_name

        valid = json.dumps({"command": "pwd"})
        assert repair_observed_tool_name("bash<|channel|>commentary", valid) == "bash"
        assert repair_observed_tool_name("bash<|channel|>analysis", valid) is None
        assert repair_observed_tool_name("bash", valid) is None
        assert repair_observed_tool_name("bash<|channel|>commentary", '{"command":') is None
        assert repair_observed_tool_name("bash<|channel|>commentary", json.dumps({"command": ""})) is None
        assert repair_observed_tool_name("bash<|channel|>commentary", json.dumps({"command": "pwd", "other": 1})) is None
        assert repair_observed_tool_name("bash<|channel|>commentary", json.dumps({"command": ["pwd"]})) is None
    """)


def test_text_only_response_is_never_turned_into_a_tool_call():
    run_with_agent_package("""
        from src import gpt_oss_tool_model as module

        def fake_completion(**kwargs):
            return module.litellm.ModelResponse(
                model="openai/test-deployment",
                choices=[{"index": 0, "finish_reason": "stop", "message": {
                    "role": "assistant", "content": "I am done.", "tool_calls": None,
                }}],
            )

        module.litellm.completion = fake_completion
        model = module.RepairingToolLitellmModel(model_name="openai/test-deployment")
        response = model._query([{"role": "user", "content": "test"}])
        assert response.choices[0].message.content == "I am done."
        assert not response.choices[0].message.tool_calls
        assert not response.model_dump().get("agent_tool_repairs")
    """)
