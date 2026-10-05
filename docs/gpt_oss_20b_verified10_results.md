# SWE-bench Verified: ten-task GPT-OSS 20B comparison

Official resolved results: **No memory 3/10**; **Flat RAG 1/10**; **Graph 1/10**.

Thirty scored attempts cover the same ten problems, one per condition. Infrastructure-interrupted attempts and diagnostic requests are recorded separately below. All submitted patches were processed by the official SWE-bench harness; empty, unsubmitted, or rejected patches count as unresolved. Grading completed without reported infrastructure errors. This is an exploratory comparison, not a reliable estimate of a general memory advantage.

## Conditions and results

| Condition | Resolved / 10 | Nonempty submissions | Patch rejections | Format-error exits | Limit exits | Model calls | Input tokens | Output tokens | Agent time (min) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| No memory | 3 | 6 | 0 | 3 | 1 | 595 | 13,508,103 | 196,127 | 54.9 |
| Flat RAG | 1 | 6 | 3 | 3 | 1 | 586 | 16,065,964 | 304,671 | 86.3 |
| Graph | 1 | 3 | 0 | 6 | 1 | 547 | 13,710,862 | 223,630 | 72.2 |

Agent time sums recorded per-attempt durations; it excludes retrieval, environment startup, grading, and the pause for infrastructure recovery. Timing is descriptive, not evidence that a memory method causes a speed difference. Dollar cost is unknown: this deployment has no usable LiteLLM price mapping.

## Paired task outcomes

Each cell is the official resolution score followed by the agent exit status.

| Task | No memory | Flat RAG | Graph |
|---|---|---|---|
| django__django-14672 | 1; Submitted | 0; Submitted | 0; RepeatedFormatError |
| sphinx-doc__sphinx-10449 | 0; Submitted | 0; LimitsExceeded | 0; RepeatedFormatError |
| django__django-11299 | 0; RepeatedFormatError | 0; Submitted | 0; RepeatedFormatError |
| django__django-14493 | 1; Submitted | 1; Submitted | 0; Submitted |
| django__django-11551 | 0; Submitted | 0; RepeatedFormatError | 0; Submitted |
| django__django-12143 | 1; Submitted | 0; Submitted | 1; Submitted |
| pydata__xarray-6938 | 0; Submitted | 0; RepeatedFormatError | 0; LimitsExceeded |
| django__django-15916 | 0; RepeatedFormatError | 0; RepeatedFormatError | 0; RepeatedFormatError |
| django__django-12193 | 0; LimitsExceeded | 0; Submitted | 0; RepeatedFormatError |
| pytest-dev__pytest-5262 | 0; RepeatedFormatError | 0; Submitted | 0; RepeatedFormatError |

## Paired differences

These counts compare outcomes on identical problems. They are descriptive and do not establish causation.

| Comparison (A versus B) | Only A resolved | Only B resolved | Both resolved | Neither resolved |
|---|---:|---:|---:|---:|
| Flat RAG versus No memory | 0 | 2 | 1 | 7 |
| Graph versus No memory | 0 | 2 | 1 | 7 |
| Graph versus Flat RAG | 1 | 1 | 0 | 8 |

## Frozen setup and retrieval exposure

- Dataset: `SWE-bench/SWE-bench_Verified`, test split; an exact local ten-task snapshot was used for both attempts and grading.
- Model: Azure GPT-OSS 20B (`openai/openai--gpt-oss-20b`), temperature 0, medium reasoning; mini-SWE-agent 2.4.6 and swebench 5.0.2.
- Agent: native bash tools with the existing narrowly specified tool-call repairs, 100 steps and a 3600-second agent wall-time limit; no model-failure retries or automatic patch recovery.
- Memory: static task-start retrieval from the same frozen corpus, current repository excluded, a 24,000-character budget for each memory arm, and the same issue wrapper with `[none]` for the baseline.
- Retrieval: BM25 seed pool 8; graph-v2 uses two hops, semantic-first traversal, at most eight neighbors, and the existing relation allowlist. Settings were not tuned on these outcomes.
- Selection: the existing deterministic seed-42 sampler, with seven Django problems and one each from Sphinx, Xarray, and pytest. The condition order was frozen before model calls and varies across tasks.

All ten flat/graph pairs have identical common seed IDs. 10/10 pairs have different final context hashes. No-memory contexts are empty and every memory context stays within budget. Graph added 55 neighbors across the ten attempts. Traversed relations: `CITES_SOURCE_MESSAGE` (43), `HAS_END_STATE_ASSESSMENT` (3), `PROPOSES_CANDIDATE_LESSON` (3), `QUALIFIED_BY` (3), `STATED_MOTIVATES` (3).

Usage coverage: every recorded model call has a saved response with token usage.

## Infrastructure recovery

The first launch stopped after one API request because the local cost calculator did not recognize the deployment. No task commands had executed. That launch is retained separately; the restart used `cost_tracking=ignore_errors` without changing model, prompts, retrieval, or limits. Its one extra pre-action request is excluded from the main 30-attempt usage totals; its token usage and billing are not recovered.

After 18 completed attempts, starting the Xarray container exceeded the 120-second Docker startup timeout while its image was uncached. No model call occurred for that interrupted start. The empty attempt directory and incident were archived, the remaining images were downloaded, and the batch resumed from the next untouched task/condition. All 18 completed records were verified and preserved, including model failures. The runner migration added resume and grading checks; the agent, model, and retrieval source hashes stayed fixed.

The initial grading guard stopped on `error_ids` for malformed or inapplicable patches. It was corrected to distinguish the harness's logged `Patch Apply Failed` errors from unexplained evaluation failures. Patch rejections remain unresolved, raw official error IDs are retained, and no agent attempt or submitted patch was changed. Grading resumed using the existing predictions.

The harness also reports ambiguous test-environment failure indicators: `{"flat_rag": {"pytest-dev__pytest-5262": "missing_module"}}`. These retain the official unresolved label; they are not claimed to be proven infrastructure failures or proof of patch correctness.

A later `graph` attempt on `pytest-dev__pytest-5262` was interrupted by the endpoint error `no healthy upstream`. Its original trajectory, retrieval, and run record were archived, and a small inference probe confirmed recovery before an explicitly documented infrastructure retry. The other 28 completed attempts were retained. This was an additional model-backed start, not a retry of an ordinary format-error or limit exit.

The interrupted attempt recorded 45 model calls, of which 44 have saved token usage: 762,241 input and 4,979 output tokens. These are excluded from the main table and preserved in the infrastructure audit. Failed transport retries may not have usage records. The recovery probe used 70 input and 24 output tokens, also outside the scored attempts.

## Interpretation and limitations

Flat RAG solved 0 problems that no memory missed; graph solved 0. Graph had 6 format-error exits, compared with 3 for no memory and 3 for flat RAG. Flat RAG also had 3 rejected submissions. Protocol completion and patch formatting are therefore material limitations in interpreting the memory comparison; these observations do not show that memory caused the failures.

The table answers which submitted patches passed for these ten problems under this specific agent configuration. It does not establish that a method is generally better. The sample is small and heavily weighted toward Django, there is only one scored attempt per task/condition, and protocol failures reduce the opportunity to evaluate patch quality. A submitted patch is not itself a solved task; only the official resolution labels determine the scores.

Three selected problems (`django__django-14672`, `sphinx-doc__sphinx-10449`, and `django__django-11299`) had already been used in GPT-5.1 smoke runs. This is therefore not an entirely fresh held-out sample. The Verified batch is separate from the stopped July SWE-rebench calibration and does not revise that earlier result.

## Artifacts and reproduction

The key-free machine-readable record is [verified10_results.json](../audit/gpt_oss_20b/verified10_results.json). It contains task/condition results, official reports, calls and tokens, exit statuses, retrieval exposure, artifact hashes, source hashes, and the infrastructure-recovery record. Raw prompts, patches, retrieved text, credentials, trajectories, and grader logs remain in ignored `results/`.

```bash
python -m src.verified_report \
  --input results/verified10_gptoss_20261004_r1 \
  --output audit/gpt_oss_20b/verified10_results.json \
  --analysis docs/gpt_oss_20b_verified10_results.md
```
