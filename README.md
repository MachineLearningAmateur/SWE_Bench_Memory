# SWE Memory Experiment — Docker + VS Code + Jupyter

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

The model string is built as:

`azure/<AZURE_DEPLOYMENT>`

The default model class is `litellm_response`, suitable for GPT-5-family deployments using LiteLLM's Responses API. If your Azure deployment does not support that path, set `model_class: litellm` in `config/experiment.yaml`.

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
- `memory_packages/trajdebug_flat_rag_86_COMPLETE.zip`: populated identical-information memory corpus (**included in the FULL bundle; add it manually when using CODE_ONLY**)

## Reproducibility notes

Pinned here:
- mini-SWE-agent 2.4.6
- swebench 5.0.2
- Python 3.11 container

Before a paper-scale run, record Docker Desktop/Engine version, Azure deployment/model version, dataset revision, task IDs, config hash, and all retrieval logs.
