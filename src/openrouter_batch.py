"""Run the ten-task Verified comparison through OpenRouter instead of Azure.

Uses the GPT-OSS batch's seed-42 tasks, trial order, agent overrides, and
graph_v2 memory settings so results are comparable; only the model route differs.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import yaml
from dotenv import load_dotenv

from .openrouter_profile import PROFILE, ROOT, resolve_openrouter_profile, run_trial, use_openrouter

SOURCE_FILES = ["src/experiment.py", "src/verified_batch.py", "src/openrouter_batch.py",
                "src/openrouter_profile.py", "src/memory_conditions.py", "src/gpt_oss_tool_model.py",
                f"config/models/{PROFILE}.yaml"]


def _settings(profile: dict) -> dict:
    return {k: v for k, v in profile["model_kwargs"].items() if k not in {"api_base", "api_key"}}


def prepare(output: Path, max_tasks: int) -> None:
    from .bootstrap import bootstrap_memory
    from .experiment import load_config, load_instances
    from .verified_batch import CONDITIONS, OVERRIDES, sha256, trial_order, write_json

    if output.exists():
        raise FileExistsError(f"Refusing to replace an experiment: {output}")
    profile = resolve_openrouter_profile()
    cfg = load_config()
    cfg["memory"].update(yaml.safe_load((ROOT / "config" / "graph_v2.yaml").read_text(encoding="utf-8")))
    cfg["pilot"].update(max_tasks=max_tasks, dataset_name="SWE-bench/SWE-bench_Verified",
                        split="test", conditions=list(CONDITIONS))
    tasks = load_instances(cfg)
    if len(tasks) != max_tasks or len({task["instance_id"] for task in tasks}) != max_tasks:
        raise ValueError(f"Expected exactly {max_tasks} unique Verified tasks")
    db = bootstrap_memory() / "data" / "flat_rag.sqlite"
    output.mkdir(parents=True)
    write_json(output / "tasks.json", tasks)
    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "task_snapshot_sha256": sha256(output / "tasks.json"),
        "memory_sha256": sha256(db), "config": cfg, "agent_overrides": OVERRIDES,
        "model_profile": PROFILE, "provider": "openrouter", "model_name": profile["model_name"],
        "deployment": profile["deployment"], "model_settings": _settings(profile),
        "versions": {name: version(name) for name in ("mini-swe-agent", "swebench", "litellm")},
        "source_sha256": {name: sha256(ROOT / name) for name in SOURCE_FILES},
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "trial_order": trial_order(tasks), "attempts_per_task_condition": 1,
        "uniform_memory_wrapper": True,
    }
    write_json(output / "manifest.json", manifest)
    write_json(output / "records.json", [])
    write_json(output / "status.json", {"state": "prepared", "completed": 0, "total": len(manifest["trial_order"])})
    print(json.dumps({"prepared": str(output), "trials": len(manifest["trial_order"]), "model": profile["model_name"]}))


def execute(output: Path, log, *, resume: bool = False) -> None:
    from .bootstrap import bootstrap_memory
    from .evaluate import swebench_eval_command, validate_grading_report
    from .experiment import export_predictions
    from .verified_batch import CONDITIONS, completed_records, sha256, validate_sources, write_json

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    if sha256(output / "tasks.json") != manifest["task_snapshot_sha256"]:
        raise ValueError("Task snapshot changed")
    validate_sources(output, manifest)
    if sha256(bootstrap_memory() / "data" / "flat_rag.sqlite") != manifest["memory_sha256"]:
        raise ValueError("Memory corpus changed")
    profile = use_openrouter()
    if profile["model_name"] != manifest["model_name"] or _settings(profile) != manifest["model_settings"]:
        raise ValueError("Model or provider routing changed")
    tasks = {task["instance_id"]: task for task in json.loads((output / "tasks.json").read_text())}
    total = len(manifest["trial_order"])
    records = completed_records(output, manifest) if resume else []
    for trial in manifest["trial_order"][len(records):]:
        write_json(output / "status.json", {"state": "running", "completed": len(records),
                                            "total": total, "current": trial})
        print(f"START {len(records)+1}/{total} {trial}", flush=True)
        record = run_trial(tasks[trial["instance_id"]], trial["condition"], manifest["config"], profile,
                           root=output / "runs", agent_overrides=manifest["agent_overrides"],
                           uniform_wrapper=True)
        records.append(record)
        write_json(output / "records.json", records)
        print(f"DONE {len(records)}/{total} exit={record['exit_status']} patch_chars={len(record['model_patch'])}", flush=True)
        if record["error"]:
            raise RuntimeError(f"Trial execution error; inspect saved run for {trial}")
    grade_results = {}
    for condition in CONDITIONS:
        predictions = export_predictions([r for r in records if r["condition"] == condition],
                                         output / f"predictions_{condition}.jsonl")
        run_id = f"{output.name}_{condition}"
        write_json(output / "status.json", {"state": "grading", "completed": total,
                                            "total": total, "condition": condition})
        command = swebench_eval_command(str(output / "tasks.json"), predictions, run_id, max_workers=1)
        command[0] = sys.executable
        print(f"GRADE {condition} run_id={run_id}", flush=True)
        completed = subprocess.run(command, cwd=output, stdout=log, stderr=subprocess.STDOUT)
        reports = list(output.glob(f"*.{run_id}.json"))
        if completed.returncode or len(reports) != 1:
            raise RuntimeError(f"Official grading failed for {condition}; inspect batch.log")
        report = json.loads(reports[0].read_text())
        instance_logs = output / "logs" / "run_evaluation" / run_id / manifest["model_name"].replace("/", "__")
        rejected = validate_grading_report(report, instance_logs)
        grade_results[condition] = {"report": str(reports[0]), "returncode": completed.returncode,
                                    "resolved_ids": report.get("resolved_ids", []), "rejected_patches": rejected}
    rows = [{"instance_id": task_id, **{c: task_id in grade_results[c]["resolved_ids"] for c in CONDITIONS}}
            for task_id in tasks]
    write_json(output / "summary.json", {"tasks": rows, "grading": grade_results,
               "resolved": {c: sum(row[c] for row in rows) for c in CONDITIONS},
               "denominator_per_condition": len(tasks)})
    write_json(output / "status.json", {"state": "complete", "completed": total, "total": total})
    print("COMPLETE", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-tasks", type=int, default=10)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--resume", action="store_true", help="Continue a stopped batch from its verified completed prefix")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env", override=True)
    os.environ["MODEL_PROFILE"] = PROFILE
    output = args.output.resolve()
    if not args.run and not args.resume:
        prepare(output, args.max_tasks)
        return
    from .verified_batch import sha256, write_json

    previous = json.loads((output / "status.json").read_text())
    expected = "stopped" if args.resume else "prepared"
    if previous["state"] != expected:
        raise ValueError(f"Cannot start from state {previous['state']}; expected {expected}")
    lock = output / "execution.lock"
    with lock.open("x") as marker:
        marker.write(str(os.getpid()))
    worker = {"started_utc": datetime.now(timezone.utc).isoformat(), "pid": os.getpid(), "resume": args.resume}
    if args.resume:
        recovery = output / "recovery"
        recovery.mkdir(exist_ok=True)
        write_json(recovery / f"resume_{os.getpid()}.json", {**worker, "prior_status": previous,
                   "records_sha256": sha256(output / "records.json")})
    else:
        with (output / "started.json").open("x") as marker:
            json.dump(worker, marker)
    with (output / "batch.log").open("a", encoding="utf-8", buffering=1) as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            try:
                execute(output, log, resume=args.resume)
            except Exception as exc:
                previous = json.loads((output / "status.json").read_text())
                write_json(output / "status.json", {**previous, "state": "stopped", "error_type": type(exc).__name__})
                import traceback
                traceback.print_exc()
                raise SystemExit(1)
            finally:
                lock.unlink()


if __name__ == "__main__":
    main()
