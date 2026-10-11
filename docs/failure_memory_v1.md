# Failure50 retrieval experiment

This version uses only the sibling `slm-failure-memory-pilot` archive at commit
`267e00c456f273efb2b41f6d0717be1c74c4f941`. The source archive is read-only input.
The prior TrajDebug corpus and runs remain historical artifacts, not inputs to this study.

The [2026-10-11 preparation record](../audit/failure50_v1/README.md) records the
local validation and subsequent solver launch.

## Data and boundaries

The authoritative annotation report is `reports/frontier_expansion_v1/result.json`,
joined against both cohort manifests. It contains 50 failed attempts, 48 existing
annotations, and two explicit oversized deferrals. Older README/cohort annotation
counts describe earlier stages and are not used to determine annotation availability.
All linked evidence bytes are hash-checked before importing.

The importer uses the exact grouped action-and-feedback view supplied to the annotator,
with original step IDs and source artifact hashes. Repeated observations resolve to
their original content. Existing alternatives, recovered errors, and uncertainty stay
visible. Annotations are model interpretations, not ground-truth causes. No new lessons
are generated. Deferrals retain their recorded events without invented annotations.
Historical post-run evaluator summaries are labeled separately; complete evaluator logs,
gold patches, test patches, request envelopes, and telemetry are not searchable.
Original full evidence stays in the archive, including omissions in its published views.

Source task IDs, duplicate normalized issue text, shared issue/PR URLs, and previously
exposed local task IDs are excluded before selection. Same-project experience is allowed
for different issues. Seed 42 selects 20 development and 10 pilot tasks in repository
round-robin order from the pinned Verified revision. This is a modified Verified subset,
not a score for the full 500-problem benchmark.

## Retrieval contract

All methods share the same rendered records and searchable relation statements.
Passages have at most 768 embedding tokens and 96-token overlap. Local, revision-pinned
Qwen3 0.6B embedding and reranker models run sequentially, with adaptive batch sizes for
GPU memory exhaustion. Embeddings are normalized 1,024-dimensional vectors searched
exactly. No external vector database or hosted retrieval API is used.

BM25 and dense retrieval each supply up to 100 candidates, fused with RRF constant 60.
The full issue is the query; neural input truncations are recorded. Flat hybrid reranks
the top 100 fused candidates. Graph retrieval keeps the top 50, then adds up to 50
reachable passages from the top eight fused seeds, bounded to two hops. Neighbors order
by hop depth, descending cosine relevance, then stable ID; missing slots use ordinary
search candidates. Both methods rerank at most 100 passages with the same scorer.
The graph may examine more neighbors before this cap; its paths and traversal time are
logged, so equal reranker budgets are not claimed to imply equal total computation.

Edges express annotation membership, cited evidence, critical-step attribution,
incidental/recovered-error descriptions, and historical terminal evidence. Traversal
may use either direction, with the original edge direction retained in the audit.
There are no automatically traversable timeline or shared-label hubs. Graph traversal
changes selection; it receives no free evidence or special prompt rendering.

The solver memory limit is 4,000 tokens, including framing and uncertainty notices,
measured by the pinned GPT-OSS tokenizer. Truncation preserves source framing and logs
offsets. No-memory and checklist conditions are naturally shorter and are not padded.

## Commands

Use an isolated Python environment for retrieval. On this Windows CUDA host:

```powershell
python -m venv .venv-retrieval
.\.venv-retrieval\Scripts\python -m pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cu128
.\.venv-retrieval\Scripts\python -m pip install -r requirements-retrieval.txt
.\.venv-retrieval\Scripts\python -m src.failure_corpus
.\.venv-retrieval\Scripts\python -m src.failure_retrieval --index
.\.venv-retrieval\Scripts\python -m src.failure_pilot prepare --output results/failure50_verified10_v1
.\.venv-retrieval\Scripts\python -m src.failure_pilot contexts --output results/failure50_verified10_v1
```

The first two builds and context preparation use local neural inference. They make no
solver API calls. Corpus paths, models, token budgets, and candidate settings live in
`config/failure_memory.yaml`; use a new corpus/output version when changing them.
The source archive need not be mounted into the solver container.

After reviewing development retrieval outputs and passing the regression suite:

```powershell
docker compose start research
docker compose exec -T research python -m src.failure_pilot run --output /workspace/results/failure50_verified10_v1
```

`run` starts paid GPT-OSS inference and automatically invokes official grading after
all 50 attempts. Its five conditions are no memory, fixed checklist, BM25, hybrid RAG,
and graph RAG. The existing solver adapter, 100-step limit, 3,600-second wall limit,
common wrapper, and balanced trial ordering are frozen across conditions.

`resume` only accepts a stopped run with an unchanged completed prefix. It never
overwrites an attempt directory or automatically retries a recorded infrastructure
failure. Interrupted attempts require an explicit, documented recovery. Ordinary model
failures remain scored attempts. `grade` can resume official grading without rerunning
the solver. Pre-cache task images to avoid the existing container startup timeout.

## Artifacts and interpretation

- `manifest.json`: source/config/code hashes, frozen dataset revision and trial order.
- `selection_audit.json`: source IDs and exclusions; `development.json`: public dev queries.
- `contexts/`: exact immutable bundles; `contexts_manifest.json`: bundle hashes.
- `retrieval_audit.json`: token counts, source diversity and graph exposure.
- `source_citation_diagnostics.json`: source-derived candidate recall for the 48 existing
  primary findings. Their annotations remain searchable; this is not independent
  relevance labeling or performance on unseen problems.
- Development hybrid bundles also contain dense, fusion-only, disabled-traversal,
  one-hop, and shuffled-edge candidate diagnostics. Shuffled edges are synthetic
  offline controls only and never become pilot context.
- `solver.json`, `records.json`, `batch.log`, `status.json`: model identity and progress.
- `summary.json`: official paired outcomes, protocol exits, usage and elapsed time.

Record retrieval relevance judgments separately from annotation labels; this dataset
does not supply gold relevant memories for new issues. A useful graph must improve
over the strengthened flat baseline and ideally the fixed checklist. More reachable
nodes alone does not establish usefulness. Ten paired problems with one attempt per
condition provide a diagnostic pilot, not a statistically reliable general advantage.

All source attempts are failures from Qwen2.5-Coder 7B; the target solver is GPT-OSS
20B. This tests transfer between models and issues using a failure-only archive.
The archive supplies no matched successful trajectories, so its annotations cannot
establish which historical behaviors distinguish failure from success.

Design references: [retrieve and rerank](https://sbert.net/examples/sentence_transformer/applications/retrieve_rerank/README.html),
[Qwen embedding model](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B),
[Qwen reranker](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B),
[GraphRAG local search](https://microsoft.github.io/graphrag/query/local_search/),
[HippoRAG 2](https://arxiv.org/abs/2502.14802).
