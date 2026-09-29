# Post-hoc paired memory replay on three July calibration tasks

Completed 2026-09-28. This is a small follow-up to the stopped gpt-oss-20b
calibration, **not** the original 24-task three-arm pilot. The selection and
six new arm orders were committed in
`audit/gpt_oss_20b/posthoc_memory_replay_plan.json` before any memory-arm
model call. We selected the first frozen calibration task in each of Python,
TypeScript, and Go order, after all twelve no-memory outcomes were known.
This is explicitly post-hoc and illustrative, not a representative estimate.

For each selected problem, the existing one-attempt no-memory result was
paired with one new flat-RAG attempt and one new graph-v2 attempt. All arms
used the same Azure gpt-oss-20b deployment, mini-SWE-agent 2.4.6, 100-step
limit, narrow tool-call repair, issue prompt wrapper, and submit-required
scoring rule. No task/arm was retried, no text-only fallback was used, and
the model or retriever was not tuned during the replay. Flat and graph read
the same frozen historical SQLite corpus with the target repository excluded
and a 24,000-character memory budget. The no-memory result is a historical
paired baseline, not a concurrent rerun.

## Result

| Task | No memory | Flat RAG | Graph |
|---|---|---|---|
| `open-jarvis__OpenJarvis-465` (Python) | 0; format-error exit | 0; format-error exit | 0; format-error exit |
| `tashfeenahmed__freellmapi-289` (TypeScript) | 0; submit marker, empty patch | 0; format-error exit | 0; format-error exit |
| `ludo-technologies__pyscn-500` (Go) | 0; submit marker, empty patch | 0; 100-step limit | 0; 100-step limit |

Each entry is both the submit-required resolution score and Harbor's raw
official verifier reward: **0/3 for every arm under both measures**. Neither
memory method rescued a task in this set. All six new trials completed with
zero Harbor infrastructure exceptions or retries. Neither memory arm issued
a submit marker or produced a non-empty patch. The two no-memory submit
markers also had empty patches.

Across these three tasks, no memory used 94 model calls with 7 recorded
format-error feedback messages; flat RAG used 148 calls with 11 errors;
graph used 178 calls with 12 errors. Summed trial wall times were 483,
960, and 651 seconds, respectively. These are descriptive totals from just
three heterogeneous problems and must not be read as speed or error-rate
effects caused by memory. In particular, the memory arms spent more calls
on the Go task because both reached the step limit.

## Did the agents actually see different memory?

Yes. For each task, flat and graph shared the same BM25 seed chunk IDs but
had different final context SHA-256 hashes, and each used the full 24,000
character budget. Graph added four neighbors on each of the Python and
TypeScript tasks, all via `CITES_SOURCE_MESSAGE` provenance links. On the Go
task it added eight neighbors: five source citations and one each of
`HAS_END_STATE_ASSESSMENT`, `PROPOSES_CANDIDATE_LESSON`, and `QUALIFIED_BY`.
Thus the Go pair exercised substantive graph relations; the other two
mainly tested graph provenance expansion. Retrieval exposure was verified
before paid runs and is recorded by hash and relation count in the audit.

## Interpretation and limits

This replay gives a direct answer for the three selected problems: adding
flat RAG or graph memory to this unchanged agent did **not** produce an
officially passing or properly submitted solution. It does **not** establish
that memory is useless, that graph is equivalent to flat RAG, or that memory
cannot rescue other tasks. Selection occurred after seeing no-memory
outcomes, there is only one attempt per arm, two graph contexts mostly
expanded provenance rather than substantive relations, and the agent still
showed protocol failures. A different agent configuration or a larger
prospectively selected sample would be a new experiment.

The original calibration floor-stop remains in force; the 24-task pilot
partition was not consumed. The complete per-task outcomes, trial and
trajectory IDs/hashes, and retrieval-exposure metadata are in
`audit/gpt_oss_20b/posthoc_memory_replay_results.json`. The read-only report
can be regenerated from retained local Harbor jobs with
`python -m src.gpt_oss_exploratory_replay --report`. Raw trajectories and
retrieved text remain in ignored `results/` and are not pushed.
