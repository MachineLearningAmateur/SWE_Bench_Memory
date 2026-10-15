"""Run the Failure50 problems and trace contexts through OpenRouter.

Reuses the ten pilot tasks and the frozen retrieval bundles from
results/failure50_verified10_v1, built from the 50 slm-failure-memory-pilot
traces. The source experiment is only read; this run copies the tasks and
bundles into its own directory with its own manifest, records, and grading.
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from .failure_corpus import digest, read_json, write_json
from .openrouter_profile import PROFILE, ROOT, resolve_openrouter_profile, run_trial, use_openrouter

SOURCE = ROOT / "results" / "failure50_verified10_v1"
SOURCE_FILES = ["src/experiment.py", "src/openrouter_failure50.py", "src/openrouter_profile.py",
                "src/gpt_oss_tool_model.py", "src/openrouter_textbased_model.py", "src/verified_batch.py",
                "src/evaluate.py", "src/verified_report.py", f"config/models/{PROFILE}.yaml"]
# "tool": native tool calls (the GPT-OSS protocol). "text": mini-swe-agent's
# text-based mode, one ```mswea_bash_command block per response, for providers
# that do not enforce tool_choice=required.
ACTION_FORMATS = ("text", "tool")
TEXT_MODEL_CLASS = "src.openrouter_textbased_model.TextbasedLitellmModel"
# DeepInfra serves Qwen3 32B with a 40,960-token window. An overflow ends that
# attempt unresolved instead of stopping the batch; other errors still stop it.
# LiteLLM reports DeepInfra's overflow through OpenRouter as a BadRequestError.
CONTEXT_LIMIT_ERRORS = {"ContextWindowExceededError"}
CONTEXT_LIMIT_MESSAGE = "maximum context length"


def _settings(profile: dict) -> dict:
    return {k: v for k, v in profile["model_kwargs"].items() if k not in {"api_base", "api_key"}}


def _bundle(instance_id: str, condition: str) -> str:
    return f"contexts/pilot/{instance_id}/{condition}.json"


def agent_overrides(action_format: str) -> dict:
    """Agent limits from the GPT-OSS batch, plus the text-mode templates when selected."""
    from .verified_batch import OVERRIDES

    if action_format == "tool":
        return OVERRIDES
    import yaml
    from minisweagent.config import builtin_config_dir
    from minisweagent.utils.serialize import recursive_merge

    text = yaml.safe_load((builtin_config_dir / "benchmarks" / "swebench_backticks.yaml").read_text(encoding="utf-8"))
    # The profile supplies the model name and settings; OVERRIDES supplies the limits.
    text["model"].pop("model_name", None)
    text["model"].pop("model_kwargs", None)
    return recursive_merge(text, OVERRIDES, {"model": {"model_class": TEXT_MODEL_CLASS}})


def format_error_count(trajectory: dict) -> int:
    """Count FormatError messages in either action format."""
    return sum((m.get("extra") or {}).get("interrupt_type") == "FormatError" for m in trajectory.get("messages", []))


def prepare(out: Path, source: Path, conditions: list[str], action_format: str = "text") -> None:
    from .failure_pilot import CONDITIONS, validate, validate_contexts

    if out.exists():
        raise FileExistsError(f"Refusing to replace an experiment: {out}")
    if action_format not in ACTION_FORMATS:
        raise ValueError(f"Unknown action format: {action_format}")
    unknown = sorted(set(conditions) - set(CONDITIONS))
    if unknown:
        raise ValueError(f"Unknown Failure50 conditions: {', '.join(unknown)}")
    overrides = agent_overrides(action_format)
    frozen = validate(source)
    validate_contexts(source)
    profile = resolve_openrouter_profile()
    # Keep the frozen balanced order, restricted to the chosen arms.
    trials = [t for t in frozen["trial_order"] if t["condition"] in conditions]
    out.mkdir(parents=True)
    shutil.copy2(source / "tasks.json", out / "tasks.json")
    contexts = {}
    for trial in trials:
        rel = _bundle(trial["instance_id"], trial["condition"])
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / rel, out / rel)
        contexts[rel] = digest(out / rel)
    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "study": "Failure50 problems and trace contexts, OpenRouter solver",
        "source_experiment": source.relative_to(ROOT).as_posix(),
        "source_manifest_sha256": digest(source / "manifest.json"),
        "source_contexts_manifest_sha256": digest(source / "contexts_manifest.json"),
        "trace_source_commit": frozen["source_commit"], "corpus_sha256": frozen["corpus_sha256"],
        "conditions": [c for c in CONDITIONS if c in conditions],
        "trial_order": trials, "attempts_per_condition": 1,
        "tasks_sha256": digest(out / "tasks.json"), "contexts_sha256": contexts,
        "action_format": action_format, "agent_overrides": overrides, "uniform_memory_wrapper": True,
        "model_profile": PROFILE, "provider": "openrouter", "model_name": profile["model_name"],
        "deployment": profile["deployment"], "model_settings": _settings(profile),
        "context_limit_policy": "A context-window overflow (ContextWindowExceededError, or a BadRequestError "
                                "reporting the maximum context length) ends the attempt unresolved; the batch continues.",
        "versions": {p: version(p) for p in ("mini-swe-agent", "swebench", "litellm")},
        "source_sha256": {p: digest(ROOT / p) for p in SOURCE_FILES},
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
    }
    write_json(out / "manifest.json", manifest)
    write_json(out / "records.json", [])
    write_json(out / "status.json", {"state": "prepared", "completed": 0, "total": len(trials)})
    print(f"prepared {out} trials={len(trials)} model={profile['model_name']}")


def validate(out: Path) -> dict:
    m = read_json(out / "manifest.json")
    for rel, expected in m["source_sha256"].items():
        if digest(ROOT / rel) != expected:
            raise ValueError(f"Frozen code/config changed: {rel}")
    if digest(out / "tasks.json") != m["tasks_sha256"]:
        raise ValueError("Task snapshot changed")
    for rel, expected in m["contexts_sha256"].items():
        if digest(out / rel) != expected:
            raise ValueError(f"Frozen context changed: {rel}")
    return m


def is_context_limit(error: dict | None) -> bool:
    return bool(error) and (error["type"] in CONTEXT_LIMIT_ERRORS or (
        error["type"] == "BadRequestError" and CONTEXT_LIMIT_MESSAGE in error.get("message", "")))


def _accepted(record: dict) -> bool:
    return not record.get("error") or is_context_limit(record["error"])


def completed_prefix(out: Path, m: dict) -> list[dict]:
    records = read_json(out / "records.json")
    if len(records) > len(m["trial_order"]):
        raise ValueError("Too many records")
    for record, trial in zip(records, m["trial_order"]):
        if any(record[k] != trial[k] for k in ("instance_id", "condition")) or not _accepted(record):
            raise ValueError("Records are not a clean completed prefix")
        if record != read_json(out / "runs" / trial["condition"] / trial["instance_id"] / "run.json"):
            raise ValueError("Completed trial differs from records.json")
    return records


def execute(out: Path, resume: bool = False) -> None:
    from .experiment import load_config
    from .memory_conditions import MemoryResult

    m = validate(out)
    profile = use_openrouter()
    if profile["model_name"] != m["model_name"] or _settings(profile) != m["model_settings"]:
        raise ValueError("Model or provider routing changed")
    tasks = {t["instance_id"]: t for t in read_json(out / "tasks.json")}
    cfg = load_config()
    # Every trial supplies prepared memory, so the legacy bootstrap is unreachable.
    cfg["memory"] = {"prepared_corpus": m["corpus_sha256"]}
    records = completed_prefix(out, m) if resume else []
    total = len(m["trial_order"])
    for trial in m["trial_order"][len(records):]:
        task_id, condition = trial["instance_id"], trial["condition"]
        bundle = read_json(out / _bundle(task_id, condition))
        write_json(out / "status.json", {"state": "running", "completed": len(records), "total": total, "current": trial})
        print(f"START {len(records)+1}/{total} {task_id} {condition}", flush=True)
        record = run_trial(tasks[task_id], condition, cfg, profile, root=out / "runs",
                           agent_overrides=m["agent_overrides"], uniform_wrapper=True,
                           prepared_memory=MemoryResult(**bundle))
        records.append(record)
        write_json(out / "records.json", records)
        print(f"DONE exit={record['exit_status']} error={(record['error'] or {}).get('type')} "
              f"patch_chars={len(record['model_patch'])}", flush=True)
        if not _accepted(record):
            raise RuntimeError("Infrastructure/runner error; inspect preserved attempt")
    write_json(out / "status.json", {"state": "awaiting_grading", "completed": total, "total": total})
    grade(out)


def grade(out: Path) -> None:
    from .evaluate import swebench_eval_command, validate_grading_report
    from .experiment import export_predictions
    from .verified_report import trajectory_metrics

    m = validate(out)
    records = completed_prefix(out, m)
    if len(records) != len(m["trial_order"]):
        raise ValueError("Cannot grade an incomplete run")
    conditions = m["conditions"]
    expected = {t["instance_id"] for t in read_json(out / "tasks.json")}
    reports = {}
    for condition in conditions:
        predictions = export_predictions([r for r in records if r["condition"] == condition],
                                         out / f"predictions_{condition}.jsonl")
        run_id = f"{out.name}_{condition}"
        write_json(out / "status.json", {"state": "grading", "condition": condition,
                                         "completed": len(records), "total": len(records)})
        command = swebench_eval_command(str(out / "tasks.json"), predictions, run_id, max_workers=1)
        command[0] = sys.executable
        with (out / "grading.log").open("a", encoding="utf-8") as log:
            result = subprocess.run(command, cwd=out, stdout=log, stderr=subprocess.STDOUT)
        found = list(out.glob(f"*.{run_id}.json"))
        if result.returncode or len(found) != 1:
            raise RuntimeError(f"Grading failed for {condition}")
        report = read_json(found[0])
        logs = out / "logs/run_evaluation" / run_id / m["model_name"].replace("/", "__")
        rejected = validate_grading_report(report, logs)
        if set(report.get("submitted_ids", [])) != expected:
            raise ValueError("Official grading does not cover every planned task")
        if not set(report.get("resolved_ids", [])) <= expected:
            raise ValueError("Grader returned unexpected task IDs")
        reports[condition] = {"report": found[0].name, "report_sha256": digest(found[0]),
                              "resolved_ids": report.get("resolved_ids", []), "rejected": rejected}
    paired = [{"instance_id": i, **{c: i in reports[c]["resolved_ids"] for c in conditions}} for i in sorted(expected)]
    usage = {c: {"input_tokens": 0, "output_tokens": 0, "calls_with_usage": 0, "model_calls": 0,
                 "format_errors": 0, "elapsed_seconds": 0, "nonempty_submissions": 0,
                 "context_limit_exits": 0} for c in conditions}
    for record in records:
        count = usage[record["condition"]]
        count["elapsed_seconds"] += record["elapsed_seconds"]
        count["nonempty_submissions"] += int(record["exit_status"] == "Submitted" and bool(record["model_patch"].strip()))
        count["context_limit_exits"] += int(is_context_limit(record.get("error")))
        trajectory = out / "runs" / record["condition"] / record["instance_id"] / "trajectory.json"
        if trajectory.exists():
            data = read_json(trajectory)
            metrics = trajectory_metrics(data)
            for key in ("input_tokens", "output_tokens", "model_calls"):
                count[key] += metrics[key]
            count["calls_with_usage"] += metrics["responses_with_usage"]
            count["format_errors"] += format_error_count(data)
    write_json(out / "summary.json", {
        "tasks": paired, "grading": reports, "denominator_per_condition": len(expected),
        "resolved": {c: len(reports[c]["resolved_ids"]) for c in conditions},
        "exit_statuses": {c: dict(collections.Counter(r["exit_status"] for r in records if r["condition"] == c))
                          for c in conditions},
        "usage": usage, "model_name": m["model_name"],
        "interpretation": "Ten paired problems, one attempt per condition; exploratory, not a general memory advantage estimate."})
    write_json(out / "status.json", {"state": "complete", "completed": len(records), "total": len(records)})


def main() -> None:
    from dotenv import load_dotenv

    from .failure_pilot import CONDITIONS

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("prepare", "run", "resume", "grade"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--conditions", default=",".join(CONDITIONS),
                        help="Comma-separated subset of: " + ", ".join(CONDITIONS))
    parser.add_argument("--action-format", choices=ACTION_FORMATS, default="text")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    os.environ["MODEL_PROFILE"] = PROFILE
    out = args.output.resolve()
    if args.stage == "prepare":
        prepare(out, args.source.resolve(), [c.strip() for c in args.conditions.split(",") if c.strip()],
                args.action_format)
        return
    if args.stage == "grade":
        grade(out)
        return
    state = read_json(out / "status.json")["state"]
    expected = "stopped" if args.stage == "resume" else "prepared"
    if state != expected:
        raise ValueError(f"Cannot {args.stage} from state {state}; expected {expected}")
    lock = out / "execution.lock"
    with lock.open("x") as marker:
        marker.write(str(os.getpid()))
    try:
        with (out / "batch.log").open("a", encoding="utf-8", buffering=1) as log, \
                contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            try:
                execute(out, resume=args.stage == "resume")
            except BaseException as exc:
                previous = read_json(out / "status.json")
                write_json(out / "status.json", {**previous, "state": "stopped", "error_type": type(exc).__name__})
                raise
    finally:
        lock.unlink()


if __name__ == "__main__":
    main()
