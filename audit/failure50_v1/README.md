# Failure50 preparation record

Prepared on 2026-10-11. The experiment directory is
`results/failure50_verified10_v1`; the corpus is `memory/failure50_v1`.
These generated directories are local, ignored artifacts. The source archive is
the sibling `slm-failure-memory-pilot` checkout at commit
`267e00c456f273efb2b41f6d0717be1c74c4f941` and remains unchanged.

The importer verified 1,475 linked artifact hashes and produced 50 cases,
1,546 records, 1,622 chunks and 681 graph edges. There are 48 existing
annotations and two explicitly deferred cases. The new pipeline does not use
the historical TrajDebug corpus.

The regression suite passed: **102 tests**, including prepared-context delivery
to the solver without accessing the legacy corpus. All ten official task images
were cached and passed repository availability checks without network access.
Image identities and the retrieval environment are recorded in the local
experiment directory.

`preparation.json` records the independent checks of all 150 context bundles
(20 development queries and 10 pilot queries, each with five conditions),
including pinned-tokenizer budgets, source offsets, graph bounds, disjoint task
IDs, and frozen hashes. Counts of selected graph evidence describe exposure;
they do not establish relevance or better solver outcomes.

The source-citation diagnostic uses annotation descriptions to retrieve their
own cited evidence while annotations remain searchable. Its mean candidate
recall was BM25 0.303753, dense 0.236078, fusion 0.327249 and graph 0.910878.
This is not independent relevance evaluation on unseen problems. No claim of
graph superiority follows from it.

At preparation time, no scored attempts had started: endpoint preflights returned
HTTP 404 and the user deferred execution until the deployment was available.

The user confirmed availability later on 2026-10-11. At 20:34 UTC the existing
solver adapter passed a structured tool call and tool-result round trip; the
frozen code, corpus and context hashes also validated. The pilot was launched,
then stopped during its first attempt at the user's explicit request to move to
another machine and reset completely. That attempt and its outputs were deleted.
The current state is **ready, 0/50 attempts**. `migration_reset.json` records this
reset. The earlier `preparation.json` remains the pre-launch validation snapshot.

See [the machine handoff](../../docs/machine_handoff.md) for restoring the prepared
corpus and context bundles, configuring credentials, and starting from zero.

The launch command for a prepared experiment is shown below:

```powershell
docker compose start research
docker compose exec -T research python -m src.failure_pilot run --output /workspace/results/failure50_verified10_v1
```

The command invokes official grading after all attempts finish. See the
[protocol](../../docs/failure_memory_v1.md) for recovery behavior and limitations.
