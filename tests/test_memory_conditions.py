"""Regression tests for graph-vs-flat retrieval budgeting.

Covers the handoff requirements: identical common seeds, per-seed caps, truncation
instead of skipping, graph expansion only from visible seeds, deterministic relation
priority, hard ``allowed_relations`` filtering, true edge-direction display, budget
limits, flat never traversing, and repository/issue exclusions.
"""

import json
import sqlite3

import pytest

from src.bootstrap import bootstrap_memory
from src.memory_conditions import (
    DEFAULT_PER_SEED_CHARS,
    DEFAULT_SEED_FRACTION,
    DISPLAY_TRUNCATED_PER_NEIGHBOR,
    DISPLAY_TRUNCATED_PER_SEED,
    MATERIALIZED_RELATION,
    WARNING,
    SameInformationMemory,
)

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


def _add_doc(con, doc_id, origin, kind, text, payload, guard="", ordinal=0):
    con.execute(
        "INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?)",
        (doc_id, origin, kind, doc_id, ordinal, json.dumps(payload), "0" * 64, text, "0" * 64, guard),
    )
    con.execute(
        "INSERT INTO chunks(chunk_id, doc_id, part, start_char, end_char, text, sha256) VALUES(?,?,?,?,?,?,?)",
        (f"{doc_id}#0", doc_id, 0, 0, len(text), text, "0" * 64),
    )


def _add_node(con, node_id, text, *, origin="base", kind="node", guard="", payload=None):
    body = {"id": node_id}
    if payload:
        body.update(payload)
    _add_doc(con, f"{origin}|node|{node_id}", origin, kind, text, body, guard)


def _add_evidence_node(con, node_id, base_node_id, text, *, case_id="C1", origin="semantic", guard=""):
    payload = {
        "id": node_id,
        "case_id": case_id,
        "properties": {"source_ref": {"base_graph_node_id": base_node_id, "case_id": case_id}},
    }
    _add_doc(con, f"{origin}|node|{node_id}", origin, "node", text, payload, guard)


def _add_edge(con, edge_id, source, target, relation, *, origin="semantic"):
    _add_doc(
        con,
        f"{origin}|edge|{edge_id}",
        origin,
        "edge",
        f"{source} {relation} {target}",
        {"id": edge_id, "source": source, "target": target, "relation": relation},
    )


def _add_case(con, case_id, repository="r", issue_group="i"):
    con.execute("INSERT INTO cases VALUES(?,?,?,?)", (case_id, repository, issue_group, "{}"))


def _map_case(con, doc_id, case_id):
    con.execute("INSERT INTO document_cases VALUES(?,?)", (doc_id, case_id))


def _finish(con):
    con.execute("INSERT INTO chunk_fts(rowid, text) SELECT rowid, text FROM chunks")
    con.commit()
    con.close()


@pytest.fixture
def make_db(tmp_path):
    """Return a builder that constructs a synthetic memory SQLite file and closes it."""
    created = []

    def build(builder):
        path = tmp_path / f"mem_{len(created)}.sqlite"
        con = sqlite3.connect(path)
        con.executescript(SCHEMA)
        builder(con)
        _finish(con)
        created.append(path)
        return path

    return build


def _rank_override(memory, ordered_doc_ids):
    """Force a deterministic BM25 order for tests that do not exercise ranking."""

    def fake(query, limit=8):
        rows = []
        for doc_id in ordered_doc_ids:
            rows.append(
                memory.db.execute(
                    "SELECT ch.*, d.origin, d.kind, d.source_id, d.guard, 0.0 AS ranking_score "
                    "FROM chunks ch JOIN documents d ON d.doc_id=ch.doc_id WHERE ch.chunk_id=?",
                    (f"{doc_id}#0",),
                ).fetchone()
            )
        return rows[:limit]

    memory.rank_chunks = fake


def _real_corpus():
    return bootstrap_memory() / "data" / "flat_rag.sqlite"


THREE_QUERIES = [
    "existing tests expect old behavior but issue explicitly requests a new interface",
    "a local bug was introduced but later repaired or rolled back",
    "verification reports success even though the underlying check failed",
]


# 1. Common seeds identical (real corpus), including rendered prefix equality.
def test_common_seeds_identical_on_real_corpus():
    with SameInformationMemory(_real_corpus()) as m:
        for query in THREE_QUERIES:
            flat = m.flat(query, top_k=8, max_chars=24000)
            graph = m.graph(query, seed_k=8, max_chars=24000, hops=1, max_neighbors=8)
            assert flat.metadata["common_seed_chunk_ids"] == graph.metadata["common_seed_chunk_ids"]
            assert flat.metadata["common_seed_document_ids"] == graph.metadata["common_seed_document_ids"]
            n = graph.metadata["seed_chars_used"]
            assert flat.context[:n] == graph.context[:n]


# 2. Oversized top seed is truncated, not skipped; later seeds still get a chance.
def test_oversized_top_seed_truncated_not_skipped(make_db):
    def build(con):
        _add_node(con, "BIG", "widget " * 2000)
        _add_node(con, "SMALL", "widget second seed")

    db = make_db(build)
    with SameInformationMemory(db) as m:
        _rank_override(m, ["base|node|BIG", "base|node|SMALL"])
        flat = m.flat("widget", top_k=2, max_chars=6000, seed_fraction=0.5, per_seed_chars=500)
    assert DISPLAY_TRUNCATED_PER_SEED in flat.context
    seeds = flat.metadata["common_seed_chunk_ids"]
    assert "base|node|BIG#0" in seeds
    assert "base|node|SMALL#0" in seeds
    assert flat.metadata["seed_truncation_count"] >= 1


# 3. No single seed can monopolise the seed budget (per-seed cap enforced).
def test_per_seed_cap_enforced(make_db):
    def build(con):
        _add_node(con, "BIG", "widget " * 2000)
        _add_node(con, "SMALL", "widget second seed")

    db = make_db(build)
    with SameInformationMemory(db) as m:
        _rank_override(m, ["base|node|BIG", "base|node|SMALL"])
        flat = m.flat("widget", top_k=2, max_chars=6000, seed_fraction=0.5, per_seed_chars=500)
    seed_entries = [x for x in flat.selected if x.get("role") == "common_seed"]
    assert seed_entries
    assert all(x["rendered_chars"] <= 500 for x in seed_entries)


# 4. Graph only expands from visible common seeds, never hidden BM25 candidates.
def test_graph_only_expands_from_visible_seeds(make_db):
    def build(con):
        _add_node(con, "A", "widget " * 2000)
        _add_node(con, "B", "widget hidden candidate")
        _add_node(con, "X", "widget downstream neighbor")
        _add_edge(con, "eBX", "B", "X", "HAS_END_STATE_ASSESSMENT")

    db = make_db(build)
    with SameInformationMemory(db) as m:
        _rank_override(m, ["base|node|A", "base|node|B"])
        small = m.graph("widget", seed_k=2, max_chars=3000, hops=1, max_neighbors=8,
                        seed_fraction=0.4, per_seed_chars=1000)
        large = m.graph("widget", seed_k=2, max_chars=6000, hops=1, max_neighbors=8,
                        seed_fraction=0.9, per_seed_chars=1000)
    # B did not render as a seed -> its neighbor X must not appear.
    assert "base|node|B#0" not in small.metadata["common_seed_chunk_ids"]
    assert small.metadata["graph_neighbors_added"] == 0
    assert "X" not in {x.get("node_id") for x in small.selected}
    # Control: when B renders, X is reachable.
    assert "base|node|B#0" in large.metadata["common_seed_chunk_ids"]
    assert "X" in {x.get("node_id") for x in large.selected}


# 5. True stored edge direction is preserved for forward and reverse traversal.
def test_true_edge_direction_preserved(make_db):
    def build(con):
        _add_node(con, "SRC", "widget source assessment")
        _add_node(con, "TGT", "widget target end state")
        _add_edge(con, "e_fwd", "SRC", "TGT", "HAS_END_STATE_ASSESSMENT")

    db = make_db(build)
    with SameInformationMemory(db) as m:
        _rank_override(m, ["base|node|SRC"])
        fwd = m.graph("widget", seed_k=1, max_chars=6000, hops=1, max_neighbors=4)
        _rank_override(m, ["base|node|TGT"])
        rev = m.graph("widget", seed_k=1, max_chars=6000, hops=1, max_neighbors=4)
    assert "GRAPH_RELATION SRC --HAS_END_STATE_ASSESSMENT--> TGT" in fwd.context
    assert "traversed reverse" not in fwd.context
    assert fwd.metadata["graph_neighbors"][0]["direction"] == "forward"
    assert "GRAPH_RELATION (traversed reverse) SRC --HAS_END_STATE_ASSESSMENT--> TGT" in rev.context
    assert rev.metadata["graph_neighbors"][0]["direction"] == "reverse"
    assert rev.metadata["graph_neighbors"][0]["edge_source"] == "SRC"
    assert rev.metadata["graph_neighbors"][0]["edge_target"] == "TGT"


# 6. Relation priority is deterministic: high-value relation wins even when a lower
#    value edge was inserted (lower edge_doc_id) first.
def test_relation_priority_deterministic(make_db):
    def build(con):
        _add_node(con, "ROOT", "widget root assessment")
        _add_node(con, "LOW", "widget review scaffolding")
        _add_node(con, "HIGH", "widget caveat")
        _add_edge(con, "aaa_first", "ROOT", "LOW", "REVIEWS")
        _add_edge(con, "zzz_second", "ROOT", "HIGH", "QUALIFIED_BY")

    db = make_db(build)
    with SameInformationMemory(db) as m:
        _rank_override(m, ["base|node|ROOT"])
        result = m.graph("widget", seed_k=1, max_chars=6000, hops=1, max_neighbors=1,
                         allowed_relations=["REVIEWS", "QUALIFIED_BY"])
    assert result.metadata["graph_neighbors_added"] == 1
    assert result.metadata["graph_neighbors"][0]["relation"] == "QUALIFIED_BY"


# 7. Hard relation filter still excludes everything not listed.
def test_allowed_relations_is_hard_filter(make_db):
    def build(con):
        _add_node(con, "ROOT", "widget root assessment")
        _add_node(con, "LOW", "widget review scaffolding")
        _add_node(con, "HIGH", "widget caveat")
        _add_edge(con, "e_rev", "ROOT", "LOW", "REVIEWS")
        _add_edge(con, "e_qual", "ROOT", "HIGH", "QUALIFIED_BY")

    db = make_db(build)
    with SameInformationMemory(db) as m:
        _rank_override(m, ["base|node|ROOT"])
        result = m.graph("widget", seed_k=1, max_chars=6000, hops=1, max_neighbors=8,
                         allowed_relations=["REVIEWS"])
    relations = {x["relation"] for x in result.metadata["graph_neighbors"]}
    assert relations == {"REVIEWS"}
    assert result.metadata["allowed_relations"] == ["REVIEWS"]


# 8. Character budget is respected by both arms, even with a tiny budget.
@pytest.mark.parametrize("budget", [2048, 24000])
def test_budget_respected(budget):
    with SameInformationMemory(_real_corpus()) as m:
        for query in THREE_QUERIES:
            flat = m.flat(query, top_k=8, max_chars=budget)
            graph = m.graph(query, seed_k=8, max_chars=budget, hops=1, max_neighbors=8)
            assert len(flat.context) <= budget
            assert len(graph.context) <= budget


# 9. Flat never traverses the graph.
def test_flat_never_traverses(make_db):
    def build(con):
        _add_node(con, "ROOT", "widget root")
        _add_node(con, "N", "widget neighbor")
        _add_edge(con, "e", "ROOT", "N", "QUALIFIED_BY")

    db = make_db(build)
    with SameInformationMemory(db) as m:
        _rank_override(m, ["base|node|ROOT"])
        flat = m.flat("widget", top_k=2, max_chars=6000)
    assert flat.metadata["graph_traversal"] is False
    assert all(x["kind"] != "graph_neighbor" for x in flat.selected)
    assert "GRAPH_RELATION" not in flat.context


# 10. Graph still expands when useful edges exist.
def test_graph_expands_when_edges_exist(make_db):
    def build(con):
        _add_node(con, "ROOT", "widget root assessment")
        _add_node(con, "LIMITS", "widget caveat")
        _add_edge(con, "e", "ROOT", "LIMITS", "QUALIFIED_BY")

    db = make_db(build)
    with SameInformationMemory(db) as m:
        _rank_override(m, ["base|node|ROOT"])
        result = m.graph("widget", seed_k=1, max_chars=6000, hops=1, max_neighbors=8,
                         allowed_relations=["QUALIFIED_BY"])
    assert result.metadata["graph_neighbors_added"] >= 1
    assert any(x["kind"] == "graph_neighbor" for x in result.selected)
    assert result.metadata["relations_followed"] == {"QUALIFIED_BY": 1}


# 11. Repository / issue exclusions still work.
def test_exclusions_still_work(make_db):
    def build(con):
        _add_case(con, "C1", repository="excluded_repo", issue_group="i1")
        _add_case(con, "C2", repository="kept_repo", issue_group="excluded_issue")
        _add_case(con, "C3", repository="kept_repo", issue_group="i3")
        _add_node(con, "N1", "widget one")
        _add_node(con, "N2", "widget two")
        _add_node(con, "N3", "widget three")
        _map_case(con, "base|node|N1", "C1")
        _map_case(con, "base|node|N2", "C2")
        _map_case(con, "base|node|N3", "C3")

    db = make_db(build)
    with SameInformationMemory(db, excluded_repositories=["excluded_repo"],
                               excluded_issues=["excluded_issue"]) as m:
        result = m.flat("widget", top_k=8, max_chars=6000)
    docs = set(result.metadata["common_seed_document_ids"])
    assert "base|node|N1" not in docs
    assert "base|node|N2" not in docs
    assert "base|node|N3" in docs


# 12. Existing behaviour: graph seeds are a prefix of the flat BM25 order.
def test_flat_and_graph_share_seed_ranking():
    db = _real_corpus()
    query = "legacy test expects old crash message signal details"
    with SameInformationMemory(db) as m:
        flat = m.flat(query, top_k=4, max_chars=8000)
        graph = m.graph(query, seed_k=4, max_chars=8000, hops=1, max_neighbors=2,
                        allowed_relations=["STATED_MOTIVATES", "CITES_SOURCE_MESSAGE", "QUALIFIED_BY"])
    flat_seed = [x["chunk_id"] for x in flat.selected if x["kind"] == "bm25_chunk"]
    graph_seed = [x["chunk_id"] for x in graph.selected if x["kind"] == "bm25_seed"]
    assert graph.metadata["graph_traversal"] is True
    assert flat.metadata["graph_traversal"] is False
    assert graph_seed == graph.metadata["common_seed_chunk_ids"]
    assert flat.metadata["common_seed_chunk_ids"] == graph_seed
    assert all(x in flat_seed for x in graph_seed)


# --- Materialized provenance pointers (source_ref.base_graph_node_id) -------------

# 13. A semantic node's source_ref becomes a truthful, traversable edge to the exact
#     base message, in both traversal directions.
def test_materialized_pointer_connects_to_exact_base_message(make_db):
    def build(con):
        _add_node(con, "C1:message:5", "widget base message")
        _add_evidence_node(con, "sem:C1:evidence:5", "C1:message:5", "widget evidence node")

    db = make_db(build)
    with SameInformationMemory(db) as m:
        _rank_override(m, ["base|node|C1:message:5"])
        rev = m.graph("widget", seed_k=1, max_chars=6000, hops=1, max_neighbors=4,
                      allowed_relations=[MATERIALIZED_RELATION])
        _rank_override(m, ["semantic|node|sem:C1:evidence:5"])
        fwd = m.graph("widget", seed_k=1, max_chars=6000, hops=1, max_neighbors=4,
                      allowed_relations=[MATERIALIZED_RELATION])
    assert rev.metadata["graph_neighbors_added"] == 1
    n = rev.metadata["graph_neighbors"][0]
    assert n["neighbor_node_id"] == "sem:C1:evidence:5"
    assert n["edge_source"] == "sem:C1:evidence:5"
    assert n["edge_target"] == "C1:message:5"
    assert n["direction"] == "reverse" and n["materialized"] is True
    assert "GRAPH_RELATION (traversed reverse) sem:C1:evidence:5 --CITES_SOURCE_MESSAGE--> C1:message:5" in rev.context
    assert fwd.metadata["graph_neighbors"][0]["neighbor_node_id"] == "C1:message:5"
    assert fwd.metadata["graph_neighbors"][0]["direction"] == "forward"
    assert "GRAPH_RELATION sem:C1:evidence:5 --CITES_SOURCE_MESSAGE--> C1:message:5" in fwd.context


# 14. Materialization encodes only the stored pointer (no invented semantic content).
def test_materialized_edge_invents_no_semantic_content(make_db):
    def build(con):
        _add_node(con, "C1:message:5", "widget base message")
        _add_evidence_node(con, "sem:C1:evidence:5", "C1:message:5", "widget evidence node")

    db = make_db(build)
    with SameInformationMemory(db) as m:
        edges = m._source_ref_edges()
    assert len(edges) == 1
    e = edges[0]
    assert e["relation"] == MATERIALIZED_RELATION
    assert e["text"] == SameInformationMemory._materialized_edge_text(
        e["edge_id"], e["source"], e["target"], e["relation"], e["case_id"]
    )
    assert "RELATION DEFINITION" not in e["text"]
    assert "sem:C1:evidence:5" in e["text"] and "C1:message:5" in e["text"]


# 15. Flat RAG has the identical relation fact available as searchable text.
def test_flat_corpus_exposes_materialized_relation_fact(make_db):
    def build(con):
        _add_node(con, "C1:message:5", "widget base message")
        _add_evidence_node(con, "sem:C1:evidence:5", "C1:message:5", "widget evidence node")
        _add_node(con, "C2:message:9", "unrelated corpus text")

    db = make_db(build)
    query = "CITES SOURCE MESSAGE sem evidence"
    with SameInformationMemory(db) as m:
        rows = m.rank_chunks(query, 500)
        derived = [r for r in rows if r["chunk_id"].startswith("materialized|edge|")]
        flat = m.flat(query, top_k=5, max_chars=6000)
    assert derived, "materialized relation chunk must be searchable by the flat ranking"
    assert any("sem:C1:evidence:5" in r["text"] and "C1:message:5" in r["text"] for r in derived)
    assert "RELATION STATEMENT" in flat.context


# 16. A base-message seed that is referenced by the semantic overlay (Q1-like) can
#     traverse into the semantic evidence node (real corpus).
def test_referenced_base_message_seed_traverses_into_evidence():
    with SameInformationMemory(_real_corpus()) as m:
        edge = m._source_ref_edges()[0]
        _rank_override(m, [f"base|node|{edge['target']}"])
        result = m.graph("widget", seed_k=1, max_chars=8000, hops=1, max_neighbors=8,
                         allowed_relations=[MATERIALIZED_RELATION])
    reached = [n for n in result.metadata["graph_neighbors"]
               if n["neighbor_node_id"] == edge["source"]]
    assert reached, f"seed {edge['target']} should reach {edge['source']}"
    assert reached[0]["direction"] == "reverse" and reached[0]["materialized"] is True


# 17. Graph backfills with BM25 when traversal underuses the budget.
def test_graph_backfills_bm25_when_expansion_underuses_budget(make_db):
    def build(con):
        for i in range(6):
            _add_node(con, f"N{i}", "widget " * 2000)

    db = make_db(build)
    order = [f"base|node|N{i}" for i in range(6)]
    with SameInformationMemory(db) as m:
        _rank_override(m, order)
        result = m.graph("widget", seed_k=2, max_chars=8000, hops=1, max_neighbors=8,
                         seed_fraction=0.4, per_seed_chars=1000)
    assert result.metadata["graph_neighbors_added"] == 0
    assert result.metadata["graph_backfill_chunks_added"] >= 1
    assert result.metadata["graph_backfill_chars_used"] > 0
    assert len(result.context) <= 8000
    assert len(result.context) == 8000
    assert any(x["role"] == "backfill_bm25" for x in result.selected)


# 18. Common seed prefix stays byte-for-byte identical even with materialized edges.
def test_common_seed_prefix_identical_with_materialized_edges(make_db):
    def build(con):
        _add_node(con, "C1:message:5", "widget base message")
        _add_evidence_node(con, "sem:C1:evidence:5", "C1:message:5", "widget evidence node")
        for i in range(4):
            _add_node(con, f"X{i}", f"widget filler {i}")

    db = make_db(build)
    with SameInformationMemory(db) as m:
        flat = m.flat("widget", top_k=4, max_chars=6000)
        graph = m.graph("widget", seed_k=4, max_chars=6000, hops=1, max_neighbors=4,
                        allowed_relations=[MATERIALIZED_RELATION])
    assert flat.metadata["common_seed_chunk_ids"] == graph.metadata["common_seed_chunk_ids"]
    n = graph.metadata["seed_chars_used"]
    assert flat.context[:n] == graph.context[:n]
    assert len(flat.context) <= 6000 and len(graph.context) <= 6000


# 19. Excluded / held-out cases cannot be reached through the new provenance edges.
def test_excluded_cases_unreachable_through_materialized_edges(make_db):
    def build(con):
        _add_case(con, "C1", repository="held_out_repo", issue_group="i1")
        _add_case(con, "C2", repository="kept_repo", issue_group="i2")
        _add_node(con, "C1:message:5", "widget heldout message")
        _add_evidence_node(con, "sem:C1:evidence:5", "C1:message:5", "widget heldout evidence", case_id="C1")
        _add_node(con, "C2:message:5", "widget kept message")
        _add_evidence_node(con, "sem:C2:evidence:5", "C2:message:5", "widget kept evidence", case_id="C2")
        _map_case(con, "base|node|C1:message:5", "C1")
        _map_case(con, "semantic|node|sem:C1:evidence:5", "C1")
        _map_case(con, "base|node|C2:message:5", "C2")
        _map_case(con, "semantic|node|sem:C2:evidence:5", "C2")

    db = make_db(build)
    with SameInformationMemory(db, excluded_repositories=["held_out_repo"]) as m:
        rows = m.rank_chunks("widget", 500)
        assert not any(r["doc_id"] == "base|node|C1:message:5" for r in rows)
        assert not any(r["chunk_id"].startswith("materialized|edge|") and "sem:C1:evidence:5" in r["text"]
                       for r in rows)
        m._build_adjacency()
        held_out_recs = m._adjacency.get("C1:message:5", [])
        assert not any(r.get("materialized") and r["source"] == "sem:C1:evidence:5" for r in held_out_recs)
        graph = m.graph("widget", seed_k=1, max_chars=6000, hops=1, max_neighbors=8,
                        allowed_relations=[MATERIALIZED_RELATION])
    reached = {n["neighbor_node_id"] for n in graph.metadata["graph_neighbors"]}
    assert "sem:C1:evidence:5" not in reached
    assert "sem:C2:evidence:5" in reached


# --- Citation dereference (raw message inlined after CITES_SOURCE_MESSAGE) --------

def _citation_case(con, *, assessment="sem:C1:assessment", evidence="sem:C1:evidence:5",
                   base_id="C1:message:5", raw_text="RAW-MESSAGE-CONTENT widget raw base body",
                   case_id="C1", extra_edge=None):
    _add_case(con, case_id, repository="kept_repo", issue_group="i1")
    _add_node(con, base_id, raw_text)
    _add_evidence_node(con, evidence, base_id, "evidence metadata widget", case_id=case_id)
    _add_node(con, assessment, "assessment widget", origin="semantic", payload={"case_id": case_id})
    _add_edge(con, "cites_e", assessment, evidence, "CITES_SOURCE_MESSAGE")
    for doc in (f"base|node|{base_id}", f"semantic|node|{evidence}", f"semantic|node|{assessment}"):
        _map_case(con, doc, case_id)
    if extra_edge:
        _add_edge(con, "extra_e", *extra_edge)


# 20. A citation neighbor's stored source_ref is dereferenced to the exact raw message.
def test_citation_dereferences_exact_raw_message(make_db):
    db = make_db(_citation_case)
    with SameInformationMemory(db) as m:
        _rank_override(m, ["semantic|node|sem:C1:assessment"])
        graph = m.graph("widget", seed_k=1, max_chars=8000, hops=1, max_neighbors=4,
                        allowed_relations=[MATERIALIZED_RELATION], per_neighbor_chars=2000)
        flat = m.flat("widget", top_k=1, max_chars=8000)
    assert graph.metadata["graph_raw_messages_inlined"] == 1
    assert graph.metadata["graph_raw_message_ids"] == ["C1:message:5"]
    assert graph.metadata["graph_raw_message_citation_ids"] == ["sem:C1:evidence:5"]
    assert "CITED_SOURCE_MESSAGE C1:message:5" in graph.context
    assert "RAW-MESSAGE-CONTENT" in graph.context
    record = graph.metadata["graph_neighbors"][0]
    assert record["raw_message_node_id"] == "C1:message:5"
    assert record["raw_message_citation_id"] == "sem:C1:evidence:5"
    assert record["raw_message_chars"] > 0
    # Budget accounting and flat isolation.
    assert len(graph.context) <= 8000
    assert graph.metadata["graph_raw_message_chars_used"] == record["raw_message_chars"]
    assert "graph_raw_messages_inlined" not in flat.metadata
    # Common seed prefix remains byte-for-byte identical.
    assert flat.metadata["common_seed_chunk_ids"] == graph.metadata["common_seed_chunk_ids"]
    n = graph.metadata["seed_chars_used"]
    assert flat.context[:n] == graph.context[:n]


# 21. Oversized raw message is truncated with a marker, under the per-neighbor cap.
def test_citation_raw_message_truncation(make_db):
    def build(con):
        _citation_case(con, raw_text="widget " + ("RAW " * 5000))

    db = make_db(build)
    with SameInformationMemory(db) as m:
        _rank_override(m, ["semantic|node|sem:C1:assessment"])
        graph = m.graph("widget", seed_k=1, max_chars=8000, hops=1, max_neighbors=1,
                        allowed_relations=[MATERIALIZED_RELATION], per_neighbor_chars=400)
    record = graph.metadata["graph_neighbors"][0]
    assert record["raw_message_truncated"] is True
    assert record["raw_message_chars"] <= 400
    assert DISPLAY_TRUNCATED_PER_NEIGHBOR in graph.context
    assert len(graph.context) <= 8000


# 22. Dereference respects exclusions: a held-out raw message is never surfaced.
def test_citation_dereference_respects_exclusions(make_db):
    def build(con):
        _add_case(con, "C1", repository="held_out_repo", issue_group="i1")
        _add_case(con, "C2", repository="kept_repo", issue_group="i2")
        _add_node(con, "C1:message:5", "SECRET-RAW-CONTENT widget")
        _add_evidence_node(con, "sem:C2:evidence:5", "C1:message:5", "evidence widget", case_id="C2")
        _add_node(con, "sem:C2:assessment", "assessment widget", origin="semantic", payload={"case_id": "C2"})
        _add_edge(con, "cites_e", "sem:C2:assessment", "sem:C2:evidence:5", "CITES_SOURCE_MESSAGE")
        _map_case(con, "base|node|C1:message:5", "C1")
        _map_case(con, "semantic|node|sem:C2:evidence:5", "C2")
        _map_case(con, "semantic|node|sem:C2:assessment", "C2")

    db = make_db(build)
    with SameInformationMemory(db, excluded_repositories=["held_out_repo"]) as m:
        _rank_override(m, ["semantic|node|sem:C2:assessment"])
        graph = m.graph("widget", seed_k=1, max_chars=8000, hops=1, max_neighbors=4,
                        allowed_relations=[MATERIALIZED_RELATION], per_neighbor_chars=2000)
    assert graph.metadata["graph_raw_messages_inlined"] == 0
    assert graph.metadata["graph_raw_message_ids"] == []
    assert "SECRET-RAW-CONTENT" not in graph.context
    assert graph.metadata["graph_neighbors"][0]["neighbor_node_id"] == "sem:C2:evidence:5"


# 23. Dereference never triggers arbitrary depth-2 traversal.
def test_citation_dereference_no_arbitrary_depth2_traversal(make_db):
    def build(con):
        _citation_case(con, extra_edge=("C1:message:5", "THIRD", "QUALIFIED_BY"))
        _add_node(con, "THIRD", "THIRD-NODE widget third")

    db = make_db(build)
    with SameInformationMemory(db) as m:
        _rank_override(m, ["semantic|node|sem:C1:assessment"])
        graph = m.graph("widget", seed_k=1, max_chars=8000, hops=1, max_neighbors=4,
                        allowed_relations=[MATERIALIZED_RELATION, "QUALIFIED_BY"], per_neighbor_chars=2000)
    neighbor_ids = {n["neighbor_node_id"] for n in graph.metadata["graph_neighbors"]}
    assert "THIRD" not in neighbor_ids
    assert all(n["from_node"] != "C1:message:5" for n in graph.metadata["graph_neighbors"])
    # The raw message was inlined, but not used as a traversal root.
    assert graph.metadata["graph_raw_message_ids"] == ["C1:message:5"]
    assert graph.metadata["graph_neighbors_added"] == 1
