"""Inspect reachable graph edges on retrieval-development tasks only."""

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
    tasks = [t for line in (source / "source_tasks.jsonl").read_text(encoding="utf-8").splitlines()
             if (t := json.loads(line))["instance_id"] in dev_ids]
    settings = memory_settings(load_frozen_config())
    db = ROOT / "memory" / "trajdebug_flat_rag_86" / "data" / "flat_rag.sqlite"
    category_tasks = Counter()
    category_candidates = Counter()
    category_available = Counter()
    category_seeded = Counter()
    examples = []
    for task in tasks:
        with SameInformationMemory(db, excluded_repositories=[task["repo"]]) as memory:
            seed = memory._select_common_seeds(task["problem_statement"], seed_k=settings["graph_seed_k"],
                max_chars=settings["character_budget"], seed_fraction=0.4, per_seed_chars=2000)
            candidates = memory._traversal_candidates(seed.rows, hops=2, allowed=set(settings["allowed_relations"]))
            seen_cats = set()
            detail = []
            for edge, from_node, direction, other in candidates:
                category = RELATION_CATEGORIES.get(edge["relation"], "OTHER")
                category_candidates[category] += 1
                doc = memory._node_doc(other)
                if doc is None:
                    continue
                if doc["doc_id"] in seed.document_ids:
                    category_seeded[category] += 1
                else:
                    category_available[category] += 1
                    seen_cats.add(category)
                    if category != "A":
                        detail.append({"relation": edge["relation"], "from": from_node, "other": other})
            category_tasks.update(seen_cats)
            if detail:
                examples.append({"instance_id": task["instance_id"], "non_a_edges": detail[:12]})
    print(json.dumps({"tasks_with_available_category": dict(category_tasks),
        "candidate_edges": dict(category_candidates), "available_neighbor_edges": dict(category_available),
        "edges_to_seeded_documents": dict(category_seeded), "examples": examples[:8]}, indent=2))


if __name__ == "__main__":
    main()
