"""Inspect graph-only exposure changes on the frozen 30 retrieval development tasks."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .memory_conditions import SameInformationMemory
from .retrieval_exposure_audit import RELATION_CATEGORIES, memory_settings, load_frozen_config

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    source = ROOT / "audit" / "retrieval_exposure"
    dev_ids = set(json.loads((source / "dev_task_ids.json").read_text(encoding="utf-8")))
    tasks = [json.loads(line) for line in (source / "source_tasks.jsonl").read_text(encoding="utf-8").splitlines()
             if json.loads(line)["instance_id"] in dev_ids]
    if len(tasks) != 30:
        raise ValueError("Expected exactly 30 retrieval development tasks")
    settings = memory_settings(load_frozen_config())
    db = ROOT / "memory" / "trajdebug_flat_rag_86" / "data" / "flat_rag.sqlite"
    results = {}
    for hops in (1, 2, 3):
        rows = []
        for task in tasks:
            with SameInformationMemory(db, excluded_repositories=[task["repo"]]) as memory:
                result = memory.graph(task["problem_statement"], seed_k=settings["graph_seed_k"],
                    max_chars=settings["character_budget"], hops=hops,
                    max_neighbors=settings["graph_max_neighbors"],
                    allowed_relations=settings["allowed_relations"])
            rels = result.metadata["relations_followed"]
            categories = sorted({RELATION_CATEGORIES.get(rel, "OTHER") for rel in rels})
            rows.append({"instance_id": task["instance_id"], "relations": rels, "categories": categories,
                         "neighbors": result.metadata["graph_neighbors_added"],
                         "context_chars": len(result.context)})
        category_tasks = Counter(category for row in rows for category in row["categories"])
        results[str(hops)] = {"category_task_counts": dict(sorted(category_tasks.items())),
                              "any_neighbor_tasks": sum(row["neighbors"] > 0 for row in rows),
                              "rows": rows}
        print(f"hops={hops}: {dict(category_tasks)}")
    out = ROOT / "audit" / "gpt_oss_20b" / "graph_dev_hop_comparison.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
