# Failure50 trace memory: Qwen3 32B through OpenRouter

Official resolved results: **No memory 2/10**; **Checklist 0/10**; **BM25 0/10**; **Hybrid RAG 0/10**; **Graph RAG 1/10**.

Fifty scored attempts cover the ten Failure50 pilot problems, one attempt per condition, in the frozen balanced trial order of `results/failure50_verified10_v1`. Each condition received the same frozen trace context bundles as the GPT-OSS Failure50 experiment; the bundles were copied and hash-checked, and the source experiment was not modified. All patches were graded by the official SWE-bench harness; empty, unsubmitted, and overflowed attempts count as unresolved. Grading reported no infrastructure failures and no errors. This is an exploratory comparison, not an estimate of a general memory advantage. The numeric record, including every trial's failure category, is [audit/qwen3_32b_openrouter/failure50_v3_results.json](../audit/qwen3_32b_openrouter/failure50_v3_results.json).

## Setup

| Setting | Value |
|---|---|
| Model | `qwen/qwen3-32b` through OpenRouter, pinned to DeepInfra (no fallbacks) |
| Context window | 40,960 tokens (DeepInfra; the only Qwen3 32B provider the account allows) |
| Sampling | temperature 0.6, top_p 0.95, top_k 20 (Qwen3 thinking-mode recommendation), max_tokens 8192 |
| Action format | mini-swe-agent text-based (one ```` ```mswea_bash_command ```` block per turn), `swebench_backticks.yaml` templates |
| Limits | 100 steps, 3600 s wall time, no cost limit |
| Context overflow | Ends the attempt unresolved; the batch continues |
| Versions | mini-swe-agent 2.4.6, swebench 5.0.2, litellm 1.102.1 |
| Run | `results/failure50_qwen3_32b_v3` (manifest hashes the solver code; all hashes match this commit) |

This protocol differs from the GPT-OSS 20B Failure50 protocol (Azure tool calls with `tool_choice=required`, temperature 0). Results from the two models are not directly comparable.

### Abandoned earlier runs

- `failure50_qwen3_32b_v1` (3 trials, temperature 0, tool calls): without `max_tokens`, OpenRouter requested output up to the full window and DeepInfra rejected the request. All three trials also looped at temperature 0 (one reasoning line repeated to 16,384 tokens; one command repeated 21 times).
- `failure50_qwen3_32b_v2` (2 trials, thinking-mode sampling, tool calls): DeepInfra does not enforce `tool_choice=required`, so Qwen3 often wrote commands as ```` ```bash ```` text with no tool call, which ended in `RepeatedFormatError`. v3 uses mini-swe-agent's text-based action format instead, its supported non-tool-call mode.

## Conditions and results

| Condition | Resolved / 10 | Nonempty submissions | Format errors | Context overflows | Model calls | Input tokens | Output tokens | Agent time (min) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| No memory | 2 | 5 | 11 | 0 | 139 | 1,287,515 | 189,242 | 113.9 |
| Checklist | 0 | 2 | 5 | 0 | 69 | 287,375 | 68,669 | 42.0 |
| BM25 | 0 | 5 | 16 | 1 | 167 | 1,758,147 | 164,504 | 97.1 |
| Hybrid RAG | 0 | 4 | 13 | 1 | 107 | 923,126 | 121,076 | 66.3 |
| Graph RAG | 1 | 2 | 17 | 0 | 127 | 1,056,674 | 178,006 | 99.8 |

Format errors are turns without exactly one valid action block, mostly responses cut off at the 8,192-token output limit; the agent recovered from all but two (see below).

## Paired task outcomes

Each cell is the official resolution score followed by the agent exit status (`BadRequestError` is a context overflow).

| Task | No memory | Checklist | BM25 | Hybrid RAG | Graph RAG |
|---|---|---|---|---|---|
| astropy__astropy-14096 | 0; Submitted | 0; Submitted | 0; Submitted | 0; Submitted | 0; RepeatedFormatError |
| django__django-12713 | 0; Submitted | 0; Submitted | 0; Submitted | 0; Submitted | 0; Submitted |
| matplotlib__matplotlib-22865 | 0; Submitted | 0; Submitted | 0; Submitted | 0; Submitted | 0; Submitted |
| psf__requests-1921 | 1; Submitted | 0; Submitted | 0; Submitted | 0; Submitted | 1; Submitted |
| pydata__xarray-6992 | 0; Submitted | 0; Submitted | 0; Submitted | 0; Submitted | 0; Submitted |
| pylint-dev__pylint-7277 | 1; Submitted | 0; Submitted | 0; BadRequestError | 0; BadRequestError | 0; Submitted |
| pytest-dev__pytest-5840 | 0; Submitted | 0; Submitted | 0; Submitted | 0; Submitted | 0; Submitted |
| scikit-learn__scikit-learn-13124 | 0; Submitted | 0; Submitted | 0; Submitted | 0; Submitted | 0; Submitted |
| sphinx-doc__sphinx-10323 | 0; Submitted | 0; Submitted | 0; Submitted | 0; Submitted | 0; Submitted |
| sympy__sympy-13031 | 0; TimeExceeded | 0; Submitted | 0; RepeatedFormatError | 0; Submitted | 0; Submitted |

The differences between conditions are zero to two problems from a single attempt each; at temperature 0.6 they are within run-to-run variation.

## Why the unresolved attempts failed

All 47 unresolved attempts were classified from their trajectories by `src.openrouter_failure_audit`.

| Category | Total | No memory | Checklist | BM25 | Hybrid RAG | Graph RAG |
|---|---:|---:|---:|---:|---:|---:|
| Edit changed nothing; empty patch submitted | 20 | 2 | 6 | 2 | 5 | 5 |
| Nonempty patch, wrong fix | 15 | 3 | 2 | 5 | 4 | 1 |
| Submitted without attempting an edit | 7 | 2 | 2 | 1 | 0 | 2 |
| Context overflow | 2 | 0 | 0 | 1 | 1 | 0 |
| Repeated format errors | 2 | 0 | 0 | 1 | 0 | 1 |
| Wall-time limit | 1 | 1 | 0 | 0 | 0 | 0 |
| **Unresolved** | **47** | **8** | **10** | **10** | **10** | **9** |

**Edits that changed nothing (20).** Qwen3 edits files only with one-line `sed -i` commands. In these attempts the `sed` address or pattern matched nothing, so `sed` exited 0 without changing the file; the model never inspected the result, wrote `git diff > patch.txt`, and submitted the empty file. Typical causes: wrong indentation in an anchored pattern (`/^    raise AttributeError/`), patterns containing `\n` (line-oriented `sed` cannot match across lines), quotes breaking the single-quoted script (`kwargs['widget']`), and edits to the wrong file (`django/forms/models.py` instead of `django/contrib/admin/options.py`). To rule out a missed diff (wrong pathspec, new untracked file), every one of these attempts was replayed command by command in a clean evaluation container with mini-swe-agent's environment (`--replay`). All 20 replays reproduced an empty `patch.txt` with no tracked changes; the only untracked files were the model's scratch test scripts. No fix was lost.

**Wrong patches (15).** Examples: `sed '600s/.*/…/'` overwrote two unrelated lines including a docstring (astropy, BM25); a global `s/\[1:-1\]/[:-1]/` also changed a second line (matplotlib, two conditions with the same patch); removing `normcase` or `.lower()` broke pytest so the harness collected no tests (pytest, three conditions); removing `sys.path.pop(0)` or only adding `import os` (pylint); a `{` inserted into Python source (sympy, Hybrid RAG). The BM25 xarray patch was closest to the reference change but did not pass the tests. The harness's "ambiguous failure" flags (`no_tests_collected`, `missing_module`) are on these patches and are consistent with patches that break imports, not with environment faults.

**No edit (7).** The model read code and submitted without any edit command.

**Stops (5).** Both context overflows are on pylint-dev__pylint-7277: BM25 issued 61 commands, 40 of them failing `sed` scripts, until the history exceeded 40,960 tokens; Hybrid RAG overflowed on the same task after 14 commands. Both `RepeatedFormatError` exits are responses repeatedly cut off at the 8,192-token output limit. The `TimeExceeded` attempt (sympy, no memory) spent its 3,600 s on 58 failing `sed` commands.

## Interpretation

The dominant failure is the model's editing loop, not missing knowledge: it edits with fragile one-line `sed` scripts, does not check the diff, and rarely runs a reproduction or test before submitting. These failures appear at similar rates in every condition, including no memory, so the trace memory had little opportunity to matter and this run cannot show whether it helps. The 40,960-token window also biases against the memory conditions, whose prompts are longer; both overflows were in memory conditions.

Options for a more informative comparison, each of which must be applied to all five conditions and changes the protocol relative to GPT-OSS:

1. Ask the agent to show `git diff` before submitting (prompt change).
2. Reject empty submissions (affects 27 of the 47 failures).
3. Use a model that edits reliably, or a larger-context provider (for example, enabling SiliconFlow's 131k-token Qwen3 32B endpoint in the OpenRouter account settings).
4. Repeat the 50 trials at least three times, as recommended for agentic evaluations with sampling (arXiv 2602.07150).

## Reproduction

```bash
python -m src.openrouter_failure50 prepare --output results/failure50_qwen3_32b_v3
python -m src.openrouter_failure50 run --output results/failure50_qwen3_32b_v3
python -m src.openrouter_failure50 grade --output results/failure50_qwen3_32b_v3
# Host, with Docker and the evaluation images:
python -m src.openrouter_failure_audit --run results/failure50_qwen3_32b_v3 \
    --output audit/qwen3_32b_openrouter/failure50_v3_results.json --replay
```

Raw trajectories and grading reports remain in the ignored `results/` directory.
