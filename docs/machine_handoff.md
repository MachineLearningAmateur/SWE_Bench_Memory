# Run the prepared experiment on another machine

The local pilot was stopped and completely reset at the user's request. Its
interrupted attempt and outputs were deleted. The transferred experiment starts
at **ready, 0/50 attempts**; do not use `resume` for this fresh run.

## Restore

Clone this repository and use Python 3.12 or newer:

```bash
git clone git@github.com:MachineLearningAmateur/SWE_Bench_Memory.git
cd SWE_Bench_Memory
python -m src.restore_transfer
```

The transfer archives are committed in `memory_packages/`. The restore command
checks the archive, every member, and the frozen code hashes before restoring the
corpus and prepared experiment. It refuses to overwrite differing local files.
The optional `--history` flag also restores `historical_experiments.zip`, with
the earlier corpus and experiment results. Credential values in historical
trajectories were redacted in these packaged copies; their original trajectory
hashes therefore do not apply to the redacted files. The exact affected paths
are listed in `audit/failure50_v1/transfer_manifest.json`.

Six previously tracked historical trajectories were sanitized in the current
checkout as well. Older Git commits are unchanged and may retain those old
credential values; rotate any that are still valid.

The new experiment's corpus and all 150 context bundles are unchanged. Its
20 development queries, 10 pilot tasks, five conditions, model revisions,
embeddings, and context hashes are already frozen. You do not need a local GPU,
the retrieval Python environment, or the source repository to execute these
prepared solver trials. Do not rerun `prepare` or `contexts` for this handoff.

## Configure and start

Install Docker with Linux containers on an x86-64 host. Create `.env` by copying
`.env.example` and supply your own credentials locally. Never commit `.env`.
For the deployment used during preparation, the non-secret settings are:

```dotenv
MODEL_PROFILE=gpt_oss_20b
GPT_OSS_DEPLOYMENT=openai--gpt-oss-20b
GPT_OSS_API_BASE=https://james-zhang-hub-01.services.ai.azure.com/openai/v1/
GPT_OSS_LITELLM_MODEL_NAME=openai/openai--gpt-oss-20b
GPT_OSS_REASONING_EFFORT_SUPPORTED=1
GPT_OSS_API_KEY=YOUR_KEY_HERE
```

Build the container with the captured solver dependency constraints, pull the
ten task images before running, and check the deployment outside scored trials:

```bash
docker compose up -d --build research
docker compose exec -T research python -m src.cache_transfer_images
docker compose exec -T research python -m results.failure50_verified10_v1.restart_preflight
```

Only launch after that preflight succeeds. To run independently of your terminal:

```bash
docker compose exec -d research python -m results.failure50_verified10_v1.launch
```

Progress is in `results/failure50_verified10_v1/status.json` and `batch.log`.
`runner_exit.json` appears when the launcher exits. The runner automatically
starts official grading after all 50 attempts; `summary.json` contains the paired
results once grading completes. Keep Docker, the host, and the endpoint running.

The host's Docker image cache and model weights are not stored in Git. The
captured image identities, Python environment, and package checksums document
the original setup. The sibling source archive can be cloned separately if
needed for inspecting raw evidence or rebuilding retrieval:

```bash
git clone --branch codex/pilot-v1 git@github.com:ieldante/slm-failure-memory-pilot.git ../slm-failure-memory-pilot
git -C ../slm-failure-memory-pilot checkout 267e00c456f273efb2b41f6d0717be1c74c4f941
```
