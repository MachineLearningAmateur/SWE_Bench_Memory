# gpt-oss mini-SWE-agent tool-call repair

Status (2026-09-28): implemented, tested offline, and exercised once in a live development run, but the **plumbing gate still fails**. A refreshed Foundry deployment restored plain chat and two-turn native tool calls after an earlier HTTP 400 `BadRequestForDependentService` failure. The repaired agent made 29 valid shell calls, including one repaired `apply_patch` call, and reached the official verifier. It then returned text without a tool call three times, exited with `RepeatedFormatError`, and submitted no patch. A direct forced-`bash` request also returned no tool call. Do not run calibration or pilot trials. See `audit/gpt_oss_20b/repair_integration_gate.json` and `audit/gpt_oss_20b/repair_live_recheck.json`.

## Original extra-bracket repair rule

Use `src.harbor_mini_agent:RepairingMiniSweAgent` with `config/harbor_gpt_oss.yaml` and mini-SWE-agent 2.4.6 for any future gpt-oss run. The Harbor adapter installs `src/gpt_oss_tool_model.py` into the task container; the built-in mini-SWE-agent loop and official verifier remain unchanged. The model request uses native Chat Completions tools with a strict `bash(command: string)` schema, `tool_choice=required`, and `parallel_tool_calls=false`.

The extra-bracket response is repaired only when there is exactly one native `bash` function call, its arguments are invalid JSON ending in `"]}`, removing that one extra `]` produces valid JSON containing exactly one string `command`, and the command starts with `apply_patch <<'PATCH'` and ends with the matching `PATCH` delimiter. The model class then passes the corrected call to mini-SWE-agent's normal parser. Its trajectory response retains both original and corrected arguments under `agent_tool_repairs`. This original rule does not reinterpret plain text, invent a missing tool call, repair other JSON, or accept an unknown tool name. Other malformed responses still fail closed.

This rule was chosen using only the already-exposed development task. Offline replay repaired all eight malformed calls in each of the two saved development trajectories. Unit tests cover the observed typo, unchanged valid JSON, unrelated invalid JSON, and unknown tool names. This replay does not prove the resulting patch would pass the verifier. The first live recheck selected the repair model but failed at its first Foundry request with a separate upstream 400 error. After the deployment recovered, a second live recheck executed one repaired call but later failed because the model supplied no tool call. The repair deliberately does not invent a shell command from prose. No calibration or pilot outcomes have been seen.

## Development-only extension under the malformed-call approval

A later no-memory development trial produced the exact nonexistent tool name
`bash<|channel|>commentary` twice. The agent-side adapter now changes that
exact name to `bash` only for one native function call whose arguments are
valid JSON containing exactly one nonempty string `command`. It logs the
original name and arguments in `agent_tool_repairs`. It does not repair other
names, malformed JSON in the same call, multiple calls, or a response with no
tool call. The earlier extra-bracket rule remains separate and unchanged.
This extension passed container unit tests and was exercised once in a live
development trajectory. That trial had no further tool-format errors, but
reached the 60-step limit without submission. A separate development trial
still had three invalid-JSON calls outside either exact repair rule. Thus
the full plumbing/submission gate remains unpassed. No calibration or pilot
outcomes have been seen.
