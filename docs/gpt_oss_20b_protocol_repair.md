# gpt-oss mini-SWE-agent tool-call repair

Status (2026-09-28): implemented and tested offline, **not yet live-verified**. The Foundry deployment currently returns HTTP 400 `BadRequestForDependentService` even for a plain Chat Completions prompt. Do not run calibration or pilot trials until a development-only Harbor run proves the repaired agent can inspect files, execute a patch, submit, save its trajectory, and reach the official verifier. See `audit/gpt_oss_20b/repair_integration_gate.json`.

## Frozen repair rule

Use `src.harbor_mini_agent:RepairingMiniSweAgent` with `config/harbor_gpt_oss.yaml` and mini-SWE-agent 2.4.6 for any future gpt-oss run. The Harbor adapter installs `src/gpt_oss_tool_model.py` into the task container; the built-in mini-SWE-agent loop and official verifier remain unchanged. The model request uses native Chat Completions tools with a strict `bash(command: string)` schema, `tool_choice=required`, and `parallel_tool_calls=false`.

The response is repaired only when there is exactly one native `bash` function call, its arguments are invalid JSON ending in `"]}`, removing that one extra `]` produces valid JSON containing exactly one string `command`, and the command starts with `apply_patch <<'PATCH'` and ends with the matching `PATCH` delimiter. The model class then passes the corrected call to mini-SWE-agent's normal parser. Its trajectory response retains both original and corrected arguments under `agent_tool_repairs`. It does not reinterpret plain text, invent a missing tool call, repair other JSON, or accept an unknown tool name. Other malformed responses still fail closed.

This rule was chosen using only the already-exposed development task. Offline replay repaired all eight malformed calls in each of the two saved development trajectories. Unit tests cover the observed typo, unchanged valid JSON, unrelated invalid JSON, and unknown tool names. This replay does not prove the resulting patch would pass the verifier. The live development recheck selected the repair model but failed at its first Foundry request with a separate upstream 400 error, before any repair event. No calibration or pilot outcomes have been seen.
