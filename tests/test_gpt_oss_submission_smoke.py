"""Prove mini-SWE-agent accepts the explicit submit marker without a model call."""

import importlib.util
import subprocess
import sys
from textwrap import dedent

import pytest


def test_explicit_bash_submit_marker_exits_agent_path():
    if importlib.util.find_spec("minisweagent") is None:
        pytest.skip("mini-SWE-agent is installed only in the research container")
    script = dedent(
        """
        from minisweagent.environments.local import LocalEnvironment
        from minisweagent.exceptions import Submitted

        env = LocalEnvironment()
        try:
            env.execute({"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"})
        except Submitted:
            pass
        else:
            raise AssertionError("Explicit submit marker was not accepted")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
