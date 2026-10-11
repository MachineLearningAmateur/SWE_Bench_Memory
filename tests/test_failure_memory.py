import copy
import json
import sqlite3

import numpy as np
import pytest

from src import failure_corpus as corpus
from src.failure_pilot import CONDITIONS, completed_prefix, select_tasks, trial_order
from src.failure_retrieval import FailureMemory, fingerprint, uncovered_span


class CharacterTokenizer:
    def encode(self, text, **kwargs):
        return list(text)

    def __call__(self, text, **kwargs):
        return {"offset_mapping": [(i, i + 1) for i in range(len(text))]}


class FakeModels:
    truncations = []

    def embed(self, texts, query=False):
        result = np.zeros((len(texts), 1024), dtype=np.float32)
        result[:, 0] = 1
        return result

    def rerank(self, query, texts):
        return [float(text.count("needle")) for text in texts]


def source_fixture(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    annotation = {"annotation_status": "attributed", "alternative_explanations": ["environment may matter"],
                  "notes": "provisional", "evidence_quality": "high",
                  "primary_failure": {"critical_step": 4, "evidence_steps": [4, 8], "short_description": "repeated invocation"},
                  "secondary_failures": [], "incidental_or_recovered_errors": [],
                  "final_failure_manifestation": {"step": 9, "description": "unresolved"}}
    body = {"action": {"command": "needle", "done": False}, "message": {},
            "result": {"observation": {"exit_code": 1, "stderr": "missing file"}}}
    view = {"source_normalized": {"task": {"problem_statement": "Repair needle", "repo": "org/project"}},
            "events": [{"step_id": 4, "raw_step_ids": [4, 5, 6], "body": body},
                       {"step_id": 8, "raw_step_ids": [8], "same_as": 4},
                       {"step_id": 9, "raw_step_ids": [9], "terminal": {"terminal_state": "turn_limit"}}],
            "official_result": {"resolved": False}}
    corpus.write_json(root / "view.json", view)
    corpus.write_json(root / "annotation.json", annotation)
    row = {"instance_id": "org__project-1", "repo": "org/project", "annotation": annotation,
           "artifacts": {"frontier_structured": {"path": "view.json", "sha256": corpus.digest(root / "view.json")},
                         "parsed_annotation": {"path": "annotation.json", "sha256": corpus.digest(root / "annotation.json")}}}
    return root, row


@pytest.fixture
def memory(tmp_path, monkeypatch):
    root, row = source_fixture(tmp_path)
    cfg = corpus.config()
    cfg["source"]["path"] = str(root)
    cfg["corpus"] = str(tmp_path / "memory")
    cfg["retrieval"].update(chunk_tokens=200, overlap_tokens=30, memory_tokens=1500, graph_base_candidates=2, graph_seeds=1, candidate_limit=8)
    monkeypatch.setattr(corpus, "audit_source", lambda root, cfg: ([row], {"commit": "test"}))
    corpus.build(cfg, tokenizer=CharacterTokenizer())
    result = FailureMemory(cfg, models=FakeModels(), solver_tokenizer=CharacterTokenizer())
    yield result
    result.close()


def test_import_preserves_feedback_and_original_step_aliases(tmp_path):
    root, row = source_fixture(tmp_path)
    case, documents, edges = corpus.records_for_case(root, row)
    second = next(d for d in documents if d["id"].endswith("step:8"))
    assert "missing file" in second["text"]
    assert "Repeated evidence from step 4" in second["text"]
    assert any(r == "CITES_EVIDENCE" and b.endswith("step:8") for _, r, b in edges)
    assert all("environment may matter" in d["text"] for d in documents if d["kind"] == "annotation")
    assert case["annotated"]


def test_deferred_case_does_not_get_a_fabricated_diagnosis(tmp_path):
    root, row = source_fixture(tmp_path)
    row["annotation"] = None
    case, documents, edges = corpus.records_for_case(root, row)
    assert not case["annotated"]
    assert not edges
    assert {d["kind"] for d in documents} == {"task", "event", "terminal"}


def test_broken_evidence_reference_fails_import(tmp_path):
    root, row = source_fixture(tmp_path)
    row["annotation"]["primary_failure"]["evidence_steps"].append(999)
    corpus.write_json(root / "annotation.json", row["annotation"])
    with pytest.raises(ValueError, match="Unresolved annotation"):
        corpus.records_for_case(root, row)


def test_repeated_evidence_cycles_and_path_escape_fail(tmp_path):
    with pytest.raises(ValueError, match="escapes"):
        corpus.source_path(tmp_path, "../outside")
    event = {"step_id": 4, "same_as": 4}
    with pytest.raises(ValueError, match="Cyclic"):
        corpus.resolve_body(event, {4: event})


def test_long_unicode_chunking_has_complete_coverage():
    text = "αβ\n🧪 testing " * 100
    spans = list(corpus.chunk_spans(text, CharacterTokenizer(), 100, 20))
    covered = set()
    for left, right, piece in spans:
        assert piece == text[left:right]
        assert len(piece) <= 100
        covered.update(range(left, right))
    assert covered == set(range(len(text)))


def test_overlap_removal_handles_reverse_rank_order_and_middle_overlap():
    assert uncovered_span(0, 100, [(20, 80)]) == (0, 20)
    assert uncovered_span(40, 100, [(0, 70)]) == (70, 100)
    assert uncovered_span(40, 60, [(0, 100)]) == (40, 40)


def test_source_citation_diagnostic_is_explicitly_not_gold(memory):
    report = memory.citation_diagnostics()
    assert len(report["rows"]) == 1
    assert "not new-task relevance" in report["scope"]
    assert report["rows"][0]["expected_documents"] == 2


def test_lexical_query_uses_terms_after_first_32(memory):
    prefix = " ".join(f"absent{i}" for i in range(40))
    ranking, _ = memory.lexical(prefix + " needle", 100)
    assert ranking


def test_source_issue_is_rejected_before_search(memory):
    with pytest.raises(ValueError, match="Source issue"):
        memory.retrieve_all("query", instance_id="org__project-1")


def test_equal_corpus_bounded_candidates_and_budget(memory):
    outputs = memory.retrieve_all("needle", instance_id="new-issue", diagnostics=True)
    assert set(outputs) == set(CONDITIONS)
    for result in outputs.values():
        assert len(result.context) <= 1500
        assert result.metadata["memory_tokens"] == len(result.context)
        assert all("END_SOURCE" in result.context for _ in result.selected)
    flat, graph = outputs["hybrid_rag"], outputs["graph_rag"]
    assert flat.metadata["corpus_sha256"] == graph.metadata["corpus_sha256"]
    assert flat.metadata["shared_seed_ids"] == graph.metadata["shared_seed_ids"]
    assert graph.metadata["candidate_count"] <= 8
    assert not any(r["graph_path"] for r in flat.selected)
    assert all(len(p) <= 2 for p in graph.metadata["graph_candidate_paths"].values())
    assert outputs["no_memory"].context == ""
    assert outputs["checklist"].selected == []
    repeated = memory.retrieve_all("needle", instance_id="new-issue")
    assert {k: v.context for k, v in repeated.items()} == {k: v.context for k, v in outputs.items()}


def test_graph_disabled_is_exact_flat_pool(memory):
    rankings, scores = memory.candidates("needle")
    candidates, paths = memory.graph_candidates(rankings["hybrid"], scores, hops=0)
    assert candidates == rankings["hybrid"]
    assert paths == {}


def test_cache_rejects_modified_embeddings(memory):
    memory.build_embeddings()
    memory.vectors._mmap.close()
    memory.vectors = None
    (memory.out / "embeddings.npy").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="embedding cache"):
        memory.build_embeddings()


def test_same_repository_is_allowed_but_same_issue_and_duplicate_are_not():
    source = [{"id": "source", "problem_statement": "Known issue https://github.com/a/b/issues/10"}]
    tasks = [{"instance_id": str(i), "repo": "a/b", "problem_statement": f"new behavior {i}"} for i in range(8)]
    tasks += [{"instance_id": "source", "repo": "a/b", "problem_statement": "different text"},
              {"instance_id": "copy", "repo": "a/b", "problem_statement": "KNOWN  ISSUE https://github.com/a/b/issues/10"},
              {"instance_id": "link", "repo": "a/b", "problem_statement": "related https://github.com/a/b/issues/10"}]
    dev, pilot, excluded = select_tasks(tasks, source, {"0"}, development=2, evaluation=3)
    assert len(dev) == 2 and len(pilot) == 3
    assert not {t["instance_id"] for t in dev} & {t["instance_id"] for t in pilot}
    assert {e["instance_id"] for e in excluded} == {"source", "copy", "link", "0"}
    assert select_tasks(tasks, source, {"0"}, development=2, evaluation=3) == (dev, pilot, excluded)


def test_fifty_unique_trials_and_balanced_serial_positions():
    order = trial_order([{"instance_id": str(i)} for i in range(10)])
    assert len(order) == len({(r["instance_id"], r["condition"]) for r in order}) == 50
    for position in range(5):
        assert sorted(r["condition"] for r in order[position::5]) == sorted(list(CONDITIONS) * 2)


def test_resume_keeps_model_failure_and_refuses_changed_record(tmp_path):
    trial = {"instance_id": "one", "condition": "graph_rag"}
    record = {**trial, "exit_status": "RepeatedFormatError", "error": None, "model_patch": ""}
    directory = tmp_path / "runs/graph_rag/one"
    directory.mkdir(parents=True)
    corpus.write_json(directory / "run.json", record)
    corpus.write_json(tmp_path / "records.json", [record])
    assert completed_prefix(tmp_path, {"trial_order": [trial]}) == [record]
    corpus.write_json(directory / "run.json", {**record, "model_patch": "modified"})
    with pytest.raises(ValueError, match="changed"):
        completed_prefix(tmp_path, {"trial_order": [trial]})
