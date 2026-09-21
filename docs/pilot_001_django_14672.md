# Pilot 001 — django__django-14672

## Purpose

This was the first end-to-end smoke test of the SWE memory harness. It validated, on a single
instance, that the full pipeline works:

- Azure model routing (`openai/<deployment>` via LiteLLM Responses → Azure / Microsoft Foundry v1)
- mini-SWE-agent 2.4.6 (SWE-bench environment)
- Docker SWE task environment
- the `no_memory`, `flat_rag`, and `graph` retrieval conditions
- patch export to `results/predictions_*.jsonl`
- official SWE-bench grading (`swebench==5.0.2`)

This is a **plumbing / smoke validation**, not part of the final 20-task pilot, and it is **not**
evidence of a graph-memory advantage. All numbers below were re-derived directly from the local run
artifacts (`results/*/django__django-14672/{run,trajectory,retrieval}.json`,
`results/predictions_*.jsonl`, `openai__gpt-5.1-codex-mini.pilot_*.json`, `logs/run_evaluation/`).

## Frozen setup

| Field | Value |
|---|---|
| Task ID | `django__django-14672` |
| Repository | `django/django` |
| Conditions | `no_memory`, `flat_rag`, `graph` |
| Model | `openai/gpt-5.1-codex-mini` (Azure / Microsoft Foundry v1, LiteLLM Responses, `model_class=litellm_response`) |
| Dataset (grading) | `SWE-bench/SWE-bench_Verified`, split `test` |
| Experiment Git commit (agent run) | `29bbf6f` ("Fix Azure v1 LiteLLM routing") |
| Character budget | `24000` (`memory.character_budget`) |
| Flat candidate pool | `flat_top_k: 8` |
| Graph seed pool | `graph_seed_k: 8` |
| Graph hops | `graph_hops: 1` |
| Graph neighbor limit | `graph_max_neighbors: 8` |
| Seed budget / caps (code defaults) | `seed_fraction=0.40`, `per_seed_chars=2000`, `per_neighbor_chars=2000` |
| Retrieval timing | Static: retrieval is performed **once at task start**, before the agent runs (`src/experiment.py::run_one` → `_memory_for` → `augment_problem`). No mid-trajectory retrieval. |

**Allowed-relation policy.** `config/experiment.yaml` defines an explicit 35-relation allowlist
(`CITES_SOURCE_MESSAGE`, `QUALIFIED_BY`, `HAS_END_STATE_ASSESSMENT`, `STATED_MOTIVATES`,
`ERROR_PRECEDES_REPAIR_ATTEMPT`, `CONTRADICTS_COMPLETION_CLAIM`, …). Chronological/structural base
links (`NEXT_MESSAGE`, `HAS_MESSAGE`, `MAY_RESPOND_TO`, `USES_TOOL_NAME`, …) are **not** followed.
The allowlist is a hard filter; a deterministic priority order only orders edges that already pass
it (repair/recovery → contradiction/counterevidence → `CITES_SOURCE_MESSAGE` → rationale →
observed effect/support → end-state → caveat/validation → lessons → review scaffolding). Semantic
`source_ref.base_graph_node_id` pointers are materialized as explicit `CITES_SOURCE_MESSAGE`
provenance edges so base messages can connect to semantic evidence; no inferred causal edges are
added.

Run timestamp / commit note: the three runs completed 2026-09-21 ~05:00–05:02, i.e. after `29bbf6f`
(04:53) and before `71172e0` (05:16). See *Discrepancies* for the dataset-name timing.

## Official outcome

| Condition | Resolved | Patch SHA256 | Elapsed (s) | API calls | Tokens (total) | Cost (USD) |
|---|---|---|---|---|---|---|
| no_memory | yes | `aad0cec9c1f4…` | 50.521 | 17 | 96,028 | 0.01195875 |
| flat_rag | yes | `aad0cec9c1f4…` | 57.442 | 22 | 284,684 | 0.02097115 |
| graph | yes | `aad0cec9c1f4…` | 37.859 | 12 | 147,709 | 0.01181425 |

- All three official grader reports resolve the instance: `resolved_ids: ["django__django-14672"]`,
  `unresolved_ids: []`, `empty_patch_instances: 0`, `error_instances: 0`.
- Per-run grader logs exist for all three run IDs: `logs/run_evaluation/pilot_{no_memory,flat_rag,graph}/`.
- Token totals are the sum of `usage.total_tokens` over assistant responses
  (input/output split — no_memory 92,051/3,977; flat_rag 281,163/3,521; graph 145,849/1,860).
- Elapsed is `run.json::elapsed_seconds` (wall time for the agent run). Cost is
  `trajectory.json::info.model_stats.instance_cost`.

**Patch equality:** the three submitted patches are **byte-identical**. Patch length = 512
characters; full SHA256 = `aad0cec9c1f4922a85269393ffbbde81c84a4abfba5c6da5a57c305998704980` for
all three. `results/predictions_{no_memory,flat_rag,graph}.jsonl` are also byte-identical
(SHA256 `9f6a678c15a74038e4cc9911e566c594edf435c4387f7009417e2f0e6b5fd1a8`).

## Patch comparison

All three conditions produced the same one-line change in
`django/db/models/fields/reverse_related.py`:

```diff
@@ -310,7 +310,7 @@ class ManyToManyRel(ForeignObjectRel):
     def identity(self):
         return super().identity + (
             self.through,
-            self.through_fields,
+            make_hashable(self.through_fields),
             self.db_constraint,
         )
```

No other files were changed in any condition.

## Retrieval audit

The retrieval query is identical in all three conditions and is the issue text itself, which begins:

> `Missing call `make_hashable` on `through_fields` in `ManyToManyRel``

**no_memory:** context is empty (0 characters); `metadata.retrieval = "none"`.

**flat_rag:** total retrieved context = 24,000 characters.
- Common seeds (BM25): `base|node|C033:message:1#1`, `C034:message:1#0`, `C063:message:1#0`,
  `C074:message:1#0`, `C080:message:1#1` — all `:message:1` chunks (first task/user messages from
  other trajectories).
- Extra BM25 chunks: `C032:message:1#1`, `C057:message:1#1`, `C058:message:1#1`, `C035:message:1#0`.
- Relevance: **not visibly task-specific.** None of `make_hashable`, `through_fields`, or
  `ManyToMany` appears anywhere in the 24,000-character context.

**graph:** total retrieved context = 24,000 characters.
- Common seed IDs are **identical to flat_rag** (verified equal).
- Relations followed: `CITES_SOURCE_MESSAGE` × 5.
- Graph neighbors (all reverse traversal from the base-message seeds through materialized
  provenance edges): `sem:C033:evidence:1`, `sem:C034:evidence:1`, `sem:C063:evidence:1`,
  `sem:C074:evidence:1`, `sem:C080:evidence:1`; neighbor chars = 10,000.
- Raw cited messages inlined: **0** (`graph_raw_messages_inlined = 0`); the dereference target for
  each provenance edge was the seed message itself, already rendered, so no raw message was inlined.
- BM25 backfill after traversal: 2 chunks (`C032:message:1#1`, `C057:message:1#1`), 4,400 chars.
- Relevance: none of `make_hashable`, `through_fields`, or `ManyToMany` appears in the context.

**Graph traversal occurred** (5 provenance edges were followed and 2 BM25 backfill chunks were
added), but **there is no evidence that the graph evidence helped solve the task**: the retrieved
memory was not task-specific, and the no-memory arm produced the identical patch with no memory at
all.

## Trajectory comparison

All three trajectories (`mini-swe-agent-1.1`) followed essentially the same path:

| | no_memory | flat_rag | graph |
|---|---|---|---|
| API calls | 17 | 22 | 12 |
| File inspections | `reverse_related.py` (rg/grep/sed) | same | same |
| Edit | one-line `make_hashable(...)` | same | same |
| Tests / official tests run | none | none (only a `make_hashable()` import snippet) | none |
| Submission | `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat patch.txt` | same | same |

- Each agent located `ManyToManyRel` (and `ForeignObjectRel.identity`), inspected the file, and
  applied the same one-line edit via `apply_patch` (with a Python text-replacement fallback in
  no_memory/flat_rag).
- **None of the three ran the project test suite or the official SWE-bench tests.** flat_rag ran a
  one-off snippet (`make_hashable(None)`, `make_hashable([1, 2])`) as a sanity check, but no test
  suite. This matches the initial review: agents inspected the code, made the obvious edit, and
  submitted.
- The trajectories did not materially diverge in approach. Differences in call count (graph 12 vs
  no_memory 17 vs flat_rag 22) are descriptive; flat_rag's extra calls were spent on patch-application
  retries (Python text replacement), not on different reasoning. Do not infer causality from the
  model mentioning memory.

## Interpretation

- All three conditions resolved the task.
- **No correctness benefit from memory was observed on this task.**
- The identical patch suggests the current task specification was sufficient for the model.
- Retrieved historical memory was not obviously task-specific (none of the task's keywords appeared
  in any retrieved context).
- Differences in runtime/token usage are **descriptive only** and cannot support an efficiency
  claim at N=1.
- This task may be too direct/easy to discriminate memory conditions: the problem statement itself
  names the missing `make_hashable` call on `through_fields` in `ManyToManyRel`.
- The run is therefore useful mainly as a successful end-to-end plumbing validation and as a
  possible ceiling-effect example.

## Next step

Next two **preselected** smoke tasks from seed 42 (independently reproduced from
`src/experiment.py::load_instances` with `seed: 42`; selection order is
`django__django-14672`, `sphinx-doc__sphinx-10449`, `django__django-11299`):

1. `sphinx-doc__sphinx-10449`
2. `django__django-11299`

Do **not** replace them based on Pilot 001's result. Run the same three conditions on both before
changing retrieval or task-selection policy.

**Retrieval is frozen. Do not tune graph or flat retrieval based on these smoke-test outcomes.**

## Discrepancies vs. the handoff / verification notes

1. **Dataset-name timing.** The agent runs completed ~05:00–05:02 on 2026-09-21, before commit
   `71172e0` (05:16) that renamed the dataset. At run time `config/experiment.yaml` still used the
   pre-rename alias `princeton-nlp/SWE-bench_Verified`, which resolves to the same Verified split.
   `SWE-bench/SWE-bench_Verified` is the name now recorded for grading. The selected instance is the
   same either way.
2. **Grader reports are byte-identical.** All three `openai__gpt-5.1-codex-mini.pilot_*.json` files
   share SHA256 `7d8369236a667e6d7d18b2ec2b37d081c01a6d453c42c201273b2d1efd61a56f`; the report content
   does not encode the run ID. Per-condition resolution is established by the filenames plus the
   per-run logs under `logs/run_evaluation/pilot_{no_memory,flat_rag,graph}/`, not by differing
   report content.
3. **"Graph surfaced connected historical evidence."** Traversal did occur (5
   `CITES_SOURCE_MESSAGE` provenance neighbors), but `graph_raw_messages_inlined = 0` because each
   dereferenced raw message was the seed message itself. None of the retrieved content was
   task-specific, so this must not be read as "graph evidence helped".
4. **"Same 512-character patch."** Confirmed exactly: patch length 512, identical SHA256 across all
   three conditions.
5. **All handoff metrics matched** the artifacts exactly (elapsed, API calls, tokens, cost).
6. Grader bookkeeping (`total_instances: 500`,
   `unremoved_images: ["swebench/sweb.eval.x86_64.django_1776_django-14672:latest"]`) is a harness
   artifact, not a resolution discrepancy.
7. Root-level run artifacts (`openai__gpt-5.1-codex-mini.pilot_*.json`,
   `pilot_django_14672_outputs.zip`) are untracked and intentionally not committed.

