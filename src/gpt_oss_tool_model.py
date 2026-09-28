"""Mini-SWE-agent's LiteLLM model with narrow, auditable bash-tool repairs.

The stock model passes a non-strict schema and leaves tool choice on ``auto``.
Mini-SWE-agent nevertheless requires a bash call on every response, including
the final submit command. Keep its normal parser, history, and agent loop;
repair only exact typos observed on development trajectories.
"""

from __future__ import annotations

import json
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


def repair_observed_patch_arguments(arguments: str) -> str | None:
    """Remove only the stray closing bracket seen in the development traces.

    This is deliberately not a general JSON-repair routine. A call that does
    not match the exact observed failure remains a mini-SWE-agent format error.
    """
    if not isinstance(arguments, str) or not arguments.endswith('"]}'):
        return None
    try:
        json.loads(arguments)
    except json.JSONDecodeError:
        pass
    else:
        return None
    candidate = arguments[:-2] + "}"
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict) or set(parsed) != {"command"}:
        return None
    command = parsed["command"]
    if not isinstance(command, str):
        return None
    if not command.startswith("apply_patch <<'PATCH'\n") or not command.endswith("\nPATCH"):
        return None
    return candidate


def repair_observed_tool_name(name: str, arguments: str) -> str | None:
    """Accept only the observed leaked channel suffix on a valid bash call."""
    if name != "bash<|channel|>commentary" or not isinstance(arguments, str):
        return None
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict) or set(parsed) != {"command"}:
        return None
    if not isinstance(parsed["command"], str) or not parsed["command"].strip():
        return None
    return "bash"


class RepairingToolLitellmModel(StrictToolLitellmModel):
    """Repair one observed typo at a time before mini's normal parser."""

    def _query(self, messages: list[dict[str, Any]], **kwargs: Any):
        response = super()._query(messages, **kwargs)
        choices = response.choices or []
        if len(choices) != 1 or choices[0].finish_reason != "tool_calls":
            return response
        calls = choices[0].message.tool_calls or []
        if (len(calls) != 1 or calls[0].type != "function" or
                calls[0].function is None):
            return response
        tool_call = calls[0]
        name = tool_call.function.name
        original = tool_call.function.arguments
        repaired_name = repair_observed_tool_name(name, original)
        if repaired_name is not None:
            tool_call.function.name = repaired_name
            response.agent_tool_repairs = [{
                "kind": "channel_suffix_in_bash_name",
                "tool_call_id": tool_call.id,
                "original_name": name,
                "repaired_name": repaired_name,
                "original_arguments": original,
            }]
            return response
        if name != "bash":
            return response
        repaired = repair_observed_patch_arguments(original)
        if repaired is None:
            return response
        tool_call.function.arguments = repaired
        # mini-SWE-agent stores response.model_dump() in every trajectory.
        # Preserve the wire-visible original alongside the interpreted call.
        response.agent_tool_repairs = [{
            "kind": "extra_bracket_after_apply_patch",
            "tool_call_id": tool_call.id,
            "original_arguments": original,
            "repaired_arguments": repaired,
        }]
        return response
