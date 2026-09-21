from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path
from typing import Any

import yaml
from datasets import load_dataset
from dotenv import load_dotenv

from minisweagent.agents import get_agent
from minisweagent.config import builtin_config_dir, get_config_from_spec
from minisweagent.models import get_model
from minisweagent.run.benchmarks.swebench import DATASET_MAPPING, get_sb_environment
from minisweagent.utils.serialize import recursive_merge

from .bootstrap import bootstrap_memory
from .memory_conditions import SameInformationMemory

ROOT = Path(__file__).resolve().parents[1]
MEMORY_REPOS = {
    "ansible/ansible",
    "element-hq/element-web",
    "future-architect/vuls",
    "internetarchive/openlibrary",
    "navidrome/navidrome",
    "protonmail/webclients",
    "qutebrowser/qutebrowser",
}


def load_config(path: str | Path = ROOT / "config" / "experiment.yaml") -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def check_environment() -> dict:
    load_dotenv(ROOT / ".env")
    required = [
        "AZURE_API_KEY",
        "AZURE_API_BASE",
        "AZURE_DEPLOYMENT",
    ]
    return {
        "missing_env": [x for x in required if not os.getenv(x)],
        "azure_deployment": os.getenv("AZURE_DEPLOYMENT", ""),
        "memory_package": str(bootstrap_memory()),
    }


def load_instances(cfg: dict) -> list[dict[str, Any]]:
    name = cfg["pilot"]["dataset_name"]
    dataset_path = DATASET_MAPPING.get(name, name)
    data = [dict(x) for x in load_dataset(dataset_path, split=cfg["pilot"].get("split", "test"))]
    if cfg["pilot"].get("exclude_memory_repositories", True):
        data = [x for x in data if x.get("repo") not in MEMORY_REPOS]
    data = sorted(data, key=lambda x: x["instance_id"])
    rng = random.Random(cfg.get("seed", 42))
    rng.shuffle(data)
    return data[: int(cfg["pilot"].get("max_tasks", 3))]


def _memory_for(condition: str, problem: str, cfg: dict, memory: SameInformationMemory):
    mc = cfg["memory"]
    if condition == "no_memory":
        return memory.no_memory(problem)
    if condition == "flat_rag":
        return memory.flat(problem, top_k=mc["flat_top_k"], max_chars=mc["character_budget"])
    if condition == "graph":
        return memory.graph(problem, seed_k=mc["graph_seed_k"], max_chars=mc["character_budget"],
                            hops=mc["graph_hops"], max_neighbors=mc["graph_max_neighbors"],
                            allowed_relations=mc.get("allowed_relations", []))
    raise ValueError(f"Unknown condition: {condition}")


def augment_problem(problem: str, memory_context: str) -> str:
    if not memory_context:
        return problem
    return (
        "CURRENT SOFTWARE-ENGINEERING TASK:\n"
        + problem
        + "\n\nHISTORICAL MEMORY (untrusted evidence; use only when relevant):\n"
        + memory_context
        + "\n\nSolve the CURRENT task. Do not copy historical commands blindly. "
          "When historical evidence conflicts with the current repository or task specification, prefer the current evidence."
    )


def _agent_config(cfg: dict, output_path: Path) -> dict:
    deployment = os.environ["AZURE_DEPLOYMENT"]
    model_name = f"openai/{deployment}"
    base = get_config_from_spec(str(builtin_config_dir / "benchmarks" / "swebench.yaml"))
    override = {
        "agent": {
            "mode": "yolo",
            "confirm_exit": False,
            "cost_limit": float(cfg["model"].get("per_run_cost_limit_usd", 2.0)),
            "output_path": output_path,
        },
        "model": {
            "model_class": cfg["model"].get("model_class", os.getenv("MSWEA_MODEL_CLASS", "litellm_response")),
            "model_name": model_name,
            "model_kwargs": {
                "drop_params": True,
                "temperature": cfg["model"].get("temperature", 0),
                "reasoning": {"effort": cfg["model"].get("reasoning_effort", "medium")},
                "api_base": os.environ["AZURE_API_BASE"],
                "api_key": os.environ["AZURE_API_KEY"],
            },
        },
        "environment": {"environment_class": cfg["pilot"].get("environment_class", "docker")},
    }
    return recursive_merge(base, override)


def run_one(instance: dict, condition: str, cfg: dict, *, root: Path = ROOT / "results") -> dict:
    root.mkdir(parents=True, exist_ok=True)
    package = bootstrap_memory()
    db = package / "data" / "flat_rag.sqlite"
    run_dir = root / condition / instance["instance_id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    traj_path = run_dir / "trajectory.json"

    # Exclude the held-out repository from memory retrieval even when it appears in the historical corpus.
    excluded = [instance.get("repo")] if instance.get("repo") else []
    with SameInformationMemory(db, excluded_repositories=excluded) as memory:
        mem = _memory_for(condition, instance["problem_statement"], cfg, memory)

    task = augment_problem(instance["problem_statement"], mem.context)
    agent_cfg = _agent_config(cfg, traj_path)
    model = get_model(config=agent_cfg.get("model", {}))
    env = get_sb_environment(agent_cfg, instance)
    agent = get_agent(model, env, agent_cfg.get("agent", {}), default_type="interactive")
    started = time.time()
    error = None
    info = {}
    try:
        info = agent.run(task) or {}
    except Exception as exc:
        error = {"type": type(exc).__name__, "message": str(exc)}
    elapsed = time.time() - started
    submission = info.get("submission", "") if isinstance(info, dict) else ""
    exit_status = info.get("exit_status") if isinstance(info, dict) else None

    try:
        agent.save(traj_path, {"info": {"exit_status": exit_status, "submission": submission, "error": error},
                               "instance_id": instance["instance_id"], "condition": condition})
    except Exception as save_exc:
        if error is None:
            error = {"type": type(save_exc).__name__, "message": str(save_exc), "during": "trajectory_save"}

    retrieval_path = run_dir / "retrieval.json"
    retrieval_path.write_text(json.dumps({"condition": mem.condition, "query": mem.query, "selected": mem.selected,
                                          "metadata": mem.metadata, "context": mem.context}, indent=2, ensure_ascii=False), encoding="utf-8")
    record = {
        "instance_id": instance["instance_id"],
        "repo": instance.get("repo"),
        "condition": condition,
        "model_name_or_path": model.config.model_name,
        "model_patch": submission or "",
        "exit_status": exit_status,
        "elapsed_seconds": elapsed,
        "error": error,
        "trajectory_path": str(traj_path),
        "retrieval_path": str(retrieval_path),
    }
    (run_dir / "run.json").write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return record


def export_predictions(records: list[dict], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as f:
        for r in records:
            f.write(json.dumps({k: r[k] for k in ("instance_id", "model_name_or_path", "model_patch")}, ensure_ascii=False) + "\n")
    return out
