"""Check that the OpenRouter route returns the agent's required bash tool calls."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from dotenv import load_dotenv

from .openrouter_profile import ROOT, resolve_openrouter_profile

# Same schema as src.gpt_oss_tool_model.STRICT_BASH_TOOL; copied so this smoke
# runs without mini-swe-agent installed.
BASH_TOOL = {
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


def _bash_command(response) -> str | None:
    calls = response.choices[0].message.tool_calls or []
    if len(calls) != 1 or calls[0].function.name != "bash":
        return None
    try:
        return json.loads(calls[0].function.arguments).get("command")
    except (json.JSONDecodeError, AttributeError):
        return None


def main() -> None:
    load_dotenv(ROOT / ".env")
    try:
        from litellm import completion
    except ImportError as exc:
        raise SystemExit("Install the pinned solver requirements first") from exc
    profile = resolve_openrouter_profile()
    # Same request shape as StrictToolLitellmModel.
    kwargs = {**profile["model_kwargs"], "model": profile["model_name"], "tools": [BASH_TOOL],
              "tool_choice": "required", "parallel_tool_calls": False}
    messages = [{"role": "user", "content": "Use bash to run `printf hello`."}]
    try:
        first = completion(messages=messages, **kwargs)
        if _bash_command(first) != "printf hello":
            raise ValueError("First response did not make the expected bash call")
        call = first.choices[0].message.tool_calls[0]
        messages += [{"role": "assistant", "content": first.choices[0].message.content,
                      "tool_calls": [call.model_dump()]},
                     {"role": "tool", "tool_call_id": call.id, "content": "hello"},
                     {"role": "user", "content": "Now use bash to run `printf done`."}]
        second = completion(messages=messages, **kwargs)
        if _bash_command(second) != "printf done":
            raise ValueError("Second response did not make the expected bash call")
        # A provider that honors tool_choice=required must call a tool even when told not to.
        probe = completion(messages=[{"role": "user", "content": "Do not use any tool. Reply with: banana."}],
                           **kwargs)
    except Exception as exc:
        # Provider errors may echo request headers or URLs.
        raise SystemExit(f"OpenRouter smoke failed: {type(exc).__name__}") from None
    result = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "profile": profile["profile_name"],
              "model_name": profile["model_name"], "served_model": second.model,
              "served_provider": [getattr(r, "provider", None) for r in (first, second, probe)],
              "provider_routing": profile["model_kwargs"].get("extra_body", {}).get("provider"),
              "bash_tool_two_turns": True,
              "tool_choice_required_enforced": bool(probe.choices[0].message.tool_calls)}
    out = ROOT / "audit" / profile["profile_name"] / "smoke.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
