"""Record a finished OpenRouter Failure50 run and classify its unresolved trials.

    python -m src.openrouter_failure_audit --run results/failure50_qwen3_32b_v3 \
        --output audit/qwen3_32b_openrouter/failure50_v3_results.json [--replay]

``--replay`` runs each empty-patch trial's commands again, in order, in a clean
SWE-bench evaluation container (same cwd, interpreter, and environment variables
as mini-swe-agent's swebench config) and records the final working tree. It
separates edits that changed nothing from changes that ``git diff > patch.txt``
missed. It needs the host Docker daemon and the local evaluation images.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections import Counter
from pathlib import Path

from .failure_corpus import read_json, write_json

SUBMIT = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
AGENT_ENV = {"PAGER": "cat", "MANPAGER": "cat", "LESS": "-R", "PIP_PROGRESS_BAR": "off", "TQDM_DISABLE": "1"}
SOURCE_EDIT = re.compile(r"sed -i|cat <<|> *\S*\.py\b|\bmv \S+")
SCRATCH_FILE = re.compile(r"(test|repro|verify)\w*\.(py|sh)\b|patch\.txt")
CATEGORIES = ("context_overflow", "repeated_format_error", "time_limit", "empty_no_edit",
              "empty_edit_no_change", "wrong_patch", "resolved")


def commands(messages: list[dict]) -> list[str]:
    return [a["command"] for m in messages if m["role"] == "assistant"
            for a in (m.get("extra") or {}).get("actions", [])]


def observations(messages: list[dict]) -> list[dict]:
    return [m["extra"] for m in messages if m["role"] == "user" and "returncode" in (m.get("extra") or {})]


def source_edits(cmds: list[str]) -> list[str]:
    """Commands that try to change repository files, not scratch scripts or patch.txt."""
    return [c for c in cmds if SOURCE_EDIT.search(c) and not SCRATCH_FILE.search(c)]


def classify(record: dict, cmds: list[str], resolved: bool) -> str:
    if resolved:
        return "resolved"
    if record.get("error"):
        return "context_overflow"
    if record["exit_status"] == "RepeatedFormatError":
        return "repeated_format_error"
    if record["exit_status"] == "TimeExceeded":
        return "time_limit"
    if record["model_patch"]:
        return "wrong_patch"
    return "empty_edit_no_change" if source_edits(cmds) else "empty_no_edit"


def trial_row(run: Path, record: dict, resolved: bool, failure_reasons: dict) -> dict:
    messages = read_json(run / "runs" / record["condition"] / record["instance_id"] / "trajectory.json")["messages"]
    cmds, obs = commands(messages), observations(messages)
    interrupts = [(m.get("extra") or {}) for m in messages if (m.get("extra") or {}).get("interrupt_type")]
    patch = record["model_patch"] or ""
    return {
        "instance_id": record["instance_id"], "condition": record["condition"],
        "resolved": resolved, "category": classify(record, cmds, resolved),
        "exit_status": record["exit_status"], "error_type": (record.get("error") or {}).get("type"),
        "commands": len(cmds), "source_edit_commands": len(source_edits(cmds)),
        "failed_sed_commands": sum(1 for c, o in zip(cmds, obs) if "sed" in c and o["returncode"] != 0),
        "format_errors": sum(1 for e in interrupts if e["interrupt_type"] == "FormatError"),
        "output_limit_stops": sum(1 for e in interrupts
                                  if ((e.get("response") or {}).get("choices") or [{}])[0].get("finish_reason") == "length"),
        "patch_chars": len(patch),
        "patch_sha256": hashlib.sha256(patch.encode()).hexdigest() if patch else None,
        "grader_failure_reason": failure_reasons.get(record["instance_id"]) if patch else None,
        "elapsed_seconds": round(record["elapsed_seconds"], 1),
    }


def _exec(container: str, cmd: str, timeout: int) -> tuple[object, str]:
    env = [arg for k, v in AGENT_ENV.items() for arg in ("-e", f"{k}={v}")]
    try:
        p = subprocess.run(["docker", "exec", "-w", "/testbed", *env, container, "bash", "-c", cmd],
                           capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
        return p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        return "timeout", ""


def replay(run: Path, condition: str, instance_id: str, timeout: int = 180) -> dict:
    """Run the trial's commands up to submission and return the final working tree."""
    cmds = commands(read_json(run / "runs" / condition / instance_id / "trajectory.json")["messages"])
    image = f"swebench/sweb.eval.x86_64.{instance_id.replace('__', '_1776_')}:latest"
    container = subprocess.run(["docker", "run", "-d", "--rm", image, "sleep", "1800"],
                               capture_output=True, text=True, check=True).stdout.strip()
    try:
        for cmd in cmds:
            before_submit = cmd.split(f"echo {SUBMIT}")[0].rstrip(" &;")
            if before_submit:
                _exec(container, before_submit, timeout)
            if SUBMIT in cmd:
                break
        _, diff = _exec(container, "git diff --stat", 60)
        _, status = _exec(container, "git status --porcelain --untracked-files=all", 60)
        _, size = _exec(container, "wc -c < patch.txt", 60)
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)
    untracked = sorted(line[3:] for line in status.splitlines() if line.startswith("?? ") and line[3:] != "patch.txt")
    return {"patch_txt_bytes": int(size.strip()) if size.strip().isdigit() else None,
            "tracked_diff": diff.strip() or None, "untracked_files": untracked}


def audit(run: Path, do_replay: bool = False) -> dict:
    manifest, summary = read_json(run / "manifest.json"), read_json(run / "summary.json")
    resolved = {c: set(g["resolved_ids"]) for c, g in summary["grading"].items()}
    reasons = {c: read_json(run / g["report"]).get("failure_reasons", {}) for c, g in summary["grading"].items()}
    rows = [trial_row(run, r, r["instance_id"] in resolved[r["condition"]], reasons[r["condition"]])
            for r in read_json(run / "records.json")]
    if do_replay:
        for row in rows:
            if row["category"] == "empty_edit_no_change":
                row["replay"] = replay(run, row["condition"], row["instance_id"])
    by_condition = {c: dict(Counter(r["category"] for r in rows if r["condition"] == c))
                    for c in manifest["conditions"]}
    keep = ("created_utc", "study", "source_experiment", "source_manifest_sha256", "trace_source_commit",
            "corpus_sha256", "conditions", "attempts_per_condition", "tasks_sha256", "action_format",
            "model_profile", "provider", "model_name", "model_settings", "context_limit_policy",
            "versions", "source_sha256", "git_commit")
    return {
        "run": run.as_posix(), "manifest": {k: manifest[k] for k in keep if k in manifest},
        "trial_order": manifest["trial_order"],
        "resolved": summary["resolved"], "denominator_per_condition": summary["denominator_per_condition"],
        "grading": summary["grading"], "exit_statuses": summary["exit_statuses"], "usage": summary["usage"],
        "failure_categories": {"total": dict(Counter(r["category"] for r in rows)), "by_condition": by_condition},
        "replay_method": ("Commands replayed in order in a clean evaluation container with mini-swe-agent's "
                          "swebench environment (cwd /testbed, bash -c, agent env vars)") if do_replay else None,
        "trials": rows,
        "interpretation": summary["interpretation"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replay", action="store_true")
    args = parser.parse_args()
    report = audit(args.run, args.replay)
    write_json(args.output, report)
    print(json.dumps(report["failure_categories"], indent=2))


if __name__ == "__main__":
    main()
