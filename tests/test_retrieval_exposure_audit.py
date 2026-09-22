"""Tests for the retrieval-exposure audit (retrieval-only; no model calls)."""

from __future__ import annotations

import ast
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from src import retrieval_exposure_audit as audit
from src.memory_conditions import SameInformationMemory

SCHEMA = """
CREATE TABLE cases(case_id TEXT PRIMARY KEY,repository TEXT NOT NULL,issue_group TEXT NOT NULL,metadata_json TEXT NOT NULL);
CREATE TABLE documents(
    doc_id TEXT PRIMARY KEY, origin TEXT NOT NULL, kind TEXT NOT NULL, source_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL, payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
    text TEXT NOT NULL, text_sha256 TEXT NOT NULL, guard TEXT NOT NULL);
CREATE TABLE document_cases(doc_id TEXT NOT NULL, case_id TEXT NOT NULL, PRIMARY KEY(doc_id, case_id));
CREATE TABLE chunks(
    rowid INTEGER PRIMARY KEY, chunk_id TEXT UNIQUE NOT NULL, doc_id TEXT NOT NULL, part INTEGER NOT NULL,
    start_char INTEGER NOT NULL, end_char INTEGER NOT NULL, text TEXT NOT NULL, sha256 TEXT NOT NULL);
CREATE INDEX chunks_doc ON chunks(doc_id, part);
CREATE VIRTUAL TABLE chunk_fts USING fts5(text, content='chunks', content_rowid='rowid', tokenize='unicode61');
"""

MODULE_PATH = Path(audit.__file__)


def _add_doc(con, doc_id, origin, kind, text, payload, guard=""):
    con.execute(
        "INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?)",
        (doc_id, origin, kind, doc_id, 0, json.dumps(payload), "0" * 64, text, "0" * 64, guard),
    )
    con.execute(
        "INSERT INTO chunks(chunk_id, doc_id, part, start_char, end_char, text, sha256) VALUES(?,?,?,?,?,?,?)",
        (f"{doc_id}#0", doc_id, 0, 0, len(text), text, "0" * 64),
    )


def _add_node(con, node_id, text, *, origin="base", payload=None):
    body = {"id": node_id}
    if payload:
        body.update(payload)
    _add_doc(con, f"{origin}|node|{node_id}", origin, "node", text, body)


def _add_edge(con, edge_id, source, target, relation, *, origin="semantic"):
    _add_doc(
        con, f"{origin}|edge|{edge_id}", origin, "edge",
        f"{source} {relation} {target}",
        {"id": edge_id, "source": source, "target": target, "relation": relation},
    )


def _add_case(con, case_id, repository):
    con.execute("INSERT INTO cases VALUES(?,?,?,?)", (case_id, repository, "i", "{}"))


def _map(con, doc_id, case_id):
    con.execute("INSERT INTO document_cases VALUES(?,?)", (doc_id, case_id))


@pytest.fixture
def make_db(tmp_path):
    created = []

    def build(builder):
        path = tmp_path / f"mem_{len(created)}.sqlite"
        con = sqlite3.connect(path)
        con.executescript(SCHEMA)
        builder(con)
        con.execute("INSERT INTO chunk_fts(rowid, text) SELECT rowid, text FROM chunks")
        con.commit()
        con.close()
        created.append(path)
        return path

    return build


def _settings(**overrides):
    base = {
        "character_budget": audit.DEFAULT_CHARACTER_BUDGET,
        "flat_top_k": audit.DEFAULT_FLAT_TOP_K,
        "graph_seed_k": audit.DEFAULT_GRAPH_SEED_K,
        "graph_hops": audit.DEFAULT_GRAPH_HOPS,
        "graph_max_neighbors": audit.DEFAULT_GRAPH_MAX_NEIGHBORS,
        "allowed_relations": ["ASSESSES_AGAINST", "CITES_SOURCE_MESSAGE"],
    }
    base.update(overrides)
    return base


def _task(instance_id="t1", repo="org/repo", query="alpha beta gamma", language="python"):
    return {
        "instance_id": instance_id,
        "repo": repo,
        "problem_statement": query,
        "language": language,
        "created_at": 1700000000000,
    }


def _sample_rows(n_activated=16, n_zero=10):
    rows = []
    for i in range(n_activated):
        rows.append(
            {
                "instance_id": f"act{i:02d}",
                "graph_traversal_activated": True,
                "graph_unique_document_count": n_activated - i,
                "graph_neighbors_added": n_activated - i,
                "graph_neighbor_chars_used": (n_activated - i) * 100,
            }
        )
    for i in range(n_zero):
        rows.append(
            {
                "instance_id": f"zero{i:02d}",
                "graph_traversal_activated": False,
                "graph_unique_document_count": 0,
                "graph_neighbors_added": 0,
                "graph_neighbor_chars_used": 0,
            }
        )
    return rows


# 1. deterministic task selection
def test_select_dev_tasks_deterministic():
    tasks = [_task(instance_id=f"t{i:03d}") for i in range(40)]
    a = [t["instance_id"] for t in audit.select_dev_tasks(tasks, n=30, seed=42)]
    b = [t["instance_id"] for t in audit.select_dev_tasks(tasks, n=30, seed=42)]
    assert a == b
    assert len(a) == 30 and len(set(a)) == 30
    # input order must not matter
    reordered = list(reversed(tasks))
    c = [t["instance_id"] for t in audit.select_dev_tasks(reordered, n=30, seed=42)]
    assert a == c
    # a different seed changes the pick (extremely unlikely to collide)
    assert a != [t["instance_id"] for t in audit.select_dev_tasks(tasks, n=30, seed=7)]


def test_select_dev_tasks_fewer_than_requested():
    tasks = [_task(instance_id=f"t{i}") for i in range(4)]
    assert len(audit.select_dev_tasks(tasks, n=30, seed=42)) == 4


# 2. memory-repository exclusion
def test_partition_eligible_excludes_memory_repos():
    tasks = [
        _task(instance_id="keep1", repo="acme/one"),
        _task(instance_id="drop1", repo="ansible/ansible"),
        _task(instance_id="keep2", repo="acme/two"),
        _task(instance_id="drop2", repo="qutebrowser/qutebrowser"),
    ]
    part = audit.partition_eligible(tasks)
    assert part["original_count"] == 4
    assert part["excluded_count"] == 2
    assert part["eligible_count"] == 2
    assert {t["instance_id"] for t in part["eligible"]} == {"keep1", "keep2"}
    assert set(part["excluded_repositories"]) == {"ansible/ansible", "qutebrowser/qutebrowser"}


# 3. no Azure/model calls
def test_module_has_no_forbidden_imports():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported_roots = set()
    used_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])
            used_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Name):
            used_names.add(node.id)
        elif isinstance(node, ast.Attribute):
            used_names.add(node.attr)
    for forbidden in audit.FORBIDDEN_MODULES:
        assert forbidden not in imported_roots, f"forbidden import: {forbidden}"
    for forbidden in audit.FORBIDDEN_NAMES:
        assert forbidden not in used_names, f"forbidden name: {forbidden}"


def test_importing_module_does_not_load_forbidden_modules():
    for forbidden in audit.FORBIDDEN_MODULES:
        assert forbidden not in sys.modules
    assert audit.MODEL_CALLS_MADE == 0


# 4. same common seeds between flat and graph
def test_common_seeds_identical(make_db):
    def build(con):
        _add_node(con, "A", "alpha beta gamma shared seed text")
        _add_node(con, "B", "alpha beta gamma another seed")
        _add_edge(con, "E1", "A", "B", "ASSESSES_AGAINST")

    db = make_db(build)
    with SameInformationMemory(db) as memory:
        result = audit.run_retrieval_only(memory, _task(query="alpha beta gamma"), _settings())
    assert result.flat.metadata["common_seed_chunk_ids"] == result.graph.metadata["common_seed_chunk_ids"]
    assert result.flat.metadata["common_seed_document_ids"] == result.graph.metadata["common_seed_document_ids"]
    row = audit.build_exposure_row(result, db_path=db, settings=_settings())
    assert row["common_seed_ids_equal"] is True
    assert row["common_seed_prefix_equal"] is True


# 5. 24000-char cap
def test_character_budget_respected(make_db):
    def build(con):
        for i in range(40):
            _add_node(con, f"N{i}", ("widget " * 400) + f" variant{i}")

    db = make_db(build)
    with SameInformationMemory(db) as memory:
        result = audit.run_retrieval_only(memory, _task(query="widget"), _settings())
    assert len(result.flat.context) <= 24000
    assert len(result.graph.context) <= 24000
    row = audit.build_exposure_row(result, db_path=db, settings=_settings())
    assert row["context_budget_respected"] is True


# 6. leakage blocking
def test_repository_leakage_blocking(make_db):
    def build(con):
        _add_node(con, "A", "alpha beta secret leak")
        _add_node(con, "B", "alpha beta neighbor")
        _add_case(con, "C1", "evil/repo")
        _map(con, "base|node|A", "C1")
        _map(con, "base|node|B", "C1")

    db = make_db(build)
    task = _task(repo="evil/repo", query="alpha beta")
    # Without exclusion the documents are selected and leakage is detectable.
    with SameInformationMemory(db) as memory:
        exposed = audit.run_retrieval_only(memory, task, _settings())
    exposed_docs = [s.get("document_id") for s in exposed.flat.selected]
    assert exposed_docs
    assert audit.repository_leakage(db, exposed_docs, "evil/repo") is True
    # With the task's repository excluded, nothing from it is exposed.
    with SameInformationMemory(db, excluded_repositories=["evil/repo"]) as memory:
        blocked = audit.run_retrieval_only(memory, task, _settings())
    blocked_docs = [s.get("document_id") for s in blocked.flat.selected] + [
        s.get("document_id") for s in blocked.graph.selected
    ]
    assert audit.repository_leakage(db, blocked_docs, "evil/repo") is False


# 7. relation-category aggregation
def test_relation_category_aggregation():
    assert audit.relation_category("CITES_SOURCE_MESSAGE") == "A"
    assert audit.relation_category("ERROR_PRECEDES_REPAIR_ATTEMPT") == "B"
    assert audit.relation_category("CONTRADICTS_COMPLETION_CLAIM") == "C"
    assert audit.relation_category("OBSERVED_EDIT_EFFECT") == "D"
    assert audit.relation_category("STATED_MOTIVATES") == "E"
    assert audit.relation_category("QUALIFIED_BY") == "F"
    assert audit.relation_category("NOT_A_REAL_RELATION") == "OTHER"
    totals = audit.aggregate_relation_categories(
        {"CITES_SOURCE_MESSAGE": 3, "ASSESSES_AGAINST": 2, "REPAIR_CONFIRMED_BY": 1}
    )
    assert totals == {"A": 5, "B": 1}
    assert audit.categories_present({"CITES_SOURCE_MESSAGE": 1}) == {"A"}
    # every configured allowed relation is categorized
    assert set(audit.RELATION_CATEGORIES) == {
        "CITES_SOURCE_MESSAGE", "REVIEWS", "ASSESSES_AGAINST", "COMPARES_WITH_PUBLISHED_LABEL",
        "ERROR_PRECEDES_REPAIR_ATTEMPT", "REPAIR_CONFIRMED_BY", "PRECEDES_LOCAL_RECOVERY",
        "LOCAL_TEST_RECOVERY_AFTER_EDIT", "FOLLOWED_BY_ROLLBACK_ATTEMPT", "FEEDBACK_PRECEDES_REPAIR",
        "CONTRADICTS_COMPLETION_CLAIM", "SUCCESS_CLAIM_EXCEEDS_EVIDENCE", "CHECK_REPORTS_MISMATCH",
        "OBSERVED_POLICY_TEST_DISAGREEMENT", "SAME_UNVERIFIED_ASSUMPTION_IN_TEST",
        "INCONSISTENT_PRESENCE_SEMANTICS", "IMPLEMENTATION_YIELDS_OBSERVED_MISMATCH",
        "OBSERVED_EDIT_EFFECT", "PERSISTS_IN", "PERSISTS_IN_RECORDED_DIFF", "IMPLEMENTATION_CONFIRMED_BY",
        "LOCAL_FAILURE_REPORTED_AFTER", "REPORTED_PROBE_RESULT", "OBSERVED_SYNTAX_FAILURE",
        "EDIT_CONFIRMED_BY_READBACK", "FINAL_DIFF_LEAVES_IDENTIFIED_CONSUMER_UNCHANGED",
        "FOLLOWED_BY_RELEVANT_TEST_PASS", "STATED_MOTIVATES", "STATED_DEPENDS_ON",
        "STATED_JUSTIFIES_RETAINED_STATE", "FEEDBACK_INTERPRETED_IN", "HAS_END_STATE_ASSESSMENT",
        "QUALIFIED_BY", "REQUIRES_VALIDATION", "PROPOSES_CANDIDATE_LESSON",
    }


# 8. JSON/JSONL schema
def test_dev_task_record_schema_and_jsonl_roundtrip(tmp_path):
    task = _task()
    record = audit.dev_task_record(task)
    assert set(record) == {"instance_id", "repo", "language", "created_at", "problem_statement_sha256"}
    assert all(bad not in record for bad in ("patch", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS"))
    path = audit.write_jsonl(tmp_path / "x.jsonl", [record])
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert lines == [record]
    payload = audit.write_json(tmp_path / "x.json", {"a": 1})
    assert json.loads(payload.read_text(encoding="utf-8")) == {"a": 1}


def test_build_exposure_row_has_required_keys(make_db):
    def build(con):
        _add_node(con, "A", "alpha beta gamma")
        _add_node(con, "B", "neighbor content only")
        _add_edge(con, "E1", "A", "B", "ASSESSES_AGAINST")

    db = make_db(build)
    with SameInformationMemory(db) as memory:
        result = audit.run_retrieval_only(memory, _task(query="alpha beta"), _settings())
    row = audit.build_exposure_row(result, db_path=db, settings=_settings())
    required = {
        "instance_id", "repo", "language",
        "flat_context_chars", "flat_seed_ids", "flat_selected_document_ids", "flat_extra_chunks_added",
        "flat_total_selected_count",
        "graph_context_chars", "graph_seed_ids", "graph_neighbors_added", "graph_neighbor_ids",
        "graph_relations_followed", "graph_neighbor_chars_used", "graph_raw_messages_inlined",
        "graph_raw_message_ids", "graph_raw_message_chars_used", "graph_backfill_chunks_added",
        "graph_backfill_chars_used", "graph_total_selected_count",
        "common_seed_ids_equal", "common_seed_prefix_equal", "context_budget_respected",
        "excluded_repo_leakage_detected",
        "flat_document_ids_not_in_graph", "graph_document_ids_not_in_flat", "graph_unique_document_count",
        "graph_unique_chars", "graph_used_only_bm25_backfill", "graph_traversal_activated",
    }
    assert required <= set(row)
    assert row["graph_traversal_activated"] is True
    assert row["graph_document_ids_not_in_flat"]


# 9. manual-review sample reproducibility
def test_manual_review_selection_reproducible():
    rows = _sample_rows()
    a = audit.select_manual_review(rows)
    b = audit.select_manual_review(rows)
    assert a == b
    assert len(a) == 20 and len(set(a)) == 20
    by_id = {r["instance_id"]: r for r in rows}
    assert sum(1 for i in a if not by_id[i]["graph_traversal_activated"]) == 5
    assert a[:10] == [r["instance_id"] for r in sorted(
        (r for r in rows if r["graph_traversal_activated"]),
        key=lambda r: (-r["graph_unique_document_count"], -r["graph_neighbors_added"],
                       -r["graph_neighbor_chars_used"], r["instance_id"]),
    )[:10]]


# 10. blind A/B assignment reproducibility
def test_blind_assignment_reproducible():
    ids = [f"t{i:02d}" for i in range(20)]
    a = audit.assign_blind_bundles(ids, seed=42)
    b = audit.assign_blind_bundles(ids, seed=42)
    assert a == b
    assert set(a) == set(ids)
    for mapping in a.values():
        assert set(mapping) == {"A", "B"}
        assert {mapping["A"], mapping["B"]} == {"flat", "graph"}
    # order of the input list must not change the assignment
    assert a == audit.assign_blind_bundles(list(reversed(ids)), seed=42)


def test_blinded_lines_hide_labels_and_reproducible():
    tasks = {f"t{i:02d}": _task(instance_id=f"t{i:02d}", query=f"q{i}") for i in range(3)}
    retrievals = {}
    for tid, task in tasks.items():
        class _R:
            pass

        r = _R()
        r.task = task
        r.flat_context = f"FLATCONTEXT-{tid}"
        r.graph_context = f"GRAPHCONTEXT-{tid}"
        retrievals[tid] = r
    ids = sorted(tasks)
    assignment = audit.assign_blind_bundles(ids, seed=42)
    lines_a = audit.build_blinded_lines(retrievals, ids, assignment)
    lines_b = audit.build_blinded_lines(retrievals, ids, audit.assign_blind_bundles(ids, seed=42))
    assert lines_a == lines_b
    blob = json.dumps(lines_a)
    assert "flat_context" not in blob and "graph_context" not in blob
    for line in lines_a:
        mapping = assignment[line["instance_id"]]
        assert line["bundle_A"] == (f"FLATCONTEXT-{line['instance_id']}" if mapping["A"] == "flat" else f"GRAPHCONTEXT-{line['instance_id']}")
        assert all(v is None for v in line["answers"].values())


# frozen config is loaded without model/agent imports
def test_frozen_config_settings():
    settings = audit.memory_settings(audit.load_frozen_config())
    assert settings["character_budget"] == 24000
    assert settings["flat_top_k"] == 8
    assert settings["graph_seed_k"] == 8
    assert settings["graph_hops"] == 1
    assert settings["graph_max_neighbors"] == 8
    assert "CITES_SOURCE_MESSAGE" in settings["allowed_relations"]
