"""Exercise two mini-SWE-agent native tool turns without a benchmark task."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from dotenv import load_dotenv

from .gpt_oss_smoke import ROOT
from .gpt_oss_tool_model import StrictToolLitellmModel
from .model_profiles import resolve_profile


def main() -> None:
    load_dotenv(ROOT / ".env")
    profile = resolve_profile("gpt_oss_20b")
    model = StrictToolLitellmModel(
        model_name=profile["model_name"],
        model_kwargs=profile["model_kwargs"],
        cost_tracking="ignore_errors",
    )
    messages = [{"role": "user", "content": "Use bash to run `printf hello`."}]
    try:
        first = model.query(messages)
        first_actions = first["extra"]["actions"]
        if len(first_actions) != 1 or first_actions[0]["command"] != "printf hello":
            raise ValueError("First response did not make the expected bash call")
        observations = model.format_observation_messages(
            first, [{"returncode": 0, "output": "hello", "exception_info": None}]
        )
        messages += [first, *observations]
        messages.append({"role": "user", "content": "Now use bash to run `printf done`."})
        second = model.query(messages)
        second_actions = second["extra"]["actions"]
        if len(second_actions) != 1 or second_actions[0]["command"] != "printf done":
            raise ValueError("Second response did not make the expected bash call")
    except Exception as exc:
        # Provider errors and model messages can echo credentials or task data.
        raise SystemExit(f"Agent protocol smoke failed: {type(exc).__name__}") from None
    result = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "profile": "gpt_oss_20b",
        "deployment": profile["deployment"],
        "model_name": profile["model_name"],
        "strict_required_bash_round_trip": True,
        "native_tool_turns": 2,
    }
    out = ROOT / "audit" / "gpt_oss_20b" / "agent_protocol_smoke.json"
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
