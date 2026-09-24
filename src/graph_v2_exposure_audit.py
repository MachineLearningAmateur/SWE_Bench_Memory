"""Audit the proposed graph-v2 policy on the frozen retrieval-development set."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from . import retrieval_exposure_audit as audit

ROOT = Path(__file__).resolve().parents[1]


def _freeze(path: Path, content: str) -> None:
    if path.exists():
        if path.read_text(encoding="utf-8") != content:
            raise ValueError(f"Refusing to overwrite a changed frozen audit: {path}")
        return
    path.write_text(content, encoding="utf-8")


def main() -> None:
    source = ROOT / "audit" / "retrieval_exposure"
    out = ROOT / "audit" / "gpt_oss_20b"
    tasks = [json.loads(line) for line in (source / "source_tasks.jsonl").read_text(encoding="utf-8").splitlines()]
    provenance = json.loads((source / "benchmark_source.json").read_text(encoding="utf-8"))
    if audit._canonical_task_list_sha256(tasks) != provenance["task_metadata_sha256"]:
        raise ValueError("Pinned source hash mismatch")
    dev_ids = json.loads((source / "dev_task_ids.json").read_text(encoding="utf-8"))
    calibration = set(json.loads((out / "calibration_task_ids.json").read_text(encoding="utf-8")))
    pilot = set(json.loads((out / "pilot_task_ids.json").read_text(encoding="utf-8")))
    if len(dev_ids) != 30 or set(dev_ids) & (calibration | pilot):
        raise ValueError("Development partition is not isolated from outcome partitions")
    by_id = {t["instance_id"]: t for t in tasks}
    selected = [by_id[tid] for tid in dev_ids]
    settings = audit.memory_settings(audit.load_frozen_config())
    policy_path = ROOT / "config" / "graph_v2.yaml"
    policy_text = policy_path.read_text(encoding="utf-8")
    settings.update(yaml.safe_load(policy_text))
    db = ROOT / "memory" / "trajdebug_flat_rag_86" / "data" / "flat_rag.sqlite"
    rows, _ = audit.audit_tasks(selected, db_path=db, settings=settings, verbose=True)
    summary = audit.summarize(rows)
    summary["policy"] = {"name": "graph_v2", "settings": settings,
                         "policy_sha256": hashlib.sha256(policy_text.encode("utf-8")).hexdigest(),
                         "task_metadata_sha256": provenance["task_metadata_sha256"],
                         "development_task_ids": dev_ids, "model_calls_made": audit.MODEL_CALLS_MADE}
    _freeze(out / "graph_v2_dev_exposure.jsonl",
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    _freeze(out / "graph_v2_dev_summary.json", json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({k: summary[k] for k in ("tasks_audited", "graph_traversal_activated_pct",
          "graph_has_recovery_repair_pct", "graph_has_contradiction_pct",
          "graph_has_rationale_pct", "graph_has_end_state_pct",
          "common_seed_ids_all_equal", "context_budget_all_respected", "excluded_repo_leakage_count")}, indent=2))


if __name__ == "__main__":
    main()
