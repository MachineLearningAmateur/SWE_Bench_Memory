# SWE Memory Experiment — Docker + VS Code + Jupyter

## Current study: Failure50 with local hybrid RAG

The new study uses only the 50 traces in the sibling `slm-failure-memory-pilot`
repository, with 48 existing annotations. It adds local embeddings and reranking,
compares five conditions on ten fresh Verified problems, and preserves the prior
corpus and experiments as historical artifacts. See
[the implementation and run protocol](docs/failure_memory_v1.md).

For another machine, follow [the prepared-experiment handoff](docs/machine_handoff.md).
It restores the frozen inputs and starts at zero attempts without rebuilding retrieval.

Use `config/failure_memory.yaml` and `src.failure_pilot` for new runs. The legacy
notebook and three-condition instructions below reproduce the earlier study.

This is a starter for the controlled pilot:

1. **No memory**
2. **Flat RAG** — BM25 over the same-information corpus
3. **Graph retrieval** — the same BM25 seed ranking + bounded traversal of preserved relationships

The same mini-SWE-agent version, Azure deployment, task, Docker image, cost limit, and memory character budget are used across conditions.

## Why Docker

SWE-bench evaluation is containerized. Running this workspace as a VS Code Dev Container also avoids host-Python drift and Windows newline problems. The dev container mounts the host Docker socket; SWE task environments are therefore separate task containers created by Docker Desktop (Docker-outside-of-Docker), not nested Docker daemons.

## Prerequisites

- Docker Desktop (Windows: WSL2 backend enabled)
- VS Code
- VS Code **Dev Containers** extension
- For official SWE-bench evaluation, plan for substantial free disk space. The upstream guide recommends roughly 120 GB minimum and 16 GB+ RAM.

## First run

1. Unzip this project.
2. Copy `.env.example` to `.env` and fill in your Azure values.
3. Open the folder in VS Code.
4. Run **Dev Containers: Reopen in Container**.
5. Open `notebooks/00_setup_and_smoke.ipynb` and choose `Python (SWE Memory)`.
6. Run cells top to bottom.
7. Run `01_retrieval_check.ipynb` before spending money on agent runs.
8. Use `02_pilot.ipynb` for one task first, then expand.

## Azure model

The default GPT-5.1 model string is built as:

`azure/<AZURE_DEPLOYMENT>`

The default model class is `litellm_response`, suitable for the existing GPT-5-family deployment. `MODEL_PROFILE=gpt_oss_20b` selects a separate Chat Completions profile without changing the GPT-5.1 settings. See [gpt-oss setup](docs/gpt_oss_20b_setup.md) for the Foundry endpoint smoke, frozen July-2026 partitions, and experiment gates.

## GPT-OSS 20B / ten-task Verified batch

`src.verified_batch` runs the existing seed-42 selection of ten SWE-bench
Verified problems in `no_memory`, `flat_rag`, and `graph`, then grades all three
prediction files with the official evaluator. It fixes the GPT-OSS repair
adapter, 100-step/3600-second limits, and a common prompt wrapper across arms.
The current selection includes the three earlier GPT-5.1 smoke problems.

From the research container, use a **new** output directory:

```bash
python -m src.verified_batch --output results/NAME
python -m src.verified_batch --output results/NAME --run
```

The first command freezes the task snapshot, configuration, memory hash,
source hashes, and trial order without model calls. The second starts paid
attempts and official grading. Cache task Docker images before starting;
the agent's 120-second container-start timeout includes an uncached image pull.
`status.json`, `batch.log`, and `records.json` track progress. `summary.json`
is written only after grading completes without reported infrastructure errors.
Reported zero model cost is not a billing estimate for this deployment.

`--resume` accepts a stopped batch only and verifies the completed prefix
against individual run records. It never retries completed model failures or
overwrites an attempt directory. Inspect and preserve any interrupted attempt
before resuming; an empty directory from a failed environment start can be
archived after confirming that no model calls occurred. A documented orchestration
migration can be recorded under `recovery/`; agent, model, and retrieval source
hashes remain fixed.

After completion, export the shareable numeric audit (no patches, prompts,
credentials, or retrieved source text):

```bash
python -m src.verified_report --input results/NAME --output audit/NAME/results.json
```

The October comparison is recorded in
[the ten-task results and analysis](docs/gpt_oss_20b_verified10_results.md),
with its [numeric audit](audit/gpt_oss_20b/verified10_results.json).

## OpenRouter alternative (Qwen3 32B)

`src.openrouter_batch` runs the same ten-task comparison through OpenRouter
instead of Azure: same seed-42 tasks, trial order, agent overrides, and
graph_v2 memory settings as `src.verified_batch`. Only `OPENROUTER_API_KEY` is
needed; set it in `.env` or in the host environment (Compose passes it through).
The profile is `config/models/qwen3_32b_openrouter.yaml`; `OPENROUTER_MODEL`
selects another OpenRouter model id and `OPENROUTER_PROVIDERS` changes the
pinned provider order.

```bash
python -m src.openrouter_smoke
python -m src.openrouter_batch --output results/NAME
python -m src.openrouter_batch --output results/NAME --run
```

`src.openrouter_failure50` runs the Failure50 problems instead: the ten pilot
tasks and the frozen trace contexts from `results/failure50_verified10_v1`,
copied into a new output directory. The source experiment is only read.
`--conditions` selects a subset of `no_memory,checklist,bm25,hybrid_rag,graph_rag`
(default: all five, 50 trials, in the frozen balanced order). A context-window
overflow ends that attempt unresolved; other trial errors stop the run.

```bash
python -m src.openrouter_failure50 prepare --output results/failure50_qwen3_32b_v1
python -m src.openrouter_failure50 run --output results/failure50_qwen3_32b_v1
```

The profile pins DeepInfra (40,960-token context), the only Qwen3 32B endpoint
the current account allows. DeepInfra does not enforce `tool_choice=required`;
it behaves as `auto`, so a turn without a bash call becomes a mini-SWE-agent
format error instead of a forced tool call. The smoke records this as
`tool_choice_required_enforced` in `audit/qwen3_32b_openrouter/smoke.json`.
`src.openrouter_failure50` therefore defaults to `--action-format text`
(mini-SWE-agent's text-based format, one fenced command block per turn);
`--action-format tool` keeps tool calls. The Azure GPT-OSS runs used enforced
tool calls at temperature 0, so report this difference when comparing results.

The October Qwen3 32B run is recorded in
[the Failure50 results and failure analysis](docs/qwen3_32b_failure50_results.md),
with its [per-trial audit](audit/qwen3_32b_openrouter/failure50_v3_results.json)
(`python -m src.openrouter_failure_audit`).

The OpenRouter code is in new files. `src/model_profiles.py`,
`src/experiment.py`, and the other files hashed by the prepared Failure50
experiment are unchanged.

## Important experimental rule

Do **not** tune graph retrieval after looking at test-set outcomes without applying the same predeclared tuning procedure to the flat arm. Use development tasks for retrieval tuning, then freeze settings.

The first pilot intentionally uses static memory retrieval once at task start. Dynamic mid-trajectory retrieval is a later experiment because it introduces another variable.

## Files

- `notebooks/00_setup_and_smoke.ipynb`: environment, Azure, Docker and memory checks
- `notebooks/01_retrieval_check.ipynb`: compare no-memory/flat/graph contexts without paying for coding runs
- `notebooks/02_pilot.ipynb`: select held-out tasks and run conditions
- `src/memory_conditions.py`: same-corpus flat vs graph retrievers
- `src/experiment.py`: mini-SWE-agent runner
- `src/evaluate.py`: official SWE-bench evaluation command wrapper
- `config/experiment.yaml`: all important experiment knobs
- `memory_packages/trajdebug_flat_rag_86_COMPLETE.zip`: populated identical-information memory corpus (**not committed to Git**; see *Memory corpus* below)

## Memory corpus

The memory corpus is **not committed to this repository**:
`memory_packages/trajdebug_flat_rag_86_COMPLETE.zip` and the extracted
`memory/trajdebug_flat_rag_86/` database are both gitignored. Provide the corpus locally in
either of these two ways before running the notebooks:

- place the ZIP at `memory_packages/trajdebug_flat_rag_86_COMPLETE.zip` (it is auto-extracted on
  first use), or
- keep the already-extracted `memory/trajdebug_flat_rag_86/` directory locally (the code uses it
  directly).

Do not commit the memory ZIP or the extracted memory database.

## Reproducibility notes

Pinned here:
- mini-SWE-agent 2.4.6
- swebench 5.0.2
- Python 3.11 container

Before a paper-scale run, record Docker Desktop/Engine version, Azure deployment/model version, dataset revision, task IDs, config hash, and all retrieval logs.
