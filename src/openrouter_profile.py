"""OpenRouter alternative to the Azure model profiles.

The prepared Failure50 manifest hashes ``src/model_profiles.py`` and
``src/experiment.py``. This module therefore adds the profile beside them and
installs it into ``src.experiment`` for the current process only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PROFILE = "qwen3_32b_openrouter"


def resolve_openrouter_profile(name: str = PROFILE, *, environ: dict | None = None) -> dict:
    """Return the same shape as ``model_profiles.resolve_profile``."""
    env = os.environ if environ is None else environ
    profile = yaml.safe_load((ROOT / "config" / "models" / f"{name}.yaml").read_text(encoding="utf-8"))
    key = env.get(profile["api_key_env"], "")
    if not key:
        raise ValueError(f"Missing model profile environment variables: {profile['api_key_env']}")
    model_id = env.get(profile["model_id_env"]) or profile["model_id"]
    base = env.get(profile["api_base_env"]) or profile["api_base"]
    routing = dict(profile.get("provider_routing") or {})
    providers = env.get("OPENROUTER_PROVIDERS", "").strip()
    if providers == "any":
        routing.pop("order", None)
        routing.pop("allow_fallbacks", None)
    elif providers:
        routing["order"] = [p.strip() for p in providers.split(",") if p.strip()]
    model_kwargs = {"drop_params": bool(profile.get("drop_params", True)),
                    "temperature": profile.get("temperature", 0),
                    "api_base": base, "api_key": key}
    for setting in ("top_p", "max_tokens"):
        if setting in profile:
            model_kwargs[setting] = profile[setting]
    # top_k is not an OpenAI parameter; send it in the body so drop_params keeps it.
    extra_body = {"top_k": profile["top_k"]} if "top_k" in profile else {}
    if routing:
        extra_body["provider"] = routing
    if extra_body:
        model_kwargs["extra_body"] = extra_body
    return {
        "profile_name": name, "deployment": model_id,
        "model_class": profile["model_class"], "model_name": f"openrouter/{model_id}",
        "model_kwargs": model_kwargs,
        "step_limit": profile.get("step_limit"),
        "wall_time_limit_seconds": profile.get("wall_time_limit_seconds"),
        "per_run_cost_limit_usd": profile.get("per_run_cost_limit_usd", 0),
    }


def use_openrouter(name: str = PROFILE) -> dict:
    """Make ``src.experiment`` resolve the OpenRouter profile in this process."""
    from . import experiment

    profile = resolve_openrouter_profile(name)
    os.environ["MODEL_PROFILE"] = name
    experiment.resolve_profile = lambda *_args, **_kwargs: resolve_openrouter_profile(name)
    return profile


def relabel_record(record: dict, profile: dict) -> dict:
    """Replace the Azure deployment field that ``run_one`` fills from AZURE_DEPLOYMENT."""
    return {**record, "azure_deployment": None, "provider": "openrouter",
            "model_profile": profile["profile_name"], "openrouter_model": profile["deployment"]}


def run_trial(instance: dict, condition: str, cfg: dict, profile: dict, **kwargs) -> dict:
    """Run one ``experiment.run_one`` trial and store the OpenRouter labels in run.json."""
    from . import experiment

    record = relabel_record(experiment.run_one(instance, condition, cfg, **kwargs), profile)
    run_json = Path(record["trajectory_path"]).parent / "run.json"
    run_json.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return record
