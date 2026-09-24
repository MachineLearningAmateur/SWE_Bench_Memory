"""Mini-SWE-agent's LiteLLM model with a strict native bash-tool contract.

The stock model passes a non-strict schema and leaves tool choice on ``auto``.
Mini-SWE-agent nevertheless requires a bash call on every response, including
the final submit command. Keep its normal parser, history, and agent loop; only
constrain the Chat Completions request to match that existing contract.
"""

from __future__ import annotations

from typing import Any

import litellm
from minisweagent.models.litellm_model import LitellmModel


STRICT_BASH_TOOL = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": "Execute a bash command",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The bash command to execute"}
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    },
}


class StrictToolLitellmModel(LitellmModel):
    """Use mini-SWE-agent's normal LiteLLM loop with a required strict tool."""

    def _query(self, messages: list[dict[str, Any]], **kwargs: Any):
        request_kwargs = self.config.model_kwargs | kwargs
        # A trial must not silently relax the protocol through a config override.
        request_kwargs.pop("tools", None)
        request_kwargs["tool_choice"] = "required"
        request_kwargs["parallel_tool_calls"] = False
        try:
            return litellm.completion(
                model=self.config.model_name,
                messages=messages,
                tools=[STRICT_BASH_TOOL],
                **request_kwargs,
            )
        except litellm.exceptions.AuthenticationError as exc:
            exc.message += " You can permanently set your API key with `mini-extra config set KEY VALUE`."
            raise
