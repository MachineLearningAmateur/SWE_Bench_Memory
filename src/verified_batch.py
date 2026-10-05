"""Run a frozen ten-task GPT-OSS Verified comparison and official grading."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import itertools
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from dotenv import load_dotenv

from .bootstrap import bootstrap_memory
from .evaluate import swebench_eval_command, validate_grading_report
from .experiment import ROOT, export_predictions, load_config, load_instances, run_one
from .model_profiles import resolve_profile

CONDITIONS = ("no_memory", "flat_rag", "graph")
OVERRIDES = {
    "agent": {"step_limit": 100, "wall_time_limit_seconds": 3600, "cost_limit": 0},
    "model": {"model_class": "src.gpt_oss_tool_model.RepairingToolLitellmModel",
              "cost_tracking": "ignore_errors"},
}


def write_json(path: Path, value) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def trial_order(instances: list[dict]) -> list[dict]:
    orders = list(itertools.permutations(CONDITIONS))
    return [{"instance_id": task["instance_id"], "condition": condition}
            for index, task in enumerate(instances)
            for condition in orders[index % len(orders)]]


def prepare(output: Path) -> None:
    if output.exists():
        raise FileExistsError(f"Refusing to replace an experiment: {output}")
    profile = resolve_profile("gpt_oss_20b")
    cfg = load_config()
    cfg["pilot"].update(max_tasks=10, dataset_name="SWE-bench/SWE-bench_Verified",
                        split="test", conditions=list(CONDITIONS))
    tasks = load_instances(cfg)
    if len(tasks) != 10 or len({task["instance_id"] for task in tasks}) != 10:
        raise ValueError("Expected exactly ten unique Verified tasks")
    db = bootstrap_memory() / "data" / "flat_rag.sqlite"
    source_files = ["src/experiment.py", "src/verified_batch.py", "src/model_profiles.py",
                    "src/memory_conditions.py", "src/gpt_oss_tool_model.py",
                    "config/models/gpt_oss_20b.yaml"]
    output.mkdir(parents=True)
    write_json(output / "tasks.json", tasks)
    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": cfg["pilot"]["dataset_name"], "split": "test",
        "task_snapshot_sha256": sha256(output / "tasks.json"),
        "memory_sha256": sha256(db), "config": cfg, "agent_overrides": OVERRIDES,
        "model_profile": "gpt_oss_20b", "model_name": profile["model_name"],
        "deployment": profile["deployment"],
        "model_settings": {k: v for k, v in profile["model_kwargs"].items()
                           if k not in {"api_base", "api_key"}},
        "versions": {name: version(name) for name in ("mini-swe-agent", "swebench", "litellm")},
        "source_sha256": {name: sha256(ROOT / name) for name in source_files},
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "trial_order": trial_order(tasks), "attempts_per_task_condition": 1,
        "uniform_memory_wrapper": True,
        "selection": "Existing deterministic seed-42 sampler; includes prior GPT-5.1 smoke tasks.",
        "scoring": "Official SWE-bench evaluation of explicitly submitted patches; unfinished attempts remain unresolved.",
    }
    write_json(output / "manifest.json", manifest)
    write_json(output / "status.json", {"state": "prepared", "completed": 0, "total": 30})
    print(json.dumps({"prepared": str(output), "trials": 30, "model": profile["model_name"]}))


def validate_sources(output: Path, manifest: dict) -> None:
    for name, expected in manifest["source_sha256"].items():
        actual = sha256(ROOT / name)
        if actual == expected:
            continue
        migration_path = output / "recovery" / "runner_migration.json"
        if name == "src/verified_batch.py" and migration_path.exists():
            migration = json.loads(migration_path.read_text())
            original = output / "recovery" / "original_verified_batch.py"
            if (migration["before_sha256"] == expected and migration["after_sha256"] == actual
                    and sha256(original) == expected):
                continue
        raise ValueError(f"Frozen source changed: {name}")


def completed_records(output: Path, manifest: dict) -> list[dict]:
    """Accept only an exact, unmodified completed prefix; never replay an attempt."""
    records = json.loads((output / "records.json").read_text())
    if len(records) > len(manifest["trial_order"]):
        raise ValueError("More records than planned trials")
    for record, trial in zip(records, manifest["trial_order"]):
        if any(record[key] != trial[key] for key in ("instance_id", "condition")) or record.get("error"):
            raise ValueError("Completed records are not a clean prefix of the frozen plan")
        path = output / "runs" / trial["condition"] / trial["instance_id"] / "run.json"
        if json.loads(path.read_text()) != record:
            raise ValueError("Completed trial differs from records.json")
    return records


def execute(output: Path, log, *, resume: bool = False) -> None:
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    if sha256(output / "tasks.json") != manifest["task_snapshot_sha256"]:
        raise ValueError("Task snapshot changed")
    validate_sources(output, manifest)
    if sha256(bootstrap_memory() / "data" / "flat_rag.sqlite") != manifest["memory_sha256"]:
        raise ValueError("Memory corpus changed")
    profile = resolve_profile("gpt_oss_20b")
    if profile["model_name"] != manifest["model_name"] or profile["deployment"] != manifest["deployment"]:
        raise ValueError("Model deployment changed")
    settings = {k: v for k, v in profile["model_kwargs"].items() if k not in {"api_base", "api_key"}}
    if settings != manifest["model_settings"]:
        raise ValueError("Model settings changed")
    tasks = {task["instance_id"]: task for task in json.loads((output / "tasks.json").read_text())}
    records = completed_records(output, manifest) if resume else []
    for trial in manifest["trial_order"][len(records):]:
        write_json(output / "status.json", {"state": "running", "completed": len(records),
                                            "total": 30, "current": trial})
        print(f"START {len(records)+1}/30 {trial}", flush=True)
        record = run_one(tasks[trial["instance_id"]], trial["condition"], manifest["config"],
                         root=output / "runs", agent_overrides=manifest["agent_overrides"],
                         uniform_wrapper=True)
        records.append(record)
        write_json(output / "records.json", records)
        print(f"DONE {len(records)}/30 exit={record['exit_status']} patch_chars={len(record['model_patch'])}", flush=True)
        if record["error"]:
            raise RuntimeError(f"Trial execution error; inspect saved run for {trial}")
    grade_results = {}
    for condition in CONDITIONS:
        predictions = export_predictions([r for r in records if r["condition"] == condition],
                                         output / f"predictions_{condition}.jsonl")
        run_id = f"{output.name}_{condition}"
        write_json(output / "status.json", {"state": "grading", "completed": 30,
                                            "total": 30, "condition": condition})
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
    rows = []
    for task_id in tasks:
        row = {"instance_id": task_id}
        for condition in CONDITIONS:
            row[condition] = task_id in grade_results[condition]["resolved_ids"]
        rows.append(row)
    write_json(output / "summary.json", {"tasks": rows, "grading": grade_results,
               "resolved": {c: sum(row[c] for row in rows) for c in CONDITIONS}, "denominator_per_condition": 10})
    write_json(output / "status.json", {"state": "complete", "completed": 30, "total": 30})
    print("COMPLETE", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--resume", action="store_true", help="Continue a stopped batch from its verified completed prefix")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env", override=True)
    os.environ["MODEL_PROFILE"] = "gpt_oss_20b"
    output = args.output.resolve()
    if not args.run and not args.resume:
        prepare(output)
        return
    if args.resume:
        previous = json.loads((output / "status.json").read_text())
        if previous["state"] != "stopped":
            raise ValueError("Resume requires a stopped batch")
    # Exclusive live lock plus immutable initial launch marker prevent duplicate workers.
    lock = output / "execution.lock"
    with lock.open("x") as marker:
        marker.write(str(os.getpid()))
    worker = {"started_utc": datetime.now(timezone.utc).isoformat(), "pid": os.getpid(), "resume": args.resume}
    if not args.resume:
        with (output / "started.json").open("x") as marker:
            json.dump(worker, marker)
    else:
        recovery = output / "recovery"
        recovery.mkdir(exist_ok=True)
        write_json(recovery / f"resume_{os.getpid()}.json", {**worker, "prior_status": previous,
                   "records_sha256": sha256(output / "records.json")})
    write_json(output / "active_worker.json", worker)
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
