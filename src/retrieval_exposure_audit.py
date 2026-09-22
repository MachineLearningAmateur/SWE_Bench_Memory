"""Retrieval-exposure audit for the frozen Flat RAG / Graph memory systems.

This module measures what historical memory each arm *exposes* for a fresh
benchmark issue description. It is deliberately retrieval-only:

* It never calls Azure/OpenAI or any model.
* It never imports mini-SWE-agent runners / ``get_model``.
* It never solves a task, generates a patch, launches a container, or grades.
* It does not modify the frozen retrieval logic in ``src/memory_conditions.py``.

Primary benchmark source (pinned to the July 2026 SWE-rebench snapshot on
Harbour Hub)::

    ibragim-badertdinov/swe-rebench-07-2026  (revision "1", 111 tasks)

Access is through the Harbour Hub dataset API discovered from the site's own
client bundle::

    GET /api/datasets/{org}/{name}/{ref}/tasks?page=N&pageSize=M
    GET /api/tasks/{org}/{name}/{ref}/files/tests/config.json

Only the whitelisted task-metadata fields are ever retained; gold patches,
test patches, Dockerfiles, and solutions are never read or stored.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

from .memory_conditions import SameInformationMemory

ROOT = Path(__file__).resolve().parents[1]

MODEL_CALLS_MADE = 0

# --- Frozen retrieval configuration (mirrors config/experiment.yaml) ---------
DEFAULT_CHARACTER_BUDGET = 24000
DEFAULT_FLAT_TOP_K = 8
DEFAULT_GRAPH_SEED_K = 8
DEFAULT_GRAPH_HOPS = 1
DEFAULT_GRAPH_MAX_NEIGHBORS = 8

# Historical memory is built from exactly these repositories (see src/experiment.py).
MEMORY_REPOSITORIES = frozenset(
    {
        "ansible/ansible",
        "element-hq/element-web",
        "future-architect/vuls",
        "internetarchive/openlibrary",
        "navidrome/navidrome",
        "protonmail/webclients",
        "qutebrowser/qutebrowser",
    }
)

# --- Benchmark source ---------------------------------------------------------
HARBOR_BASE = "https://hub.harborframework.com"
HARBOR_DATASET_ORG = "ibragim-badertdinov"
HARBOR_DATASET_NAME = "swe-rebench-07-2026"
HARBOR_DATASET_REF = "1"
HARBOR_DATASET_URL = f"{HARBOR_BASE}/datasets/{HARBOR_DATASET_ORG}/{HARBOR_DATASET_NAME}/latest"
DERIVED_FROM = "nebius/SWE-rebench-leaderboard"

# Fields we are allowed to retain from a task config. ``patch`` and ``test_patch``
# are intentionally ABSENT and must never be added.
TASK_METADATA_FIELDS = ("instance_id", "repo", "problem_statement", "language", "created_at")

# Imports that must never appear in this module or anything it imports.
FORBIDDEN_MODULES = (
    "minisweagent",
    "openai",
    "azure",
    "litellm",
    "anthropic",
)
FORBIDDEN_NAMES = ("get_model", "get_agent", "get_sb_environment")

# --- Relation analysis buckets (analysis labels ONLY; traversal untouched) ---
RELATION_CATEGORY_NAMES = {
    "A": "provenance_review_scaffolding",
    "B": "recovery_repair",
    "C": "contradiction_verification_risk",
    "D": "persistence_observed_effects",
    "E": "rationale_motivation",
    "F": "end_state_caveat_lesson",
}
RELATION_CATEGORY_ORDER = ("A", "B", "C", "D", "E", "F", "OTHER")

RELATION_CATEGORIES: dict[str, str] = {}


def _register(category: str, relations: Iterable[str]) -> None:
    for relation in relations:
        if relation in RELATION_CATEGORIES:
            raise ValueError(f"Relation categorized twice: {relation}")
        RELATION_CATEGORIES[relation] = category


_register("A", ("CITES_SOURCE_MESSAGE", "REVIEWS", "ASSESSES_AGAINST", "COMPARES_WITH_PUBLISHED_LABEL"))
_register(
    "B",
    (
        "ERROR_PRECEDES_REPAIR_ATTEMPT",
        "REPAIR_CONFIRMED_BY",
        "PRECEDES_LOCAL_RECOVERY",
        "LOCAL_TEST_RECOVERY_AFTER_EDIT",
        "FOLLOWED_BY_ROLLBACK_ATTEMPT",
        "FEEDBACK_PRECEDES_REPAIR",
    ),
)
_register(
    "C",
    (
        "CONTRADICTS_COMPLETION_CLAIM",
        "SUCCESS_CLAIM_EXCEEDS_EVIDENCE",
        "CHECK_REPORTS_MISMATCH",
        "OBSERVED_POLICY_TEST_DISAGREEMENT",
        "SAME_UNVERIFIED_ASSUMPTION_IN_TEST",
        "INCONSISTENT_PRESENCE_SEMANTICS",
        "IMPLEMENTATION_YIELDS_OBSERVED_MISMATCH",
    ),
)
_register(
    "D",
    (
        "OBSERVED_EDIT_EFFECT",
        "PERSISTS_IN",
        "PERSISTS_IN_RECORDED_DIFF",
        "IMPLEMENTATION_CONFIRMED_BY",
        "LOCAL_FAILURE_REPORTED_AFTER",
        "REPORTED_PROBE_RESULT",
        "OBSERVED_SYNTAX_FAILURE",
        "EDIT_CONFIRMED_BY_READBACK",
        "FINAL_DIFF_LEAVES_IDENTIFIED_CONSUMER_UNCHANGED",
        "FOLLOWED_BY_RELEVANT_TEST_PASS",
    ),
)
_register("E", ("STATED_MOTIVATES", "STATED_DEPENDS_ON", "STATED_JUSTIFIES_RETAINED_STATE", "FEEDBACK_INTERPRETED_IN"))
_register("F", ("HAS_END_STATE_ASSESSMENT", "QUALIFIED_BY", "REQUIRES_VALIDATION", "PROPOSES_CANDIDATE_LESSON"))


def relation_category(relation: str) -> str:
    """Map a relation name to its broad analysis bucket (or ``OTHER``)."""
    return RELATION_CATEGORIES.get(relation, "OTHER")


def aggregate_relation_categories(relations: dict[str, int]) -> dict[str, int]:
    """Aggregate a ``{relation: count}`` mapping into ``{category: count}``."""
    totals: dict[str, int] = {}
    for relation, count in relations.items():
        category = relation_category(relation)
        totals[category] = totals.get(category, 0) + int(count)
    return totals


def categories_present(relations: dict[str, int]) -> set[str]:
    return {relation_category(r) for r in relations}


# --- Frozen config loading ----------------------------------------------------
def load_frozen_config(path: str | Path = ROOT / "config" / "experiment.yaml") -> dict:
    """Load the frozen experiment config without importing model/agent code."""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def memory_settings(cfg: dict) -> dict:
    mc = cfg["memory"]
    return {
        "character_budget": int(mc["character_budget"]),
        "flat_top_k": int(mc["flat_top_k"]),
        "graph_seed_k": int(mc["graph_seed_k"]),
        "graph_hops": int(mc["graph_hops"]),
        "graph_max_neighbors": int(mc["graph_max_neighbors"]),
        "allowed_relations": list(mc.get("allowed_relations", [])),
    }


# --- Benchmark fetching -------------------------------------------------------
def _http_json(url: str, timeout: float = 40.0) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "swe-memory-retrieval-exposure-audit/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_task_list(*, ref: str = HARBOR_DATASET_REF, page_size: int = 100) -> list[dict]:
    """Return every task entry for the pinned dataset revision."""
    tasks: list[dict] = []
    page = 1
    while True:
        url = (
            f"{HARBOR_BASE}/api/datasets/{HARBOR_DATASET_ORG}/{HARBOR_DATASET_NAME}/{ref}"
            f"/tasks?page={page}&pageSize={page_size}"
        )
        payload = _http_json(url)
        items = payload.get("items", [])
        tasks.extend(items)
        total = int(payload.get("total", len(tasks)))
        if not items or len(tasks) >= total:
            break
        page += 1
    return tasks


def fetch_task_metadata(task_name: str, *, org: str = HARBOR_DATASET_ORG, ref: str = HARBOR_DATASET_REF) -> dict:
    """Fetch a task's config and retain ONLY whitelisted metadata fields.

    Gold patch, test patch, Dockerfile, solution, and verifier fields are parsed
    but explicitly dropped; they are never returned or persisted.
    """
    url = f"{HARBOR_BASE}/api/tasks/{urllib.parse.quote(org)}/{urllib.parse.quote(task_name)}/{ref}/files/tests/config.json"
    config = _http_json(url)
    if not isinstance(config, dict):
        raise ValueError(f"Unexpected task config for {task_name!r}: {type(config).__name__}")
    metadata = {
        "instance_id": config.get("instance_id") or task_name,
        "repo": config.get("repo"),
        "problem_statement": config.get("problem_statement"),
        "language": config.get("language"),
        "created_at": config.get("created_at"),
        "source_task_name": task_name,
    }
    if not metadata["problem_statement"]:
        raise ValueError(f"Task {task_name!r} has no problem_statement")
    return metadata


def _canonical_task_list_sha256(tasks: list[dict]) -> str:
    canonical = "\n".join(
        f"{t.get('instance_id')}|{t.get('repo')}"
        for t in sorted(tasks, key=lambda x: x.get("instance_id") or "")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def fetch_benchmark(
    *,
    out_dir: Path,
    refresh: bool = False,
    fetch_all_metadata: bool = True,
) -> tuple[list[dict], dict]:
    """Load (or fetch+cache) the pinned benchmark task metadata.

    Cache is a JSONL of whitelisted metadata only, so no patches are written.
    Returns ``(tasks, source_manifest)``.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache = out_dir / "source_tasks.jsonl"
    manifest_path = out_dir / "benchmark_source.json"

    if cache.exists() and not refresh:
        tasks = [json.loads(line) for line in cache.read_text(encoding="utf-8").splitlines() if line.strip()]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        manifest.setdefault("cached", True)
        return tasks, manifest

    listing = fetch_task_list()
    available = [t for t in listing if t.get("available", True)]
    if fetch_all_metadata:
        tasks = []
        for entry in available:
            tasks.append(fetch_task_metadata(entry["task_name"], org=entry.get("org_name", HARBOR_DATASET_ORG)))
    else:
        tasks = [
            {"instance_id": e["task_name"], "repo": None, "problem_statement": None, "language": None,
             "created_at": None, "source_task_name": e["task_name"]}
            for e in available
        ]
    tasks = sorted(tasks, key=lambda t: t["instance_id"])

    with cache.open("w", encoding="utf-8", newline="\n") as fh:
        for task in tasks:
            fh.write(json.dumps(task, ensure_ascii=False) + "\n")

    languages: dict[str, int] = {}
    for task in tasks:
        key = task.get("language") or "unknown"
        languages[key] = languages.get(key, 0) + 1
    manifest = {
        "dataset": f"{HARBOR_DATASET_ORG}/{HARBOR_DATASET_NAME}",
        "dataset_url": HARBOR_DATASET_URL,
        "dataset_ref": HARBOR_DATASET_REF,
        "derived_from": DERIVED_FROM,
        "api_base": HARBOR_BASE,
        "tasks_endpoint": (
            f"/api/datasets/{HARBOR_DATASET_ORG}/{HARBOR_DATASET_NAME}/{HARBOR_DATASET_REF}/tasks"
        ),
        "files_endpoint": f"/api/tasks/{{org}}/{{name}}/{HARBOR_DATASET_REF}/files/{{path}}",
        "listing_task_count": len(listing),
        "available_task_count": len(available),
        "metadata_task_count": len(tasks),
        "repository_count": len({t["repo"] for t in tasks if t.get("repo")}),
        "languages": dict(sorted(languages.items())),
        "task_metadata_sha256": _canonical_task_list_sha256(tasks),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "retained_fields": list(TASK_METADATA_FIELDS),
        "excluded_fields": ["patch", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS", "install_config", "solution"],
        "cached": False,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return tasks, manifest


# --- Eligibility / deterministic selection -----------------------------------
def partition_eligible(tasks: list[dict], memory_repos: Iterable[str] = MEMORY_REPOSITORIES) -> dict:
    """Split tasks into eligible vs excluded-by-memory-repository.

    Duplicate ``instance_id`` rows are collapsed (first wins after a stable sort).
    """
    memory_repos = set(memory_repos)
    by_id: dict[str, dict] = {}
    for task in tasks:
        tid = task.get("instance_id")
        if not tid or tid in by_id:
            continue
        by_id[tid] = task
    ordered = [by_id[tid] for tid in sorted(by_id)]
    excluded = [t for t in ordered if (t.get("repo") or "") in memory_repos]
    eligible = [t for t in ordered if (t.get("repo") or "") not in memory_repos]
    return {
        "original_count": len(ordered),
        "excluded_count": len(excluded),
        "eligible_count": len(eligible),
        "eligible": eligible,
        "excluded": excluded,
        "excluded_repositories": sorted({t.get("repo") for t in excluded}),
    }


def dev_task_record(task: dict) -> dict:
    """The persisted per-task metadata schema (no gold/test patches)."""
    return {
        "instance_id": task["instance_id"],
        "repo": task.get("repo"),
        "language": task.get("language"),
        "created_at": task.get("created_at"),
        "problem_statement_sha256": hashlib.sha256(task["problem_statement"].encode("utf-8")).hexdigest(),
    }


def select_dev_tasks(tasks: list[dict], *, n: int = 30, seed: int = 42) -> list[dict]:
    """Deterministically pick up to ``n`` tasks: sort by instance_id, then seeded shuffle."""
    ordered = sorted(tasks, key=lambda t: t["instance_id"])
    rng = random.Random(seed)
    shuffled = list(ordered)
    rng.shuffle(shuffled)
    return shuffled[:n]


# --- Retrieval-only audit -----------------------------------------------------
@dataclass
class TaskRetrieval:
    task: dict
    flat_context: str
    graph_context: str
    flat: Any
    graph: Any


def run_retrieval_only(memory: SameInformationMemory, task: dict, settings: dict) -> TaskRetrieval:
    """Run the frozen flat and graph arms for one task. No model is involved."""
    query = task["problem_statement"]
    flat = memory.flat(query, top_k=settings["flat_top_k"], max_chars=settings["character_budget"])
    graph = memory.graph(
        query,
        seed_k=settings["graph_seed_k"],
        max_chars=settings["character_budget"],
        hops=settings["graph_hops"],
        max_neighbors=settings["graph_max_neighbors"],
        allowed_relations=settings["allowed_relations"],
    )
    return TaskRetrieval(task=task, flat_context=flat.context, graph_context=graph.context, flat=flat, graph=graph)


def _ordered_unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _selected_document_ids(result: Any) -> list[str]:
    return _ordered_unique(x.get("document_id") for x in result.selected)


def _neighbor_unique_chars(records: list[dict], flat_doc_ids: set[str]) -> int:
    total = 0
    for record in records:
        if record.get("document_id") not in flat_doc_ids:
            total += int(record.get("rendered_chars") or 0)
    return total


def repository_leakage(db_path: str | Path, document_ids: Iterable[str], repo: str) -> bool:
    """Return True if any selected document is linked to ``repo`` in the corpus."""
    ids = [d for d in _ordered_unique(document_ids)]
    if not ids or not repo:
        return False
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    try:
        db.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in ids)
        rows = db.execute(
            f"SELECT DISTINCT c.repository AS repository FROM document_cases dc "
            f"JOIN cases c ON c.case_id=dc.case_id WHERE dc.doc_id IN ({placeholders})",
            ids,
        ).fetchall()
        return any(r["repository"] == repo for r in rows)
    finally:
        db.close()


def build_exposure_row(retrieval: TaskRetrieval, *, db_path: str | Path, settings: dict) -> dict:
    """Assemble the structured exposure metrics for one task."""
    task = retrieval.task
    flat, graph = retrieval.flat, retrieval.graph
    fm, gm = flat.metadata, graph.metadata

    flat_docs = _selected_document_ids(flat)
    graph_docs = _selected_document_ids(graph)
    flat_set, graph_set = set(flat_docs), set(graph_docs)
    flat_docs_not_graph = [d for d in flat_docs if d not in graph_set]
    graph_docs_not_flat = [d for d in graph_docs if d not in flat_set]

    neighbor_records = gm.get("graph_neighbors", [])
    raw_message_doc_ids = _ordered_unique(
        f"base|node|{nid}" for nid in gm.get("graph_raw_message_ids", [])
    )
    graph_unique_chars = _neighbor_unique_chars(neighbor_records, flat_set)
    graph_unique_chars += sum(
        int(gm.get("graph_raw_message_chars_used") or 0) for d in raw_message_doc_ids if d not in flat_set
    )

    flat_seed_ids = list(fm.get("common_seed_chunk_ids", []))
    graph_seed_ids = list(gm.get("common_seed_chunk_ids", []))
    prefix_len = min(len(flat_seed_ids), len(graph_seed_ids))
    relations_followed = dict(gm.get("relations_followed", {}))
    category_counts = aggregate_relation_categories(relations_followed)
    cats = set(category_counts)

    neighbors_added = int(gm.get("graph_neighbors_added", 0))
    raw_inlined = int(gm.get("graph_raw_messages_inlined", 0))
    backfill_added = int(gm.get("graph_backfill_chunks_added", 0))
    traversal_activated = neighbors_added > 0

    selected = flat_docs + graph_docs
    leakage = repository_leakage(db_path, selected, task.get("repo"))

    return {
        "instance_id": task["instance_id"],
        "repo": task.get("repo"),
        "language": task.get("language"),
        "problem_statement_sha256": hashlib.sha256(task["problem_statement"].encode("utf-8")).hexdigest(),
        # FLAT
        "flat_context_chars": len(flat.context),
        "flat_seed_ids": flat_seed_ids,
        "flat_seed_document_ids": list(fm.get("common_seed_document_ids", [])),
        "flat_selected_document_ids": flat_docs,
        "flat_extra_chunks_added": int(fm.get("flat_extra_chunks_added", 0)),
        "flat_total_selected_count": len(flat.selected),
        # GRAPH
        "graph_context_chars": len(graph.context),
        "graph_seed_ids": graph_seed_ids,
        "graph_seed_document_ids": list(gm.get("common_seed_document_ids", [])),
        "graph_neighbors_added": neighbors_added,
        "graph_neighbor_ids": [r.get("neighbor_node_id") for r in neighbor_records],
        "graph_neighbor_document_ids": [r.get("document_id") for r in neighbor_records],
        "graph_relations_followed": relations_followed,
        "graph_relation_category_counts": {k: category_counts[k] for k in RELATION_CATEGORY_ORDER if k in category_counts},
        "graph_neighbor_chars_used": int(gm.get("graph_neighbor_chars_used", 0)),
        "graph_raw_messages_inlined": raw_inlined,
        "graph_raw_message_ids": list(gm.get("graph_raw_message_ids", [])),
        "graph_raw_message_chars_used": int(gm.get("graph_raw_message_chars_used", 0)),
        "graph_backfill_chunks_added": backfill_added,
        "graph_backfill_chars_used": int(gm.get("graph_backfill_chars_used", 0)),
        "graph_total_selected_count": len(graph.selected),
        # FAIRNESS
        "common_seed_ids_equal": flat_seed_ids == graph_seed_ids,
        "common_seed_prefix_equal": flat_seed_ids[:prefix_len] == graph_seed_ids[:prefix_len],
        "context_budget_respected": len(flat.context) <= settings["character_budget"]
        and len(graph.context) <= settings["character_budget"],
        "excluded_repo_leakage_detected": bool(leakage),
        # DIFFERENCE
        "flat_document_ids_not_in_graph": flat_docs_not_graph,
        "graph_document_ids_not_in_flat": graph_docs_not_flat,
        "graph_unique_document_count": len(graph_docs_not_flat),
        "graph_unique_chars": graph_unique_chars,
        "graph_used_only_bm25_backfill": (not traversal_activated) and raw_inlined == 0 and backfill_added > 0,
        "graph_traversal_activated": traversal_activated,
        # ANALYSIS
        "graph_found_any_neighbor": neighbors_added >= 1,
        "graph_only_provenance_scaffolding": bool(cats) and cats <= {"A"},
        "graph_has_recovery_repair": "B" in cats,
        "graph_has_contradiction": "C" in cats,
        "graph_has_rationale": "E" in cats,
        "graph_has_end_state": "F" in cats,
        "graph_inlined_raw_source": raw_inlined > 0,
    }


# --- Aggregation --------------------------------------------------------------
def _pct(numerator: int, denominator: int) -> float:
    return round(100.0 * numerator / denominator, 2) if denominator else 0.0


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 2) if values else 0.0


def summarize(rows: list[dict]) -> dict:
    """Aggregate exposure rows into the audit summary."""
    n = len(rows)
    activated = [r for r in rows if r["graph_traversal_activated"]]
    with_neighbor = [r for r in rows if r["graph_found_any_neighbor"]]
    relation_freq: dict[str, int] = {}
    category_freq: dict[str, int] = {}
    for row in rows:
        for relation, count in row["graph_relations_followed"].items():
            relation_freq[relation] = relation_freq.get(relation, 0) + int(count)
        for category, count in row["graph_relation_category_counts"].items():
            category_freq[category] = category_freq.get(category, 0) + int(count)

    def strongest_key(row: dict) -> tuple:
        return (-row["graph_unique_document_count"], -row["graph_neighbors_added"],
                -row["graph_neighbor_chars_used"], row["instance_id"])

    strongest = sorted(activated, key=strongest_key)[:10]
    zero = sorted((r for r in rows if not r["graph_traversal_activated"]), key=lambda r: r["instance_id"])

    return {
        "model_calls_made": MODEL_CALLS_MADE,
        "tasks_audited": n,
        "graph_traversal_activated_pct": _pct(len(activated), n),
        "graph_found_any_neighbor_pct": _pct(len(with_neighbor), n),
        "graph_only_provenance_scaffolding_pct": _pct(
            sum(1 for r in with_neighbor if r["graph_only_provenance_scaffolding"]), len(with_neighbor)
        ),
        "graph_has_recovery_repair_pct": _pct(sum(1 for r in rows if r["graph_has_recovery_repair"]), n),
        "graph_has_contradiction_pct": _pct(sum(1 for r in rows if r["graph_has_contradiction"]), n),
        "graph_has_rationale_pct": _pct(sum(1 for r in rows if r["graph_has_rationale"]), n),
        "graph_has_end_state_pct": _pct(sum(1 for r in rows if r["graph_has_end_state"]), n),
        "graph_inlined_raw_source_pct": _pct(sum(1 for r in rows if r["graph_inlined_raw_source"]), n),
        "graph_bm25_only_fallback_pct": _pct(sum(1 for r in rows if r["graph_used_only_bm25_backfill"]), n),
        "avg_graph_neighbor_chars": _mean([r["graph_neighbor_chars_used"] for r in activated]),
        "avg_bm25_backfill_chars": _mean([r["graph_backfill_chars_used"] for r in rows]),
        "avg_flat_context_chars": _mean([r["flat_context_chars"] for r in rows]),
        "avg_graph_context_chars": _mean([r["graph_context_chars"] for r in rows]),
        "avg_graph_neighbors": _mean([r["graph_neighbors_added"] for r in rows]),
        "relation_frequency": dict(sorted(relation_freq.items(), key=lambda kv: (-kv[1], kv[0]))),
        "relation_category_frequency": {k: category_freq[k] for k in RELATION_CATEGORY_ORDER if k in category_freq},
        "strongest_graph_activation_tasks": [r["instance_id"] for r in strongest],
        "zero_graph_activation_tasks": [r["instance_id"] for r in zero],
        "excluded_repo_leakage_count": sum(1 for r in rows if r["excluded_repo_leakage_detected"]),
        "common_seed_ids_all_equal": all(r["common_seed_ids_equal"] for r in rows) if rows else True,
        "context_budget_all_respected": all(r["context_budget_respected"] for r in rows) if rows else True,
    }


# --- Manual review sample -----------------------------------------------------
def select_manual_review(rows: list[dict], *, n_strong: int = 10, n_moderate: int = 5, n_zero: int = 5) -> list[str]:
    """Deterministically choose review tasks: strongest, moderate, zero activation."""
    by_id = {r["instance_id"]: r for r in rows}
    activated = sorted(
        (r for r in rows if r["graph_traversal_activated"]),
        key=lambda r: (-r["graph_unique_document_count"], -r["graph_neighbors_added"],
                       -r["graph_neighbor_chars_used"], r["instance_id"]),
    )
    zero = sorted((r for r in rows if not r["graph_traversal_activated"]), key=lambda r: r["instance_id"])

    selected: list[str] = []

    def take(group: list[dict], count: int) -> None:
        added = 0
        for row in group:
            if added >= count:
                break
            if row["instance_id"] in selected:
                continue
            selected.append(row["instance_id"])
            added += 1

    take(activated, n_strong)
    take(activated[n_strong:], n_moderate)
    take(zero, n_zero)

    # Deterministic backfill so the sample reaches the requested size when possible.
    target = n_strong + n_moderate + n_zero
    if len(selected) < target:
        for row in sorted(rows, key=lambda r: r["instance_id"]):
            if len(selected) >= target:
                break
            if row["instance_id"] not in selected:
                selected.append(row["instance_id"])
    return [tid for tid in selected if tid in by_id]


def assign_blind_bundles(task_ids: list[str], *, seed: int = 42) -> dict[str, dict]:
    """Randomly assign flat/graph to bundles A/B; reproducible for a fixed ID order."""
    rng = random.Random(seed)
    assignment: dict[str, dict] = {}
    for task_id in sorted(task_ids):
        if rng.random() < 0.5:
            assignment[task_id] = {"A": "flat", "B": "graph"}
        else:
            assignment[task_id] = {"A": "graph", "B": "flat"}
    return assignment


REVIEW_QUESTIONS = [
    {
        "id": "q1_applicability",
        "text": "Is the retrieved historical experience applicable to the current task?",
        "options": ["clearly applicable", "possibly applicable", "generic", "irrelevant", "potentially misleading", "uncertain"],
    },
    {"id": "q2_concrete_action", "text": "Does the bundle provide a concrete debugging action?", "options": ["yes", "no", "uncertain"]},
    {"id": "q3_supporting_evidence", "text": "Does it provide evidence supporting that action?", "options": ["yes", "no", "uncertain"]},
    {
        "id": "q4_preserves_relation",
        "text": "Does it preserve a useful repair / contradiction / end-state relation?",
        "options": ["yes", "no", "uncertain"],
    },
    {"id": "q5_better_bundle", "text": "Is one bundle more useful than the other?", "options": ["A", "B", "tie", "uncertain"]},
]


def build_manual_review_sample(
    rows: list[dict],
    retrievals: dict[str, TaskRetrieval],
    selected_ids: list[str],
) -> list[dict]:
    sample = []
    for task_id in selected_ids:
        row = next(r for r in rows if r["instance_id"] == task_id)
        retrieval = retrievals[task_id]
        sample.append(
            {
                "instance_id": task_id,
                "repo": row["repo"],
                "language": row["language"],
                "problem_statement": retrieval.task["problem_statement"],
                "flat_context": retrieval.flat_context,
                "graph_context": retrieval.graph_context,
                "retrieval_metadata": {
                    "flat": _jsonable_metadata(retrieval.flat.metadata),
                    "graph": _jsonable_metadata(retrieval.graph.metadata),
                },
                "exposure_metrics": row,
                "review_questions": REVIEW_QUESTIONS,
            }
        )
    return sample


def _jsonable_metadata(metadata: dict) -> dict:
    """Drop un-serializable/oversized internals while keeping audit metadata."""
    out = {}
    for key, value in metadata.items():
        if key == "graph_neighbors":
            out[key] = value
            continue
        try:
            json.dumps(value)
            out[key] = value
        except (TypeError, ValueError):
            out[key] = str(value)
    return out


def build_blinded_lines(
    retrievals: dict[str, TaskRetrieval],
    selected_ids: list[str],
    assignment: dict[str, dict],
) -> list[dict]:
    lines = []
    for task_id in sorted(selected_ids):
        retrieval = retrievals[task_id]
        bundles = {"flat": retrieval.flat_context, "graph": retrieval.graph_context}
        assigned = assignment[task_id]
        lines.append(
            {
                "instance_id": task_id,
                "problem_statement": retrieval.task["problem_statement"],
                "bundle_A": bundles[assigned["A"]],
                "bundle_B": bundles[assigned["B"]],
                "review_questions": REVIEW_QUESTIONS,
                "answers": {q["id"]: None for q in REVIEW_QUESTIONS},
            }
        )
    return lines


def build_answer_key(assignment: dict[str, dict]) -> dict:
    return {
        "note": "Private answer key. Do NOT share with blinded reviewers. A/B -> flat|graph.",
        "assignments": {task_id: assignment[task_id] for task_id in sorted(assignment)},
    }


# --- Orchestration ------------------------------------------------------------
def audit_tasks(
    tasks: list[dict],
    *,
    db_path: str | Path,
    settings: dict,
    verbose: bool = False,
) -> tuple[list[dict], dict[str, TaskRetrieval]]:
    """Run retrieval-only audit for each task, excluding its own repository."""
    rows: list[dict] = []
    retrievals: dict[str, TaskRetrieval] = {}
    for index, task in enumerate(tasks, start=1):
        excluded = [task["repo"]] if task.get("repo") else []
        with SameInformationMemory(db_path, excluded_repositories=excluded) as memory:
            retrieval = run_retrieval_only(memory, task, settings)
            row = build_exposure_row(retrieval, db_path=db_path, settings=settings)
        retrievals[task["instance_id"]] = retrieval
        rows.append(row)
        if verbose:
            print(
                f"[{index}/{len(tasks)}] {task['instance_id']}: "
                f"flat={row['flat_context_chars']}c graph={row['graph_context_chars']}c "
                f"neighbors={row['graph_neighbors_added']} raw={row['graph_raw_messages_inlined']}"
            )
    return rows, retrievals


def write_jsonl(path: str | Path, records: list[dict]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def write_json(path: str | Path, payload: Any) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def render_markdown(summary: dict, provenance: dict, partition: dict, selected_ids: list[str]) -> str:
    lines = [
        "# Retrieval Exposure Audit",
        "",
        "Retrieval-only diagnostic. **No model calls, no agent runs, no task solving, no grading.**",
        "This audit measures what historical memory each frozen system would *expose* for a new issue;",
        "it cannot establish that graph memory improves task outcomes.",
        "",
        f"MODEL CALLS MADE: {summary['model_calls_made']}",
        "",
        "## Benchmark source",
        "",
        f"- Dataset: `{provenance.get('dataset')}` (ref `{provenance.get('dataset_ref')}`)",
        f"- URL: {provenance.get('dataset_url')}",
        f"- Derived from: `{provenance.get('derived_from')}`",
        f"- Listing task count: {provenance.get('listing_task_count')}",
        f"- Task-metadata SHA256: `{provenance.get('task_metadata_sha256')}`",
        f"- Languages: {provenance.get('languages')}",
        "",
        "## Task partition",
        "",
        f"- Original task count: {partition['original_count']}",
        f"- Excluded (memory repositories): {partition['excluded_count']} {partition['excluded_repositories']}",
        f"- Eligible task count: {partition['eligible_count']}",
        f"- Audited: {summary['tasks_audited']}",
        "",
        "## Selected task IDs",
        "",
        "```",
        *selected_ids,
        "```",
        "",
        "## Exposure statistics",
        "",
        f"- Graph traversal activated: {summary['graph_traversal_activated_pct']}%",
        f"- Graph found >=1 neighbor: {summary['graph_found_any_neighbor_pct']}%",
        f"- Only provenance/scaffolding relations (of activated): {summary['graph_only_provenance_scaffolding_pct']}%",
        f"- At least one recovery/repair relation: {summary['graph_has_recovery_repair_pct']}%",
        f"- Contradiction/verification relation: {summary['graph_has_contradiction_pct']}%",
        f"- Rationale relation: {summary['graph_has_rationale_pct']}%",
        f"- End-state/caveat relation: {summary['graph_has_end_state_pct']}%",
        f"- Inlined raw source evidence: {summary['graph_inlined_raw_source_pct']}%",
        f"- Fell back entirely to BM25 after common seeds: {summary['graph_bm25_only_fallback_pct']}%",
        f"- Avg graph-specific neighbor chars: {summary['avg_graph_neighbor_chars']}",
        f"- Avg BM25 backfill chars: {summary['avg_bm25_backfill_chars']}",
        f"- Avg flat context chars: {summary['avg_flat_context_chars']}",
        f"- Avg graph context chars: {summary['avg_graph_context_chars']}",
        f"- Avg graph neighbors: {summary['avg_graph_neighbors']}",
        f"- Excluded-repo leakage incidents: {summary['excluded_repo_leakage_count']}",
        f"- Common seeds identical (all tasks): {summary['common_seed_ids_all_equal']}",
        f"- Character budget respected (all tasks): {summary['context_budget_all_respected']}",
        "",
        "## Automated observations",
        "",
    ]
    n = summary["tasks_audited"]
    activated = round(summary["graph_traversal_activated_pct"] / 100.0 * n)
    raw = round(summary["graph_inlined_raw_source_pct"] / 100.0 * n)
    fallback = round(summary["graph_bm25_only_fallback_pct"] / 100.0 * n)
    observations = [
        f"Graph traversal activated on {activated}/{n} audited tasks.",
        f"Graph inlined raw cited source evidence on {raw}/{n} audited tasks.",
        f"Graph fell back entirely to BM25 backfill on {fallback}/{n} audited tasks.",
    ]
    categories = summary["relation_category_frequency"]
    if categories:
        observations.append(
            "Relations followed were confined to categories: "
            + ", ".join(f"{k} ({RELATION_CATEGORY_NAMES.get(k, k)})" for k in categories)
            + "."
        )
    else:
        observations.append("No graph relations were followed on any audited task.")
    observations.append(
        "These are exposure observations only; they do not establish that graph memory improves task outcomes."
    )
    lines += [f"- {o}" for o in observations]
    lines += [
        "",
        "## Relation frequency",
        "",
        "| Relation | Count | Category |",
        "| --- | ---: | --- |",
    ]
    for relation, count in summary["relation_frequency"].items():
        lines.append(f"| {relation} | {count} | {relation_category(relation)} |")
    lines += ["", "## Relation category frequency", "", "| Category | Meaning | Count |", "| --- | --- | ---: |"]
    for category, count in summary["relation_category_frequency"].items():
        lines.append(f"| {category} | {RELATION_CATEGORY_NAMES.get(category, category)} | {count} |")
    lines += [
        "",
        "## Strongest graph activation",
        "",
        "```",
        *summary["strongest_graph_activation_tasks"],
        "```",
        "",
        "## Zero graph activation",
        "",
        "```",
        *summary["zero_graph_activation_tasks"],
        "```",
        "",
        "## Interpretation guardrails",
        "",
        "Valid: activation rates, relation-category rates, raw-evidence exposure, BM25-only fallback,",
        "flat/graph differences, and whether humans judge extra context as potentially relevant.",
        "",
        "Invalid (needs the later agent experiment): \"graph helps agents\", \"graph improves accuracy\",",
        "\"graph memory is superior to RAG\".",
        "",
    ]
    return "\n".join(lines) + "\n"


def run_audit(
    *,
    out_dir: str | Path = ROOT / "audit" / "retrieval_exposure",
    db_path: str | Path = ROOT / "memory" / "trajdebug_flat_rag_86" / "data" / "flat_rag.sqlite",
    config_path: str | Path = ROOT / "config" / "experiment.yaml",
    seed: int = 42,
    n_dev: int = 30,
    refresh_source: bool = False,
    verbose: bool = True,
) -> dict:
    """End-to-end retrieval-exposure audit. Returns a result dictionary."""
    if MODEL_CALLS_MADE != 0:
        raise AssertionError("Model calls occurred during a retrieval-only audit")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    _, provenance = fetch_benchmark(out_dir=out_dir, refresh=refresh_source)
    tasks = [json.loads(line) for line in (out_dir / "source_tasks.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]

    partition = partition_eligible(tasks)
    dev_tasks = select_dev_tasks(partition["eligible"], n=n_dev, seed=seed)

    write_json(out_dir / "dev_task_ids.json", [t["instance_id"] for t in dev_tasks])
    write_jsonl(out_dir / "dev_tasks.jsonl", [dev_task_record(t) for t in dev_tasks])

    cfg = load_frozen_config(config_path)
    settings = memory_settings(cfg)
    rows, retrievals = audit_tasks(dev_tasks, db_path=db_path, settings=settings, verbose=verbose)

    if MODEL_CALLS_MADE != 0:
        raise AssertionError("Model calls occurred during a retrieval-only audit")

    write_jsonl(out_dir / "exposure_results.jsonl", rows)
    summary = summarize(rows)
    summary["partition"] = {
        "original_count": partition["original_count"],
        "excluded_count": partition["excluded_count"],
        "eligible_count": partition["eligible_count"],
        "excluded_repositories": partition["excluded_repositories"],
    }
    summary["frozen_settings"] = settings
    write_json(out_dir / "exposure_summary.json", summary)

    # Manual review sample (not blinded) + blinded variant.
    review_ids = select_manual_review(rows)
    review_sample = build_manual_review_sample(rows, retrievals, review_ids)
    write_json(out_dir / "manual_review_sample.json", review_sample)

    assignment = assign_blind_bundles(review_ids, seed=seed)
    blinded = build_blinded_lines(retrievals, review_ids, assignment)
    write_jsonl(out_dir / "manual_review_blinded.jsonl", blinded)
    write_json(out_dir / "manual_review_answer_key.json", build_answer_key(assignment))

    (ROOT / "docs" / "retrieval_exposure_audit.md").write_text(
        render_markdown(summary, provenance, partition, [t["instance_id"] for t in dev_tasks]),
        encoding="utf-8",
    )

    return {
        "provenance": provenance,
        "partition": partition,
        "dev_task_ids": [t["instance_id"] for t in dev_tasks],
        "rows": rows,
        "summary": summary,
        "review_ids": review_ids,
        "out_dir": str(out_dir),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Retrieval-exposure audit (no model calls).")
    parser.add_argument("--refresh-source", action="store_true", help="Re-fetch benchmark task metadata from Harbour Hub.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--n-dev", type=int, default=30)
    parser.add_argument("--db", default=str(ROOT / "memory" / "trajdebug_flat_rag_86" / "data" / "flat_rag.sqlite"))
    parser.add_argument("--out", default=str(ROOT / "audit" / "retrieval_exposure"))
    parser.add_argument("--config", default=str(ROOT / "config" / "experiment.yaml"))
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    result = run_audit(
        out_dir=args.out,
        db_path=args.db,
        config_path=args.config,
        seed=args.seed,
        n_dev=args.n_dev,
        refresh_source=args.refresh_source,
        verbose=not args.quiet,
    )
    summary = result["summary"]
    print("\nMODEL CALLS MADE:", summary["model_calls_made"])
    print("Audited:", summary["tasks_audited"])
    print("Graph activation:", f"{summary['graph_traversal_activated_pct']}%")
    print("Outputs:", result["out_dir"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
