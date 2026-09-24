"""Resolve selectable model profiles without exposing credentials."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PROFILES = {"gpt_5_1_codex_mini", "gpt_oss_20b"}


def resolve_profile(name: str | None = None, *, environ: dict | None = None) -> dict:
    name = name or os.getenv("MODEL_PROFILE", "gpt_5_1_codex_mini")
    if name not in PROFILES:
        raise ValueError(f"Unknown MODEL_PROFILE: {name}")
    env = os.environ if environ is None else environ
    profile = yaml.safe_load((ROOT / "config" / "models" / f"{name}.yaml").read_text(encoding="utf-8"))
    deployment = env.get(profile["deployment_env"], "")
    base = env.get(profile["api_base_env"], "")
    key = env.get(profile["api_key_env"], "")
    model_name = env.get(profile["model_name_env"], "") if profile.get("model_name_env") else (
        profile["model_name_prefix"] + deployment if deployment else ""
    )
    missing = [label for label, value in ((profile["deployment_env"], deployment),
              (profile["api_base_env"], base), (profile["api_key_env"], key)) if not value]
    if profile.get("model_name_env") and not model_name:
        missing.append(profile["model_name_env"])
    if missing:
        raise ValueError(f"Missing model profile environment variables: {', '.join(missing)}")
    model_kwargs = {"drop_params": bool(profile.get("drop_params", True)),
                    "temperature": profile.get("temperature", 0),
                    "api_base": base, "api_key": key}
    if name == "gpt_5_1_codex_mini":
        model_kwargs["reasoning"] = {"effort": profile["reasoning_effort"]}
    elif env.get(profile["send_reasoning_effort_env"]) == "1":
        model_kwargs["reasoning_effort"] = profile["reasoning_effort"]
    return {
        "profile_name": name, "deployment": deployment,
        "model_class": profile["model_class"], "model_name": model_name,
        "model_kwargs": model_kwargs,
        "step_limit": profile.get("step_limit"),
        "wall_time_limit_seconds": profile.get("wall_time_limit_seconds"),
        "per_run_cost_limit_usd": profile.get("per_run_cost_limit_usd", 0),
    }
