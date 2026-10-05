"""Export a key-free, source-free audit of a completed Verified batch."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from .evaluate import validate_grading_report

CONDITIONS = ("no_memory", "flat_rag", "graph")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def trajectory_metrics(trajectory: dict) -> dict:
    messages = trajectory.get("messages", [])
    # Format-error messages can also carry the billable raw response.
    responses = [m.get("extra", {}).get("response") for m in messages]
    responses = [response for response in responses if isinstance(response, dict)]
    usages = [r["usage"] for r in responses if isinstance(r.get("usage"), dict)]
    return {
        "model_calls": trajectory["info"]["model_stats"]["api_calls"],
        "responses_with_usage": len(usages),
        "input_tokens": sum(u.get("prompt_tokens", 0) or 0 for u in usages),
        "output_tokens": sum(u.get("completion_tokens", 0) or 0 for u in usages),
        "format_errors": sum(m.get("role") == "user" and (
            str(m.get("content", "")).startswith("Tool call error:") or
            str(m.get("content", "")).startswith("Your previous response reached the output token limit")
        ) for m in messages),
        "tool_repairs": dict(Counter(repair["kind"] for response in responses
                                     for repair in response.get("agent_tool_repairs", []))),
    }


def collect(root: Path) -> dict:
    if read(root / "status.json")["state"] != "complete":
        raise ValueError("Batch and official grading must be complete before final export")
    manifest = read(root / "manifest.json")
    summary = read(root / "summary.json")
    records = read(root / "records.json")
    planned = [(t["instance_id"], t["condition"]) for t in manifest["trial_order"]]
    if len(records) != 30 or [(r["instance_id"], r["condition"]) for r in records] != planned:
        raise ValueError("Expected exactly the thirty frozen trial records")
    task_ids = {task_id for task_id, _ in planned}
    grading = {}
    for condition in CONDITIONS:
        report_path = Path(summary["grading"][condition]["report"])
        report = read(report_path)
        run_id = f"{root.name}_{condition}"
        logs = root / "logs" / "run_evaluation" / run_id / manifest["model_name"].replace("/", "__")
        rejected = validate_grading_report(report, logs)
        if set(report["submitted_ids"]) != task_ids:
            raise ValueError("Grading does not cover every planned task")
        grading[condition] = {**report, "report_sha256": digest(report_path), "rejected_patches": rejected}
    rows = []
    for record in records:
        task_id, condition = record["instance_id"], record["condition"]
        folder = root / "runs" / condition / task_id
        trajectory = read(folder / "trajectory.json")
        retrieval = read(folder / "retrieval.json")
        context = retrieval["context"]
        submitted = record["exit_status"] == "Submitted" and bool(record["model_patch"].strip())
        official_resolved = task_id in grading[condition]["resolved_ids"]
        if official_resolved and not submitted:
            raise ValueError("Grader resolved a task without an explicit patch submission")
        rows.append({
            "instance_id": task_id, "repo": record["repo"], "condition": condition,
            "exit_status": record["exit_status"], "submitted_patch": submitted,
            "patch_apply_failure": task_id in grading[condition]["rejected_patches"],
            "resolved": official_resolved, "elapsed_seconds": round(record["elapsed_seconds"], 3),
            "patch_chars": len(record["model_patch"]),
            "patch_sha256": hashlib.sha256(record["model_patch"].encode()).hexdigest(),
            "trajectory_sha256": digest(folder / "trajectory.json"),
            "retrieval_sha256": digest(folder / "retrieval.json"),
            "context_chars": len(context), "context_sha256": hashlib.sha256(context.encode()).hexdigest(),
            "selected_count": len(retrieval["selected"]),
            "common_seed_chunk_ids": retrieval["metadata"].get("common_seed_chunk_ids", []),
            "graph_neighbors_added": retrieval["metadata"].get("graph_neighbors_added", 0),
            "relations_followed": retrieval["metadata"].get("relations_followed", {}),
            **trajectory_metrics(trajectory),
        })
    totals = {}
    for condition in CONDITIONS:
        selected = [r for r in rows if r["condition"] == condition]
        totals[condition] = {
            "attempts": len(selected), "resolved": sum(r["resolved"] for r in selected),
            "submitted_patches": sum(r["submitted_patch"] for r in selected),
            "patch_apply_failures": sum(r["patch_apply_failure"] for r in selected),
            "exits": dict(Counter(r["exit_status"] for r in selected)),
            **{key: sum(r[key] for r in selected) for key in
               ("model_calls", "responses_with_usage", "input_tokens", "output_tokens", "format_errors")},
            "elapsed_seconds": round(sum(r["elapsed_seconds"] for r in selected), 3),
            "cost_usd": None,
        }
    paired = []
    exposure = []
    for task_id in dict.fromkeys(t["instance_id"] for t in manifest["trial_order"]):
        paired.append({"instance_id": task_id, **{r["condition"]: r["resolved"] for r in rows
                                                   if r["instance_id"] == task_id}})
        arms = {r["condition"]: r for r in rows if r["instance_id"] == task_id}
        if arms["no_memory"]["context_chars"] != 0:
            raise ValueError("No-memory arm received memory")
        if any(arms[c]["context_chars"] > manifest["config"]["memory"]["character_budget"] for c in CONDITIONS):
            raise ValueError("Memory character budget exceeded")
        if arms["flat_rag"]["common_seed_chunk_ids"] != arms["graph"]["common_seed_chunk_ids"]:
            raise ValueError("Flat/graph seed rankings differ")
        exposure.append({"instance_id": task_id, "identical_common_seeds": True,
                         "different_memory_contexts": arms["flat_rag"]["context_sha256"] != arms["graph"]["context_sha256"],
                         "graph_neighbors_added": arms["graph"]["graph_neighbors_added"],
                         "relations_followed": arms["graph"]["relations_followed"]})
    incidents = []
    if manifest.get("infrastructure_predecessor"):
        incidents.append(manifest["infrastructure_predecessor"])
    timeout_record = root / "recovery" / "docker_start_timeout.json"
    if timeout_record.exists():
        incidents.append(read(timeout_record))
    service_record = root / "recovery" / "service_unavailable_retry.json"
    if service_record.exists():
        incidents.append(read(service_record))
    grading_record = root / "recovery" / "grading_guard_recovery.json"
    if grading_record.exists():
        incidents.append(read(grading_record))
    probe_record = root / "recovery" / "inference_health_probe.json"
    migration_path = root / "recovery" / "runner_migration.json"
    return {
        "experiment": root.name, "dataset": manifest["dataset"], "split": manifest["split"],
        "created_utc": manifest["created_utc"], "model_name": manifest["model_name"],
        "model_settings": manifest["model_settings"], "agent_overrides": manifest["agent_overrides"],
        "memory_settings": manifest["config"]["memory"], "versions": manifest["versions"],
        "selection": manifest["selection"], "scoring": manifest["scoring"],
        "seed": manifest["config"]["seed"], "task_snapshot_sha256": manifest["task_snapshot_sha256"],
        "memory_sha256": manifest["memory_sha256"], "manifest_sha256": digest(root / "manifest.json"),
        "source_sha256": manifest["source_sha256"], "git_commit_at_freeze": manifest["git_commit"],
        "trial_order": manifest["trial_order"], "totals": totals, "paired_results": paired,
        "trials": rows, "official_reports": grading, "retrieval_exposure": exposure,
        "infrastructure_incidents": incidents,
        "recovery_inference_probe": read(probe_record) if probe_record.exists() else None,
        "runner_migration": read(migration_path) if migration_path.exists() else None,
        "evaluation_source_sha256": digest(Path(__file__).with_name("evaluate.py")),
        "cost_note": "Unknown: zero cost in model statistics is not evidence of zero billing.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--analysis", type=Path, help="Also write the repository analysis document")
    args = parser.parse_args()
    report = collect(args.input.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.analysis:
        args.analysis.parent.mkdir(parents=True, exist_ok=True)
        args.analysis.write_text(render_analysis(report), encoding="utf-8")
    print(json.dumps(report["totals"], indent=2))


def render_analysis(report: dict) -> str:
    totals = report["totals"]
    names = {"no_memory": "No memory", "flat_rag": "Flat RAG", "graph": "Graph"}
    lines = [
        "# SWE-bench Verified: ten-task GPT-OSS 20B comparison", "",
        "Official resolved results: " + "; ".join(
            f"**{names[c]} {totals[c]['resolved']}/10**" for c in CONDITIONS) + ".", "",
        "Thirty scored attempts cover the same ten problems, one per condition. Infrastructure-interrupted "
        "attempts and diagnostic requests are recorded separately below. All submitted patches "
        "were processed by the official SWE-bench harness; empty, unsubmitted, or rejected patches count "
        "as unresolved. Grading completed without reported infrastructure errors. "
        "This is an exploratory comparison, not a reliable estimate of a general memory advantage.", "",
        "## Conditions and results", "",
        "| Condition | Resolved / 10 | Nonempty submissions | Patch rejections | Format-error exits | Limit exits | Model calls | Input tokens | Output tokens | Agent time (min) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for c in CONDITIONS:
        t = totals[c]
        lines.append(f"| {names[c]} | {t['resolved']} | {t['submitted_patches']} | {t['patch_apply_failures']} | "
                     f"{t['exits'].get('RepeatedFormatError', 0)} | {t['exits'].get('LimitsExceeded', 0)} | "
                     f"{t['model_calls']} | {t['input_tokens']:,} | {t['output_tokens']:,} | {t['elapsed_seconds']/60:.1f} |")
    lines += ["", "Agent time sums recorded per-attempt durations; it excludes retrieval, environment "
              "startup, grading, and the pause for infrastructure recovery. Timing is descriptive, "
              "not evidence that a memory method causes a speed difference. Dollar cost is unknown: "
              "this deployment has no usable LiteLLM price mapping.", "",
              "## Paired task outcomes", "",
              "Each cell is the official resolution score followed by the agent exit status.", "",
              "| Task | No memory | Flat RAG | Graph |", "|---|---|---|---|"]
    for pair in report["paired_results"]:
        arms = {r["condition"]: r for r in report["trials"] if r["instance_id"] == pair["instance_id"]}
        lines.append("| " + pair["instance_id"] + " | " + " | ".join(
            f"{int(pair[c])}; {arms[c]['exit_status']}" for c in CONDITIONS) + " |")
    lines += ["", "## Paired differences", "",
              "These counts compare outcomes on identical problems. They are descriptive and do not establish causation.", "",
              "| Comparison (A versus B) | Only A resolved | Only B resolved | Both resolved | Neither resolved |",
              "|---|---:|---:|---:|---:|"]
    for a, b in (("flat_rag", "no_memory"), ("graph", "no_memory"), ("graph", "flat_rag")):
        pairs = report["paired_results"]
        counts = (sum(p[a] and not p[b] for p in pairs), sum(p[b] and not p[a] for p in pairs),
                  sum(p[a] and p[b] for p in pairs), sum(not p[a] and not p[b] for p in pairs))
        lines.append(f"| {names[a]} versus {names[b]} | " + " | ".join(map(str, counts)) + " |")
    exposure = report["retrieval_exposure"]
    relations = Counter()
    for row in exposure:
        relations.update(row["relations_followed"])
    usage_matches = all(r["model_calls"] == r["responses_with_usage"] for r in report["trials"])
    lines += ["", "## Frozen setup and retrieval exposure", "",
              "- Dataset: `SWE-bench/SWE-bench_Verified`, test split; an exact local ten-task snapshot was used for both attempts and grading.",
              "- Model: Azure GPT-OSS 20B (`" + report["model_name"] + "`), temperature 0, medium reasoning; mini-SWE-agent 2.4.6 and swebench 5.0.2.",
              "- Agent: native bash tools with the existing narrowly specified tool-call repairs, 100 steps and a 3600-second agent wall-time limit; no model-failure retries or automatic patch recovery.",
              "- Memory: static task-start retrieval from the same frozen corpus, current repository excluded, a 24,000-character budget for each memory arm, and the same issue wrapper with `[none]` for the baseline.",
              "- Retrieval: BM25 seed pool 8; graph-v2 uses two hops, semantic-first traversal, at most eight neighbors, and the existing relation allowlist. Settings were not tuned on these outcomes.",
              "- Selection: the existing deterministic seed-42 sampler, with seven Django problems and one each from Sphinx, Xarray, and pytest. The condition order was frozen before model calls and varies across tasks.", "",
              f"All ten flat/graph pairs have identical common seed IDs. {sum(r['different_memory_contexts'] for r in exposure)}/10 pairs "
              "have different final context hashes. No-memory contexts are empty and every memory context stays within budget. "
              f"Graph added {sum(r['graph_neighbors_added'] for r in exposure)} neighbors across the ten attempts. "
              "Traversed relations: " + ", ".join(f"`{key}` ({value})" for key, value in sorted(relations.items())) + ".", "",
              "Usage coverage: " + ("every recorded model call has a saved response with token usage." if usage_matches else
              "some calls lack saved usage; token sums cover only responses carrying usage, as shown in the JSON audit."), "",
              "## Infrastructure recovery", "",
              "The first launch stopped after one API request because the local cost calculator did not recognize the deployment. "
              "No task commands had executed. That launch is retained separately; the restart used `cost_tracking=ignore_errors` "
              "without changing model, prompts, retrieval, or limits. Its one extra pre-action request is excluded from the main "
              "30-attempt usage totals; its token usage and billing are not recovered.", "",
              "After 18 completed attempts, starting the Xarray container exceeded the 120-second Docker startup timeout "
              "while its image was uncached. No model call occurred for that interrupted start. The empty attempt directory "
              "and incident were archived, the remaining images were downloaded, and the batch resumed from the next untouched "
              "task/condition. All 18 completed records were verified and preserved, including model failures. The runner migration "
              "added resume and grading checks; the agent, model, and retrieval source hashes stayed fixed.", "",
              "## Interpretation and limitations", "",
              "The table answers which submitted patches passed for these ten problems under this specific agent configuration. "
              "It does not establish that a method is generally better. The sample is small and heavily weighted toward Django, "
              "there is only one scored attempt per task/condition, and protocol failures reduce the opportunity to evaluate patch quality. "
              "A submitted patch is not itself a solved task; only the official resolution labels determine the scores.", "",
              "Three selected problems (`django__django-14672`, `sphinx-doc__sphinx-10449`, and `django__django-11299`) had "
              "already been used in GPT-5.1 smoke runs. This is therefore not an entirely fresh held-out sample. The Verified "
              "batch is separate from the stopped July SWE-rebench calibration and does not revise that earlier result.", "",
              "## Artifacts and reproduction", "",
              "The key-free machine-readable record is [verified10_results.json](../audit/gpt_oss_20b/verified10_results.json). "
              "It contains task/condition results, official reports, calls and tokens, exit statuses, retrieval exposure, "
              "artifact hashes, source hashes, and the infrastructure-recovery record. Raw prompts, patches, retrieved text, "
              "credentials, trajectories, and grader logs remain in ignored `results/`.", "", "```bash",
              "python -m src.verified_report \\",
              "  --input results/" + report["experiment"] + " \\",
              "  --output audit/gpt_oss_20b/verified10_results.json \\",
              "  --analysis docs/gpt_oss_20b_verified10_results.md", "```", ""]
    pairs = report["paired_results"]
    rescued = {c: sum(p[c] and not p["no_memory"] for p in pairs) for c in ("flat_rag", "graph")}
    interpretation = (
        f"Flat RAG solved {rescued['flat_rag']} problems that no memory missed; graph solved {rescued['graph']}. "
        f"Graph had {totals['graph']['exits'].get('RepeatedFormatError', 0)} format-error exits, compared with "
        f"{totals['no_memory']['exits'].get('RepeatedFormatError', 0)} for no memory and "
        f"{totals['flat_rag']['exits'].get('RepeatedFormatError', 0)} for flat RAG. "
        f"Flat RAG also had {totals['flat_rag']['patch_apply_failures']} rejected submissions. "
        "Protocol completion and patch formatting are therefore material limitations in interpreting the memory comparison; "
        "these observations do not show that memory caused the failures."
    )
    lines[lines.index("## Interpretation and limitations") + 2:lines.index("## Interpretation and limitations") + 2] = [interpretation, ""]
    index = lines.index("## Interpretation and limitations")
    grading_note = ["The initial grading guard stopped on `error_ids` for malformed or inapplicable patches. "
                    "It was corrected to distinguish the harness's logged `Patch Apply Failed` errors from unexplained "
                    "evaluation failures. Patch rejections remain unresolved, raw official error IDs are retained, "
                    "and no agent attempt or submitted patch was changed. Grading resumed using the existing predictions.", ""]
    ambiguous = {c: report["official_reports"][c].get("failure_reasons", {}) for c in CONDITIONS
                 if report["official_reports"][c].get("ambiguous_failure_instances", 0)}
    if ambiguous:
        grading_note += ["The harness also reports ambiguous test-environment failure indicators: `" + json.dumps(ambiguous, sort_keys=True) +
                         "`. These retain the official unresolved label; they are not claimed to be proven infrastructure failures or proof of patch correctness.", ""]
    lines[index:index] = grading_note
    service_incidents = [x for x in report["infrastructure_incidents"] if x.get("failure_type") == "ServiceUnavailableError"]
    if service_incidents:
        index = lines.index("## Interpretation and limitations")
        extra = []
        for incident in service_incidents:
            metrics = incident["original_metrics"]
            extra += [
                f"A later `{incident['condition']}` attempt on `{incident['task']}` was interrupted by the endpoint error "
                "`no healthy upstream`. Its original trajectory, retrieval, and run record were archived, and a small "
                "inference probe confirmed recovery before an explicitly documented infrastructure retry. "
                "The other 28 completed attempts were retained. This was an additional model-backed start, not a "
                "retry of an ordinary format-error or limit exit.", "",
                f"The interrupted attempt recorded {metrics['model_calls']} model calls, of which "
                f"{metrics['responses_with_usage']} have saved token usage: {metrics['input_tokens']:,} input and "
                f"{metrics['output_tokens']:,} output tokens. These are excluded from the main table and preserved "
                "in the infrastructure audit. Failed transport retries may not have usage records. "
                "The recovery probe used 70 input and 24 output tokens, also outside the scored attempts.", "",
            ]
        lines[index:index] = extra
    return "\n".join(lines)


if __name__ == "__main__":
    main()
