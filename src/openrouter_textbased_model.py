"""Mini-SWE-agent's text-based LiteLLM model for the OpenRouter profile.

Text mode reads one ```mswea_bash_command block from the response instead of a
tool call. ``run_one`` merges the text config over swebench.yaml, which leaves
that config's ``parallel_tool_calls`` in model_kwargs; a request without tools
must not carry tool parameters.
"""

from __future__ import annotations

from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel

TOOL_ONLY_PARAMS = ("parallel_tool_calls", "tool_choice", "tools")


class TextbasedLitellmModel(LitellmTextbasedModel):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        for name in TOOL_ONLY_PARAMS:
            self.config.model_kwargs.pop(name, None)
