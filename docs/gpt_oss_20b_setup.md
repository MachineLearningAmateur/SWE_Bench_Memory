# gpt-oss-20b setup and experiment gates

Status (2026-09-28): Foundry plain chat and native tool calls work on the refreshed gpt-oss deployment. Harbor resolves the pinned 111-task dataset. Two earlier no-memory development-task runs reached the verifier but exited with `RepeatedFormatError` and submitted no patch; see `audit/gpt_oss_20b/plumbing_smoke.json` and `audit/gpt_oss_20b/strict_tool_recheck.json`. The user approved a narrow agent-side repair on 2026-09-28; its exact rule and evidence are in `docs/gpt_oss_20b_protocol_repair.md`. After a transient HTTP 400 upstream failure (`audit/gpt_oss_20b/repair_integration_gate.json`), a live development-only run exercised one repaired tool call and reached the verifier but ended with three text-only responses, `RepeatedFormatError`, and no submission; see `audit/gpt_oss_20b/repair_live_recheck.json`. No calibration or pilot tasks have run. Stop before calibration until a development-only Harbor run passes the plumbing gate.

## Run environment

Use the existing `research` container at `/workspace` for repo tests, retrieval, and LiteLLM checks. It can reach the host Docker daemon through the mounted socket. On this Windows Docker Desktop setup, run Harbor itself from Windows PowerShell in an isolated Python 3.12 environment: Docker Desktop cannot resolve Harbor's container-local `/workspace/...` bind-mount sources against the Windows host path. Harbor still starts isolated Linux task containers on the same host daemon. Do not start another daemon or run Compose inside `research`. Recreate the research container only from Windows PowerShell.

Before any model execution:

```bash
pwd
git status
git rev-parse HEAD
python --version
docker ps
python -m pytest -q
```

The Python tests must pass. The pinned source is `ibragim-badertdinov/swe-rebench-07-2026@1`, with 111 tasks and task metadata SHA256 `e18fbd54e334d914ebe2981710ce0f5b9c3b9f169823560aced50a5443eb3a23`. The Windows Harbor runner needs the repo root on `PYTHONPATH` to import `src.harbor_mini_agent:RepairingMiniSweAgent`. This adapter preserves Harbor's normal agent execution and official verifier while supplying the frozen, auditable repair described above. `config/harbor_gpt_oss.yaml` fixes the 60-step Chat Completions profile for any future arms.

## Foundry deployment

The user is setting up the deployment in their Foundry project. Follow [Microsoft's managed-compute deployment guide](https://learn.microsoft.com/en-us/azure/foundry/how-to/deploy-models-managed): sign in to Foundry (new), select the intended subscription and resource, open **Build → Models**, find `openai/gpt-oss-20b` in the Hugging Face collection, and select **Deploy** on its model card. The model card version from the handoff is `6`; verify that the wizard is still showing that version before deployment.

In the wizard, inspect the compatible deployment templates. Record the exact model ID, deployment-template ID, serving runtime, accelerator family and count, context limit, and displayed hourly rate. The portal, quota, and price for this project are not accessible from this checkout, so do not guess a template. For the first smoke use a template that explicitly supports Chat Completions and native function/tool calls, with enough context for the task plus up to 24,000 characters of memory. Set **Model instances** to `1`; note the chosen deployment name and cost estimate before selecting **Deploy**. Managed compute is dedicated accelerator capacity billed by accelerator-hours while provisioned, including idle time. If quota is unavailable, request managed-compute quota for the chosen accelerator family through **Manage → Quota → Managed compute**. Do not substitute Azure VM quota.

After provisioning reaches **Succeeded**, open the deployment's **Consume / sample code**. Record its exact endpoint, route type, deployment name, and required API version, if any. The [Microsoft Managed Compute endpoint guide](https://learn.microsoft.com/en-us/azure/foundry/concepts/managed-compute-overview) documents both `/openai/v1/` and `/managed-deployments/<deployment>/` routes. The latter is available for managed deployments; the former requires an OpenAI-compatible runtime. Do not infer compatibility from the catalog entry alone.

Keep the existing `AZURE_*` values for GPT-5.1. Add separate `GPT_OSS_DEPLOYMENT`, `GPT_OSS_API_BASE`, and `GPT_OSS_API_KEY` in the ignored `.env`. If the sample gives a full Chat Completions URL, set `GPT_OSS_CHAT_COMPLETIONS_URL`; set `GPT_OSS_API_VERSION` if the sample requires it. Never commit `.env` or keys.

Run the direct smoke:

```bash
python -m src.gpt_oss_smoke
```

It checks the exact reply, probes `reasoning_effort: medium`, and requires a structured `get_magic_number` tool call, client tool result, and final answer. Its nonsecret record is `audit/gpt_oss_20b/endpoint_smoke.json`. Stop if native tool calling fails. Do not switch agent protocol to text parsing.

Set `GPT_OSS_REASONING_EFFORT_SUPPORTED=1` only if the probe succeeds. Set `GPT_OSS_LITELLM_MODEL_NAME` to the candidate provider/model name indicated by the deployed runtime, then verify it in the container with `python -m src.gpt_oss_litellm_smoke`. Its successful nonsecret record is `audit/gpt_oss_20b/litellm_smoke.json`. Select `MODEL_PROFILE=gpt_oss_20b`. The profile uses `litellm` Chat Completions and a per-run 60-step / 3600-second limit; global mini-SWE-agent counters are not the experimental guardrail. Keep the profile and its reasoning settings fixed for all calibration/pilot trials. The existing GPT-5.1 profile remains the default.

## Frozen partition

The partition files in `audit/gpt_oss_20b/` were generated from the pinned local task snapshot before any gpt-oss outcomes. Validate without writing:

```bash
python -m src.gpt_oss_partitions --check
```

Calibration has 4 Python, 4 TypeScript, 4 Go tasks. Pilot has 8 of each. The 12 remaining eligible tasks are reserve. There is no calibration/pilot repository overlap. The Go pilot contains one repeated repository because the eligible Go set has only 11 repositories for 12 slots. The manifest records the deterministic seed, source hash, exclusions, language/repository counts, and balanced pilot condition order.

Use `Soju06__codex-lb-744` from the earlier retrieval-development set for the one-task no-memory plumbing smoke. It is not in calibration or pilot. Before trials, confirm Harbor resolves the pinned July dataset and that solution/verifier files are not visible to the agent. Use the official Harbor reward with one isolated Docker task container per trial. [Harbor's evaluation guide](https://www.harborframework.com/docs/run-jobs/run-evals) describes trial rewards and artifacts; [its task guide](https://www.harborframework.com/docs/tasks) explains verifier separation.

## Gates

Run exactly one no-memory attempt on each of the 12 calibration IDs and record official reward. If 0–2 or 10–12 resolve, stop. If 3–9 resolve, the model passes calibration. The post-development graph-v2 policy is recorded in `docs/graph_v2_exposure_audit.md` and must stay frozen for the exploratory pilot. Never tune retrieval on calibration or pilot outcomes.

Once all gates pass, run 24 tasks × 3 conditions, using the same agent, environment, limits, and static task-start memory retrieval. The condition order is predeclared in `partition_manifest.json`. Preserve each exact retrieval context, metadata, trajectory, official reward, calls, tokens, time, tool calls, and patch hash. Do not fabricate missing pricing or outcomes.

At the end, record whether Managed Compute remains active and its replica settings. Do not delete the deployment without authorization.
