"""The gpt-oss harness must request the tool contract its parser expects."""

import importlib.util
import subprocess
import sys
from textwrap import dedent

import pytest


def test_strict_bash_request_overrides_optional_tool_choice():
    if importlib.util.find_spec("minisweagent") is None:
        pytest.skip("mini-SWE-agent is installed only in the research container")
    # Keep minisweagent out of the test process: the exposure audit checks that
    # simply importing repo modules never loads agent/benchmark dependencies.
    code = dedent("""
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
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
