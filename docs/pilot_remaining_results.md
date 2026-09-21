# Remaining SWE memory smoke tasks — results

Two preselected smoke tasks (seed 42) run end-to-end across the three frozen conditions.
This is a plumbing/plumbing-scale smoke run, **not** the final pilot. **No scientific
conclusion is drawn here** — only a description of these two tasks.

- Tasks: `sphinx-doc__sphinx-10449`, `django__django-11299`
- Conditions: `no_memory`, `flat_rag`, `graph`
- Model: `openai/gpt-5.1-codex-mini` (Azure / Microsoft Foundry v1, LiteLLM Responses)
- Dataset: `SWE-bench/SWE-bench_Verified`, `max_workers=1`
- Retrieval frozen: character budget 24000, `flat_top_k 8`, `graph_seed_k 8`, `graph_hops 1`,
  `graph_max_neighbors 8`, allowlist + deterministic relation priority.
- Six new agent runs (the earlier `django__django-14672` was **not** rerun).

## Six-run table

| Task | Condition | Resolved | Elapsed (s) | Calls | Tokens | Cost (USD) | Patch len | Patch SHA256 |
|---|---|---|---|---|---|---|---|---|
| sphinx-doc__sphinx-10449 | no_memory | yes | 209.858 | 34 | 693,638 | 0.085958 | 2281 | `a317f753b3ac…` |
| sphinx-doc__sphinx-10449 | flat_rag | yes | 215.181 | 39 | 1,021,946 | 0.093285 | 1328 | `cdfa8de2713a…` |
| sphinx-doc__sphinx-10449 | graph | yes | 375.520 | 54 | 2,249,213 | 0.165539 | 2023 | `79e7f2497639…` |
| django__django-11299 | no_memory | yes | 88.824 | 30 | 287,650 | 0.034716 | 700 | `9287769ced09…` |
| django__django-11299 | flat_rag | yes | 65.073 | 20 | 315,569 | 0.026295 | 700 | `9287769ced09…` |
| django__django-11299 | graph | yes | 80.639 | 24 | 320,479 | 0.029958 | 700 | `9287769ced09…` |

All six `exit_status = Submitted`, no run errors. Tokens = sum of `usage.total_tokens`.
Full per-run fields (retrieval paths, seed IDs, etc.) are in `pilot_remaining_results.json`.

## Patch comparison

**sphinx-doc__sphinx-10449 — NOT byte-identical; same logical fix, three different implementations.**

- `no_memory` (2281 chars): in `merge_typehints`, copy the recorded annotations and drop
  `'return'` when `objtype == 'class'` before emitting fields.
- `flat_rag` (1328 chars): in `record_typehints`, do not record `'return'` when
  `objtype in ('class', 'exception')`; **also edits `tests/test_ext_autodoc_configs.py`** to
  remove the expected `Return type:` block.
- `graph` (2023 chars): thread `objtype` into `modify_field_list` and skip the `rtype` field for
  classes; also fixes the latent `annotation` → `annotations['return']` reference.

All three target the same behavior (suppress the class "Return type" field) and all three were
graded resolved. `flat_rag` is the only one that modified test files.

**django__django-11299 — byte-identical across all three conditions.**
All three submit the same change in `django/db/models/sql/query.py`:
`self._add_q(..., allow_joins, split_subq, simple_col=simple_col)`.

## Retrieval summary (per task)

Retrieval is produced once at task start; query = the task `problem_statement`.

### sphinx-doc__sphinx-10449
- **flat_rag**: context 24000 chars; common seeds
  `C012:message:86:call:0`, `C045:message:85`, `C069:message:39`, `C012:message:86`,
  `C045:message:84:call:0`; 3 extra BM25 chunks.
- **graph**: context 24000 chars; **identical common seeds**; relations followed
  `{CITES_SOURCE_MESSAGE: 1}`; neighbor `sem:C012:evidence:86`; neighbor chars 2000; no raw
  message inlined (`raw_cited_message_ids = []`); BM25 backfill 12400 chars.

### django__django-11299
- **flat_rag**: context 24000 chars; common seeds `C033:task`, `C033:message:1`,
  `sem:C033:task`, `C015:message:1`, `C039:message:1`; 3 extra BM25 chunks.
- **graph**: context 24000 chars; **identical common seeds**; relations followed
  `{ASSESSES_AGAINST: 1, CITES_SOURCE_MESSAGE: 3}`; neighbors `sem:C015:evidence:1`,
  `sem:C033:evidence:1`, `sem:C039:evidence:1`, `sem:C033:assessment`; neighbor chars 7925; no raw
  message inlined; BM25 backfill 6475 chars.

## Official grader summary

| Run ID | Submitted | Completed | Resolved | Unresolved | Infra failures | Empty patches | Errors |
|---|---|---|---|---|---|---|---|
| `smoke_remaining_no_memory` | 2 | 2 | 2 | 0 | 0 | 0 | 0 |
| `smoke_remaining_flat_rag` | 2 | 2 | 2 | 0 | 0 | 0 | 0 |
| `smoke_remaining_graph` | 2 | 2 | 2 | 0 | 0 | 0 | 0 |

All three run IDs resolved both `django__django-11299` and `sphinx-doc__sphinx-10449`. Reports:
`openai__gpt-5.1-codex-mini.smoke_remaining_{no_memory,flat_rag,graph}.json`.

## Status

All six runs resolved. **Do not draw a scientific conclusion from these two tasks.** See
`trajectory_audit_remaining.md` for observable behavior and `pilot_001_django_14672.md` for the
first smoke task.
