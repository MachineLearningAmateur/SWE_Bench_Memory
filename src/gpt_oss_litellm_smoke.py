"""Verify the chosen LiteLLM route preserves a native tool-call round trip."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from dotenv import load_dotenv

from .gpt_oss_smoke import ROOT
from .model_profiles import resolve_profile


def main() -> None:
    load_dotenv(ROOT / ".env")
    try:
        from litellm import completion
    except ImportError as exc:
        raise SystemExit("Run this inside research after installing the pinned requirements") from exc
    profile = resolve_profile("gpt_oss_20b")
    tool = {"type": "function", "function": {"name": "get_magic_number", "description": "Return the magic number.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}}
    kwargs = {**profile["model_kwargs"], "model": profile["model_name"], "tools": [tool], "max_tokens": 256}
    messages = [{"role": "user", "content": "Call get_magic_number, then answer with the result."}]
    try:
        first = completion(messages=messages, **kwargs)
        message = first.choices[0].message
        calls = message.tool_calls or []
        if not calls or any(c.type != "function" or c.function.name != "get_magic_number" for c in calls):
            raise RuntimeError("No valid native get_magic_number call")
        assistant = {"role": "assistant", "content": message.content, "tool_calls": [c.model_dump() for c in calls]}
        tool_outputs = [{"role": "tool", "tool_call_id": c.id, "content": "42"} for c in calls]
        final = completion(messages=messages + [assistant] + tool_outputs, **kwargs)
        success = "42" in (final.choices[0].message.content or "")
    except Exception as exc:
        # Provider errors may contain request headers or URLs.
        raise SystemExit(f"LiteLLM smoke failed: {type(exc).__name__}") from None
    result = {"timestamp_utc": datetime.now(timezone.utc).isoformat(),
              "profile": "gpt_oss_20b", "deployment": profile["deployment"],
              "model_name": profile["model_name"], "native_tool_round_trip": success}
    out = ROOT / "audit" / "gpt_oss_20b" / "litellm_smoke.json"
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    if not success:
        raise SystemExit("LiteLLM did not return a final answer; do not run SWE tasks")


if __name__ == "__main__":
    main()
