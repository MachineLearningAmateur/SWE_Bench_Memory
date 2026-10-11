"""Shared lexical/dense search, bounded graph candidates and local Qwen reranking."""
from __future__ import annotations

import argparse
import gc
import hashlib
import inspect
import json
import random
import re
import sqlite3
import time
from collections import defaultdict, deque
from pathlib import Path

import numpy as np

from .failure_corpus import DEFAULT_CONFIG, canonical, config, digest, load_tokenizer, read_json, rooted, write_json
from .memory_conditions import MemoryResult, WARNING

CHECKLIST = (
    "GENERAL DEBUGGING CHECKLIST\n"
    "Inspect the relevant repository code and layout.\n"
    "Adapt your investigation after a failed command instead of repeating it unchanged.\n"
    "Implement the requested behavior before declaring completion.\n"
    "Run relevant checks and accurately report what actually ran."
)
CONDITIONS = ("no_memory", "checklist", "bm25", "hybrid_rag", "graph_rag")


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def uncovered_span(left, right, occupied):
    spans = [(left, right)]
    for begin, end in sorted(occupied):
        next_spans = []
        for a, b in spans:
            if end <= a or begin >= b:
                next_spans.append((a, b))
            else:
                if a < begin:
                    next_spans.append((a, begin))
                if end < b:
                    next_spans.append((end, b))
        spans = next_spans
    return max(spans, key=lambda s: (s[1] - s[0], -s[0])) if spans else (left, left)


class LocalModels:
    """Only one model resident at once. Public weights do not need a saved HF token."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.model = self.tokenizer = self.kind = None
        self.truncations = []
        self.batch_size = cfg["retrieval"]["batch_size"]

    def unload(self):
        self.model = self.tokenizer = self.kind = None
        gc.collect()
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def load(self, kind):
        if self.kind == kind:
            return
        self.unload()
        import torch
        from transformers import AutoModel, AutoModelForCausalLM, AutoTokenizer
        spec = self.cfg["models"][kind]
        self.tokenizer = load_tokenizer(spec, padding_side="left")
        self.tokenizer.pad_token = self.tokenizer.eos_token
        cls = AutoModel if kind == "embedding" else AutoModelForCausalLM
        device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = cls.from_pretrained(spec["name"], revision=spec["revision"], token=False,
                                        torch_dtype=torch.float16 if device == "cuda" else torch.float32,
                                        attn_implementation="sdpa").to(device).eval()
        self.kind = kind

    def _run_batches(self, encoded, infer):
        import torch
        results = [None] * len(encoded)
        order = sorted(range(len(encoded)), key=lambda i: (len(encoded[i]), i))
        offset = 0
        batch_size = self.batch_size
        while offset < len(order):
            positions = order[offset:offset + batch_size]
            batch = None
            try:
                batch = self.tokenizer.pad({"input_ids": [encoded[i] for i in positions]}, padding=True, return_tensors="pt").to(self.model.device)
                with torch.inference_mode():
                    values = infer(batch)
                for pos, value in zip(positions, values):
                    results[pos] = value
                offset += len(positions)
            except torch.cuda.OutOfMemoryError:
                del batch
                batch = None
                gc.collect()
                torch.cuda.empty_cache()
                if batch_size == 1:
                    raise
                batch_size = max(1, batch_size // 2)
            finally:
                if batch is not None:
                    del batch
        self.batch_size = batch_size
        return results

    def embed(self, texts, *, query=False):
        self.load("embedding")
        instruction = self.cfg["retrieval"]["instruction"]
        prepared = [f"Instruct: {instruction}\nQuery: {text}" if query else text for text in texts]
        limit = self.cfg["retrieval"]["model_input_tokens"]
        encoded = []
        for text in prepared:
            ids = self.tokenizer.encode(text, add_special_tokens=False)
            if len(ids) > limit:
                self.truncations.append({"stage": "embedding_query" if query else "embedding_document", "text_sha256": fingerprint(text), "original_tokens": len(ids), "kept_tokens": limit})
            encoded.append(ids[:limit])

        def infer(batch):
            import torch.nn.functional as F
            vectors = self.model(**batch).last_hidden_state[:, -1]
            return F.normalize(vectors.float(), p=2, dim=1).cpu().numpy()

        return np.asarray(self._run_batches(encoded, infer), dtype=np.float32)

    def rerank(self, query, texts):
        self.load("reranker")
        prefix = '<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the Instruct provided. Answer only "yes" or "no".<|im_end|>\n<|im_start|>user\n'
        suffix = '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'
        start = self.tokenizer.encode(prefix, add_special_tokens=False)
        end = self.tokenizer.encode(suffix, add_special_tokens=False)
        instruction = self.cfg["retrieval"]["instruction"]
        limit = self.cfg["retrieval"]["model_input_tokens"] - len(start) - len(end)
        query_ids = self.tokenizer.encode(f"<Instruct>: {instruction}\n<Query>: {query}", add_special_tokens=False)
        encoded = []
        for text in texts:
            doc = self.tokenizer.encode("\n<Document>: " + text, add_special_tokens=False)
            # Preserve the complete passage; long queries get an explicitly logged cap.
            keep_doc = min(len(doc), limit - 128)
            keep_query = min(len(query_ids), limit - keep_doc)
            if keep_query < len(query_ids) or keep_doc < len(doc):
                self.truncations.append({"stage": "reranker", "query_sha256": fingerprint(query), "document_sha256": fingerprint(text),
                                         "query_tokens": len(query_ids), "kept_query_tokens": keep_query,
                                         "document_tokens": len(doc), "kept_document_tokens": keep_doc})
            encoded.append(start + query_ids[:keep_query] + doc[:keep_doc] + end)
        yes, no = (self.tokenizer.convert_tokens_to_ids(t) for t in ("yes", "no"))

        def infer(batch):
            logits = self.model(**batch, logits_to_keep=1).logits[:, -1].float()
            return (logits[:, yes] - logits[:, no]).cpu().tolist()

        return self._run_batches(encoded, infer)


class FailureMemory:
    def __init__(self, cfg, *, models=None, solver_tokenizer=None):
        self.cfg = cfg
        self.out = rooted(cfg["corpus"])
        self.manifest = read_json(self.out / "manifest.json")
        if digest(self.out / "corpus.sqlite") != self.manifest["corpus_sha256"]:
            raise ValueError("Memory database changed")
        if cfg["models"]["embedding"] != self.manifest["config"]["models"]["embedding"]:
            raise ValueError("Chunk tokenizer differs from corpus manifest")
        self.db = sqlite3.connect((self.out / "corpus.sqlite").as_uri() + "?mode=ro", uri=True)
        self.db.row_factory = sqlite3.Row
        self.rows = [dict(r) for r in self.db.execute("SELECT c.*,d.case_id,d.repo,d.kind,d.guard,d.provenance FROM chunks c JOIN documents d ON d.id=c.doc_id ORDER BY c.id")]
        self.by_id = {r["id"]: r for r in self.rows}
        self.position = {r["id"]: i for i, r in enumerate(self.rows)}
        self.cases = {r["id"] for r in self.db.execute("SELECT id FROM cases")}
        self.models = models or LocalModels(cfg)
        self.solver_tokenizer = solver_tokenizer
        self.vectors = None
        self.cache = self.out / "cache"
        self.cache.mkdir(exist_ok=True)

    def close(self):
        self.db.close()
        if self.vectors is not None and hasattr(self.vectors, "_mmap"):
            self.vectors._mmap.close()
        self.vectors = None
        if hasattr(self.models, "unload"):
            self.models.unload()

    def text(self, row):
        return f"PROJECT {row['repo']}\nRECORD {row['kind']}\n{row['guard']}\n{row['text']}"

    def build_embeddings(self):
        identity = {"corpus": self.manifest["corpus_sha256"], "model": self.cfg["models"]["embedding"],
                    "input_tokens": self.cfg["retrieval"]["model_input_tokens"],
                    "implementation": fingerprint(inspect.getsource(LocalModels) + inspect.getsource(self.text))}
        path = self.out / "embeddings.npy"
        receipt = self.out / "embeddings.json"
        if receipt.exists():
            saved = read_json(receipt)
            if saved["identity"] != identity or saved["sha256"] != digest(path):
                raise ValueError("Stale or modified embedding cache; build a new corpus version")
            self.vectors = np.load(path, mmap_mode="r")
            return
        # A failed build never publishes a receipt or a partly valid index.
        temporary = self.out / "embeddings.partial.npy"
        vectors = np.lib.format.open_memmap(temporary, mode="w+", dtype=np.float32, shape=(len(self.rows), 1024))
        for offset in range(0, len(self.rows), 128):
            batch = self.rows[offset:offset + 128]
            values = self.models.embed([self.text(row) for row in batch])
            if values.shape != (len(batch), 1024) or not np.isfinite(values).all():
                raise ValueError("Invalid embedding shape or values")
            vectors[offset:offset + len(batch)] = values
            vectors.flush()
            print(f"Embedded {offset + len(batch)}/{len(self.rows)}", flush=True)
        del vectors
        temporary.replace(path)
        write_json(receipt, {"identity": identity, "sha256": digest(path), "truncations": self.models.truncations})
        self.vectors = np.load(path, mmap_mode="r")

    def lexical(self, query, limit):
        terms = list(dict.fromkeys(re.findall(r"\w+", query, flags=re.UNICODE)))
        if not terms:
            return [], {}
        expression = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
        rows = self.db.execute("SELECT rowid,bm25(fts) AS score FROM fts WHERE fts MATCH ? ORDER BY score,rowid LIMIT ?", (expression, limit)).fetchall()
        return [r[0] for r in rows], {r[0]: r[1] for r in rows}

    def candidates(self, query):
        if self.vectors is None:
            self.build_embeddings()
        limit = self.cfg["retrieval"]["candidate_limit"]
        lexical, bm25 = self.lexical(query, limit)
        key = fingerprint({"query": query, "model": self.cfg["models"]["embedding"], "settings": self.cfg["retrieval"], "implementation": digest(__file__)})
        path = self.cache / f"query-{key}.json"
        if path.exists():
            vector = np.asarray(read_json(path)["vector"], dtype=np.float32)
        else:
            vector = self.models.embed([query], query=True)[0]
            write_json(path, {"vector": vector.tolist(), "truncations": self.models.truncations})
        similarities = self.vectors @ vector
        dense = sorted(self.by_id, key=lambda i: (-float(similarities[self.position[i]]), self.by_id[i]["chunk_id"]))[:limit]
        scores = defaultdict(float)
        for ranking in (lexical, dense):
            for rank, index in enumerate(ranking, 1):
                scores[index] += 1 / (self.cfg["retrieval"]["rrf_constant"] + rank)
        fused = sorted(scores, key=lambda i: (-scores[i], self.by_id[i]["chunk_id"]))[:limit]
        return {"bm25": lexical, "dense": dense, "hybrid": fused}, {i: {"bm25": bm25.get(i), "cosine": float(similarities[self.position[i]]), "rrf": scores.get(i)} for i in self.by_id}

    def graph_candidates(self, fused, scores, *, hops=None, shuffle=False):
        settings = self.cfg["retrieval"]
        hops = settings["graph_hops"] if hops is None else hops
        if hops == 0:
            return list(fused), {}
        edges = [tuple(r) for r in self.db.execute("SELECT source,relation,target FROM edges ORDER BY source,relation,target")]
        if shuffle:
            targets = [e[2] for e in edges]
            random.Random(42).shuffle(targets)
            edges = [(a, rel, b) for (a, rel, _), b in zip(edges, targets)]
        adjacency = defaultdict(list)
        for source, relation, target in edges:
            adjacency[source].append((target, source, relation, target))
            adjacency[target].append((source, source, relation, target))
        seeds = []
        for i in fused[:settings["graph_seeds"]]:
            row = self.by_id[i]
            seeds.append(row["doc_id"])
            if row["kind"] == "relation":
                p = json.loads(row["provenance"])
                seeds.extend([p["source"], p["target"]])
        reached = {node: [] for node in seeds}
        queue = deque(dict.fromkeys(seeds))
        while queue:
            node = queue.popleft()
            path = reached[node]
            if len(path) >= hops:
                continue
            for other, source, relation, target in adjacency[node]:
                if other not in reached:
                    reached[other] = path + [{"source": source, "relation": relation, "target": target,
                                              "direction": "forward" if node == source else "reverse"}]
                    queue.append(other)
        base = fused[:settings["graph_base_candidates"]]
        used = set(base)
        neighbors = [i for i, row in self.by_id.items() if row["doc_id"] in reached and reached[row["doc_id"]] and i not in used]
        neighbors.sort(key=lambda i: (len(reached[self.by_id[i]["doc_id"]]), -scores[i]["cosine"], self.by_id[i]["chunk_id"]))
        limit = settings["candidate_limit"]
        chosen = base + neighbors[:limit - len(base)]
        paths = {i: reached[self.by_id[i]["doc_id"]] for i in chosen if i not in used}
        for i in fused:
            if len(chosen) >= limit:
                break
            if i not in chosen:
                chosen.append(i)
        return chosen, paths

    def rerank(self, query, candidates):
        spec = {"model": self.cfg["models"]["reranker"], "settings": self.cfg["retrieval"], "query": query,
                "corpus": self.manifest["corpus_sha256"], "implementation": digest(__file__)}
        path = self.cache / f"rerank-{fingerprint(spec)}.json"
        cached = read_json(path) if path.exists() else {"scores": {}, "truncations": []}
        missing = [i for i in candidates if str(i) not in cached["scores"]]
        if missing:
            scores = self.models.rerank(query, [self.text(self.by_id[i]) for i in missing])
            if len(scores) != len(missing) or not np.isfinite(scores).all():
                raise ValueError("Invalid reranker scores")
            cached["scores"].update({str(i): float(score) for i, score in zip(missing, scores)})
            cached["truncations"] = list(self.models.truncations)
            write_json(path, cached)
        scores = {i: cached["scores"][str(i)] for i in candidates}
        return sorted(candidates, key=lambda i: (-scores[i], self.by_id[i]["chunk_id"])), scores

    def tokenizer(self):
        if self.solver_tokenizer is None:
            self.solver_tokenizer = load_tokenizer(self.cfg["models"]["tokenizer"])
        return self.solver_tokenizer

    def count(self, text):
        return len(self.tokenizer().encode(text, add_special_tokens=False))

    def pack(self, ranking, scores, paths=None):
        budget = self.cfg["retrieval"]["memory_tokens"]
        context = WARNING + "\n\n"
        selected, seen, intervals = [], set(), defaultdict(list)
        for i in ranking:
            row = self.by_id[i]
            body = row["text"]
            left, right = row["start_char"], row["end_char"]
            # Strip already shown overlap without silently dropping a new suffix.
            left, right = uncovered_span(left, right, intervals[row["doc_id"]])
            if left >= right:
                continue
            body = body[left - row["start_char"]:right - row["start_char"]]
            key = fingerprint(body)
            if key in seen:
                continue
            heading = f"SOURCE {row['chunk_id']} | {row['repo']} | {row['kind']}\n"
            guard = row["guard"] + "\n" if row["guard"] else ""
            prefix = heading + guard
            suffix = "\nEND_SOURCE\n\n"
            full = prefix + body + suffix
            truncated = False
            if self.count(context + full) > budget:
                marker = "\n[DISPLAY TRUNCATED]"
                low, high = 0, len(body)
                while low < high:
                    mid = (low + high + 1) // 2
                    if self.count(context + prefix + body[:mid] + marker + suffix) <= budget:
                        low = mid
                    else:
                        high = mid - 1
                if low < 80:
                    continue
                body = body[:low]
                full = prefix + body + marker + suffix
                right = left + low
                truncated = True
            if self.count(context + full) > budget:
                continue
            context += full
            intervals[row["doc_id"]].append((left, right))
            seen.add(key)
            selected.append({"chunk_id": row["chunk_id"], "document_id": row["doc_id"], "case_id": row["case_id"],
                             "kind": row["kind"], "start_char": left, "end_char": right, "truncated": truncated,
                             "provenance": json.loads(row["provenance"]), "scores": scores.get(i, {}),
                             "graph_path": (paths or {}).get(i, [])})
        return context, selected

    def retrieve_all(self, query, *, instance_id=None, diagnostics=False):
        if instance_id in self.cases:
            raise ValueError("Source issue cannot be used as a held-out retrieval task")
        started = time.perf_counter()
        rankings, scores = self.candidates(query)
        graph_started = time.perf_counter()
        graph, paths = self.graph_candidates(rankings["hybrid"], scores)
        graph_seconds = time.perf_counter() - graph_started
        hybrid, hybrid_scores = self.rerank(query, rankings["hybrid"])
        graph, graph_scores = self.rerank(query, graph)
        results = {}
        elapsed = time.perf_counter() - started
        for name, ranking in [("bm25", rankings["bm25"]), ("hybrid_rag", hybrid), ("graph_rag", graph)]:
            reranking = hybrid_scores if name == "hybrid_rag" else graph_scores if name == "graph_rag" else {}
            per_scores = {i: {**scores[i], "reranker": reranking.get(i)} for i in ranking}
            context, selected = self.pack(ranking, per_scores, paths if name == "graph_rag" else {})
            metadata = {"corpus_sha256": self.manifest["corpus_sha256"], "models": self.cfg["models"],
                        "settings": self.cfg["retrieval"], "memory_tokens": self.count(context),
                        "candidate_ids": [self.by_id[i]["chunk_id"] for i in ranking],
                        "candidate_count": len(ranking), "shared_seed_ids": [self.by_id[i]["chunk_id"] for i in rankings["hybrid"][:self.cfg["retrieval"]["graph_seeds"]]],
                        "graph_candidate_paths": {self.by_id[i]["chunk_id"]: p for i, p in paths.items()} if name == "graph_rag" else {},
                        "graph_traversal_seconds": graph_seconds if name == "graph_rag" else 0,
                        "shared_precomputation_seconds": elapsed, "model_truncations": list(self.models.truncations)}
            results[name] = MemoryResult(name, query, context, selected, metadata)
        for name, text in (("no_memory", ""), ("checklist", CHECKLIST)):
            results[name] = MemoryResult(name, query, text, [], {"memory_tokens": self.count(text), "retrieval": "none"})
        if diagnostics:
            extra = {}
            for name in ("dense", "hybrid"):
                extra[name] = [self.by_id[i]["chunk_id"] for i in rankings[name]]
            for name, hops, shuffle in (("graph_disabled", 0, False), ("graph_one_hop", 1, False), ("graph_shuffled", 2, True)):
                ids, diagnostic_paths = self.graph_candidates(rankings["hybrid"], scores, hops=hops, shuffle=shuffle)
                ids, _ = self.rerank(query, ids)
                extra[name] = {"candidate_ids": [self.by_id[i]["chunk_id"] for i in ids], "reachable_added": len(diagnostic_paths)}
            results["hybrid_rag"].metadata["development_diagnostics"] = extra
        return results

    def citation_diagnostics(self):
        """Source-derived candidate recall; explicitly NOT unseen-issue relevance."""
        rows = []
        annotations = self.db.execute("SELECT id,text FROM documents WHERE kind='annotation' AND id LIKE '%:primary' ORDER BY id").fetchall()
        for annotation in annotations:
            finding = json.loads(annotation["text"])["finding"]
            query = finding["short_description"]
            expected = {r[0] for r in self.db.execute("SELECT target FROM edges WHERE source=? AND relation IN ('CITES_EVIDENCE','IDENTIFIES_CRITICAL_STEP')", (annotation["id"],))}
            rankings, scores = self.candidates(query)
            rankings["graph"], _ = self.graph_candidates(rankings["hybrid"], scores)
            rows.append({"annotation_id": annotation["id"], "expected_documents": len(expected),
                         "candidate_recall": {name: len(expected & {self.by_id[i]["doc_id"] for i in ranking}) / len(expected) if expected else None
                                              for name, ranking in rankings.items()}})
        return {"scope": "Source annotation descriptions querying their own cited evidence; annotations remain searchable. This is a candidate-recovery diagnostic, not new-task relevance or independent validation.", "rows": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--index", action="store_true")
    parser.add_argument("--query")
    args = parser.parse_args()
    memory = FailureMemory(config(args.config))
    try:
        if args.index:
            memory.build_embeddings()
        if args.query:
            results = memory.retrieve_all(args.query)
            print(json.dumps({k: {"tokens": v.metadata["memory_tokens"], "records": len(v.selected)} for k, v in results.items()}))
    finally:
        memory.close()


if __name__ == "__main__":
    main()
