"""Freeze, precompute, execute and grade the new five-arm Verified pilot.

GPU retrieval is precomputed on the host; the unchanged solver runs in the research
container and consumes immutable JSON bundles without neural dependencies.
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import dataclasses
import json
import os
import random
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .failure_corpus import DEFAULT_CONFIG, ROOT, canonical, config, digest, read_json, rooted, write_json

CONDITIONS = ("no_memory", "checklist", "bm25", "hybrid_rag", "graph_rag")
SOURCE_FILES = ("src/failure_corpus.py", "src/failure_retrieval.py", "src/failure_pilot.py",
                "src/experiment.py", "src/gpt_oss_tool_model.py", "src/model_profiles.py", "src/evaluate.py", "src/verified_report.py",
                "config/failure_memory.yaml", "config/models/gpt_oss_20b.yaml", "config/experiment.yaml",
                "config/graph_v2.yaml", "requirements-retrieval.txt")


def normalized_issue(text):
    return re.sub(r"\s+", " ", text).strip().casefold()


def issue_links(text):
    return set(re.findall(r"https?://github\.com/[^\s/]+/[^\s/]+/(?:issues|pull)/\d+", text.casefold()))


def prior_attempts(root=ROOT / "results"):
    ids = set()
    for path in root.rglob("run.json"):
        data = read_json(path)
        if data.get("instance_id"):
            ids.add(data["instance_id"])
    # Old snapshots are already examined and should not become fresh pilot tasks.
    for path in root.rglob("tasks.json"):
        data = read_json(path)
        if isinstance(data, list):
            ids.update(x["instance_id"] for x in data if isinstance(x, dict) and x.get("instance_id"))
    return ids


def select_tasks(tasks, source_cases, previous, *, seed=42, development=20, evaluation=10):
    source_ids = {r["id"] for r in source_cases}
    texts = {normalized_issue(r["problem_statement"]) for r in source_cases}
    links = set().union(*(issue_links(r["problem_statement"]) for r in source_cases))
    excluded, groups = [], collections.defaultdict(list)
    for task in sorted(tasks, key=lambda t: t["instance_id"]):
        reason = None
        if task["instance_id"] in source_ids:
            reason = "source_issue"
        elif task["instance_id"] in previous:
            reason = "previously_exposed"
        elif normalized_issue(task["problem_statement"]) in texts:
            reason = "duplicate_issue_text"
        elif issue_links(task["problem_statement"]) & links:
            reason = "shared_issue_or_pr_link"
        if reason:
            excluded.append({"instance_id": task["instance_id"], "reason": reason})
        else:
            groups[task["repo"]].append(task)
    rng = random.Random(seed)
    repos = sorted(groups)
    rng.shuffle(repos)
    for repo in repos:
        rng.shuffle(groups[repo])
    chosen, chosen_texts, chosen_links = [], set(texts), set(links)
    while len(chosen) < development + evaluation:
        progress = False
        for repo in repos:
            while groups[repo]:
                task = groups[repo].pop()
                text, refs = normalized_issue(task["problem_statement"]), issue_links(task["problem_statement"])
                if text in chosen_texts or refs & chosen_links:
                    excluded.append({"instance_id": task["instance_id"], "reason": "duplicate_selected_issue"})
                    continue
                chosen.append(task)
                chosen_texts.add(text)
                chosen_links.update(refs)
                progress = True
                break
            if len(chosen) == development + evaluation:
                break
        if not progress:
            raise ValueError("Not enough independent eligible tasks")
    return chosen[:development], chosen[development:], excluded


def trial_order(tasks):
    # Balanced Latin rotations and reversed rotations: each arm occupies each
    # serial position twice across ten tasks.
    orders = [CONDITIONS[i:] + CONDITIONS[:i] for i in range(5)]
    orders += [tuple(reversed(order)) for order in orders]
    return [{"instance_id": task["instance_id"], "condition": condition}
            for index, task in enumerate(tasks) for condition in orders[index % 10]]


def prepare(out, cfg):
    if out.exists():
        raise FileExistsError(out)
    from datasets import load_dataset
    corpus = rooted(cfg["corpus"])
    corpus_manifest = read_json(corpus / "manifest.json")
    if digest(corpus / "corpus.sqlite") != corpus_manifest["corpus_sha256"]:
        raise ValueError("Corrupt corpus")
    with sqlite3.connect((corpus / "corpus.sqlite").as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        cases = [dict(r) for r in db.execute("SELECT * FROM cases")]
    selection = cfg["pilot"]
    tasks = [dict(t) for t in load_dataset(selection["dataset"], revision=selection["revision"], split="test", token=False)]
    previous = prior_attempts()
    dev, pilot, excluded = select_tasks(tasks, cases, previous, seed=selection["seed"],
                                       development=selection["development_tasks"], evaluation=selection["evaluation_tasks"])
    out.mkdir(parents=True)
    # Grading snapshots include evaluator fields; retrieval receives only public task fields.
    write_json(out / "tasks.json", pilot)
    write_json(out / "development.json", [{k: t[k] for k in ("instance_id", "repo", "problem_statement")} for t in dev])
    write_json(out / "selection_audit.json", {"excluded": excluded, "previous_ids": sorted(previous),
                                              "source_ids": sorted(r["id"] for r in cases)})
    manifest = {"created_utc": datetime.now(timezone.utc).isoformat(), "config": cfg,
                "source_commit": corpus_manifest["source"]["commit"], "corpus_sha256": corpus_manifest["corpus_sha256"],
                "corpus_manifest_sha256": digest(corpus / "manifest.json"),
                "snapshot_sha256": {p: digest(out / p) for p in ("tasks.json", "development.json", "selection_audit.json")},
                "trial_order": trial_order(pilot), "source_sha256": {p: digest(ROOT / p) for p in SOURCE_FILES},
                "attempts_per_condition": 1, "study": "Exploratory diagnostic; same-project different-issue transfer"}
    write_json(out / "manifest.json", manifest)
    write_json(out / "status.json", {"state": "prepared", "completed": 0, "total": len(manifest["trial_order"])})
    print(json.dumps({"development": len(dev), "pilot": len(pilot), "trials": len(manifest["trial_order"])}))


def validate(out):
    m = read_json(out / "manifest.json")
    for rel, expected in m["source_sha256"].items():
        if digest(ROOT / rel) != expected:
            raise ValueError(f"Frozen code/config changed: {rel}")
    for rel, expected in m["snapshot_sha256"].items():
        if digest(out / rel) != expected:
            raise ValueError(f"Frozen task snapshot changed: {rel}")
    corpus = rooted(m["config"]["corpus"])
    if digest(corpus / "corpus.sqlite") != m["corpus_sha256"] or digest(corpus / "manifest.json") != m["corpus_manifest_sha256"]:
        raise ValueError("Frozen memory changed")
    return m


def precompute(out):
    from .failure_retrieval import FailureMemory
    m = validate(out)
    if (out / "contexts_manifest.json").exists():
        raise FileExistsError("Contexts already frozen")
    memory = FailureMemory(m["config"])
    files, audit = {}, []
    try:
        if not (out / "source_citation_diagnostics.json").exists():
            write_json(out / "source_citation_diagnostics.json", memory.citation_diagnostics())
        for split, tasks in (("development", read_json(out / "development.json")), ("pilot", read_json(out / "tasks.json"))):
            for task in tasks:
                directory = out / "contexts" / split / task["instance_id"]
                directory.mkdir(parents=True, exist_ok=True)
                # Only complete bundles may be reused after an interrupted precomputation.
                paths = [directory / f"{name}.json" for name in CONDITIONS]
                if not all(p.exists() for p in paths):
                    results = memory.retrieve_all(task["problem_statement"], instance_id=task["instance_id"], diagnostics=split == "development")
                    for name, result in results.items():
                        write_json(directory / f"{name}.json", dataclasses.asdict(result))
                for path in paths:
                    result = read_json(path)
                    if result["query"] != task["problem_statement"] or result["condition"] != path.stem:
                        raise ValueError("Context bundle identity mismatch")
                    files[path.relative_to(out).as_posix()] = digest(path)
                    audit.append({"split": split, "instance_id": task["instance_id"], "condition": result["condition"],
                                  "tokens": result["metadata"]["memory_tokens"], "sources": len({r["case_id"] for r in result["selected"]}),
                                  "records": len(result["selected"]),
                                  "graph_records": sum(bool(r.get("graph_path")) for r in result["selected"])})
                print(f"CONTEXT {split} {task['instance_id']}", flush=True)
        write_json(out / "retrieval_audit.json", audit)
        write_json(out / "contexts_manifest.json", {"files": files, "embeddings_sha256": digest(memory.out / "embeddings.npy"),
                                                    "retrieval_audit_sha256": digest(out / "retrieval_audit.json"),
                                                    "source_citation_diagnostics_sha256": digest(out / "source_citation_diagnostics.json")})
        write_json(out / "status.json", {"state": "ready", "completed": 0, "total": len(m["trial_order"])})
    finally:
        memory.close()


def validate_contexts(out):
    frozen = read_json(out / "contexts_manifest.json")
    for rel, expected in frozen["files"].items():
        if digest(out / rel) != expected:
            raise ValueError(f"Frozen context changed: {rel}")


def completed_prefix(out, manifest):
    records = read_json(out / "records.json") if (out / "records.json").exists() else []
    if len(records) > len(manifest["trial_order"]):
        raise ValueError("Too many records")
    for record, trial in zip(records, manifest["trial_order"]):
        if any(record[k] != trial[k] for k in ("instance_id", "condition")):
            raise ValueError("Records are not a completed prefix")
        path = out / "runs" / trial["condition"] / trial["instance_id"] / "run.json"
        if record != read_json(path):
            raise ValueError("Recorded attempt changed")
        if record.get("error"):
            raise ValueError("Interrupted attempt needs documented recovery; it will not be retried automatically")
    return records


def execute(out, resume=False):
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    os.environ["MODEL_PROFILE"] = "gpt_oss_20b"
    from importlib.metadata import version
    from .experiment import load_config, run_one
    from .memory_conditions import MemoryResult
    from .model_profiles import resolve_profile
    from .verified_batch import OVERRIDES
    manifest = validate(out)
    validate_contexts(out)
    state = read_json(out / "status.json")["state"]
    if (resume and state != "stopped") or (not resume and state != "ready"):
        raise ValueError(f"Cannot launch from state {state}")
    profile = resolve_profile("gpt_oss_20b")
    identity = {"model_name": profile["model_name"], "deployment": profile["deployment"],
                "settings": {k: v for k, v in profile["model_kwargs"].items() if k not in ("api_key", "api_base")},
                "agent_overrides": OVERRIDES, "versions": {p: version(p) for p in ("mini-swe-agent", "swebench", "litellm")}}
    if (out / "solver.json").exists():
        if read_json(out / "solver.json") != identity:
            raise ValueError("Solver identity changed")
    else:
        write_json(out / "solver.json", identity)
    records = completed_prefix(out, manifest)
    tasks = {t["instance_id"]: t for t in read_json(out / "tasks.json")}
    cfg = load_config()
    # Every trial supplies prepared memory, so the legacy bootstrap is unreachable.
    cfg["memory"] = {"prepared_corpus": manifest["corpus_sha256"]}
    lock = out / "execution.lock"
    with lock.open("x") as stream:
        stream.write(str(os.getpid()))
    total = len(manifest["trial_order"])
    try:
        with (out / "batch.log").open("a", encoding="utf-8", buffering=1) as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            for trial in manifest["trial_order"][len(records):]:
                task_id, condition = trial["instance_id"], trial["condition"]
                bundle = read_json(out / "contexts/pilot" / task_id / f"{condition}.json")
                write_json(out / "status.json", {"state": "running", "completed": len(records), "total": total, "current": trial})
                print(f"START {len(records)+1}/{total} {task_id} {condition}", flush=True)
                record = run_one(tasks[task_id], condition, cfg, root=out / "runs", agent_overrides=OVERRIDES,
                                 uniform_wrapper=True, prepared_memory=MemoryResult(**bundle))
                records.append(record)
                write_json(out / "records.json", records)
                print(f"DONE exit={record['exit_status']} patch_chars={len(record['model_patch'])}", flush=True)
                if record.get("error"):
                    raise RuntimeError("Infrastructure/runner error; inspect preserved attempt")
            write_json(out / "status.json", {"state": "awaiting_grading", "completed": total, "total": total})
    except BaseException as exc:
        write_json(out / "status.json", {"state": "stopped", "completed": len(records), "total": total, "error_type": type(exc).__name__})
        raise
    finally:
        lock.unlink()
    grade(out)


def grade(out):
    from .evaluate import swebench_eval_command, validate_grading_report
    from .experiment import export_predictions
    manifest = validate(out)
    records = completed_prefix(out, manifest)
    if len(records) != len(manifest["trial_order"]):
        raise ValueError("Cannot grade an incomplete pilot")
    solver = read_json(out / "solver.json")
    expected = {t["instance_id"] for t in read_json(out / "tasks.json")}
    reports = {}
    for condition in CONDITIONS:
        predictions = export_predictions([r for r in records if r["condition"] == condition], out / f"predictions_{condition}.jsonl")
        run_id = f"{out.name}_{condition}"
        write_json(out / "status.json", {"state": "grading", "condition": condition, "completed": len(records), "total": len(records)})
        command = swebench_eval_command(str(out / "tasks.json"), predictions, run_id, max_workers=1)
        command[0] = sys.executable
        with (out / "grading.log").open("a", encoding="utf-8") as log:
            result = subprocess.run(command, cwd=out, stdout=log, stderr=subprocess.STDOUT)
        found = list(out.glob(f"*.{run_id}.json"))
        if result.returncode or len(found) != 1:
            raise RuntimeError(f"Grading failed for {condition}")
        report = read_json(found[0])
        logs = out / "logs/run_evaluation" / run_id / solver["model_name"].replace("/", "__")
        rejected = validate_grading_report(report, logs)
        if set(report.get("submitted_ids", [])) != expected:
            raise ValueError("Official grading does not cover every planned task")
        if not set(report.get("resolved_ids", [])) <= expected:
            raise ValueError("Grader returned unexpected task IDs")
        reports[condition] = {"report": found[0].name, "report_sha256": digest(found[0]), "resolved_ids": report.get("resolved_ids", []), "rejected": rejected}
    paired = [{"instance_id": i, **{c: i in reports[c]["resolved_ids"] for c in CONDITIONS}} for i in sorted(expected)]
    from .verified_report import trajectory_metrics
    usage = {c: {"input_tokens": 0, "output_tokens": 0, "calls_with_usage": 0, "model_calls": 0,
                 "format_errors": 0, "elapsed_seconds": 0, "nonempty_submissions": 0} for c in CONDITIONS}
    for record in records:
        path = out / "runs" / record["condition"] / record["instance_id"] / "trajectory.json"
        trajectory = read_json(path)
        metrics = trajectory_metrics(trajectory)
        count = usage[record["condition"]]
        for key in ("input_tokens", "output_tokens", "model_calls", "format_errors"):
            count[key] += metrics[key]
        count["calls_with_usage"] += metrics["responses_with_usage"]
        count["elapsed_seconds"] += record["elapsed_seconds"]
        count["nonempty_submissions"] += int(record["exit_status"] == "Submitted" and bool(record["model_patch"].strip()))
    write_json(out / "summary.json", {"tasks": paired, "grading": reports, "denominator_per_condition": len(expected),
                                      "resolved": {c: len(reports[c]["resolved_ids"]) for c in CONDITIONS},
                                      "exit_statuses": {c: dict(collections.Counter(r["exit_status"] for r in records if r["condition"] == c)) for c in CONDITIONS},
                                      "usage": usage, "dollar_cost": None,
                                      "interpretation": "Ten paired problems, one attempt per condition; exploratory, not a general memory advantage estimate."})
    write_json(out / "status.json", {"state": "complete", "completed": len(records), "total": len(records)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("prepare", "contexts", "run", "resume", "grade"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    out = args.output.resolve()
    if args.stage == "prepare":
        prepare(out, config(args.config))
    elif args.stage == "contexts":
        precompute(out)
    elif args.stage == "grade":
        grade(out)
    else:
        execute(out, resume=args.stage == "resume")


if __name__ == "__main__":
    main()
